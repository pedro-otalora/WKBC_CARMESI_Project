import json
import os
import re
import tempfile
import time
import unicodedata
from datetime import datetime, timezone

from wikibaseintegrator.datatypes import Item, URL, String
from wikibaseintegrator.wbi_enums import ActionIfExists

from config_carmesi import wbi, LOAD_FILES_PATH, EXTRACT_FILES_PATH
from progreso import Progreso

MAP_ONTOLOGIA = os.path.join(LOAD_FILES_PATH, 'mapa_ontologia_wikibase.json')
MAP_FILE = os.path.join(LOAD_FILES_PATH, 'mapa_cuadro_wikibase.json')
CUADRO_FILE = os.path.join(EXTRACT_FILES_PATH, 'cuadro_clasificacion_definitivo.json')

# Mapa de CONTROL compartido con inyectar_registros.py — mismo fichero, mismo
# namespace "entidades_compartidas". Aquí se reconcilian los productores de
# fondo/subfondo/serie contra los productores ya creados (o que se creen más
# tarde) a partir del campo ISAD 2.1 de los documentos, evitando duplicar la
# misma institución real como dos QID distintos. No importa qué script de
# los dos se ejecute primero: el fichero nace vacío ({"documentos": {},
# "entidades_compartidas": {}}) y lo puebla el que llegue antes.
MAPA_CONTROL_PATH = os.path.join(LOAD_FILES_PATH, 'mapa_items_inyectados_wikibase.json')

# Bandera de simulación: True para probar en consola, False para escribir en Wikibase
MODO_SIMULACION = False

# ==========================================
# 2. GESTIÓN DEL MAPA DE IDs Y ONTOLOGÍA
# ==========================================
def cargar_json_seguro(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def guardar_mapa_ids(mapa):
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=4)

# Cargamos estrictamente la ontología como fuente maestra de propiedades y clases
ontologia = cargar_json_seguro(MAP_ONTOLOGIA)

if not ontologia:
    raise SystemExit(
        f"[ERROR] No se encontró o está vacío el mapa de ontología: {MAP_ONTOLOGIA}\n"
        f"Ejecute primero inyectar_ontologia.py para crear las clases y propiedades base."
    )

def _get_ontologia(clave):
    """Acceso con mensaje de error claro si falta alguna clase/propiedad esperada,
    en vez de un KeyError críptico a mitad de la carga."""
    try:
        return ontologia[clave]
    except KeyError:
        raise SystemExit(
            f"[ERROR] Falta '{clave}' en {MAP_ONTOLOGIA}. "
            f"Compruebe que inyectar_ontologia.py se ejecutó con la tabla P/Q completa."
        )

# Cargamos el mapa local de este cuadro de clasificación (independiente del de la ontología)
mapa_ids = cargar_json_seguro(MAP_FILE)

# Asignación de propiedades y clases leídas EXCLUSIVAMENTE del mapa de ontología
P_INSTANCIA_DE = _get_ontologia("P_instancia_de")
P_INCLUIDO_EN = _get_ontologia("P_incluido_en")
P_TITULO = _get_ontologia("P_titulo")
P_URL_ACCESO = _get_ontologia("P_url_acceso")
P_ID_ORIGEN = _get_ontologia("P_id_origen")  # identificador del sistema de origen (archivo_id/js_id/serie_id)
P_TIPO_AGRUPACION = _get_ontologia("P_tipo_agrupacion_doc")
P_ALCANCE_CONTENIDO = _get_ontologia("P_alcance_contenido")  # RiC-A38, string
P_PRODUCTOR = _get_ontologia("P_productor")  # RiC-R027, wikibase-item

Q_AGRUPACION_DOC = _get_ontologia("Q_agrupacion_doc")
Q_AGRUPACION_FONDOS = _get_ontologia("Q_agrupacion_fondos")
Q_FONDO = _get_ontologia("Q_fondo")
Q_SUBFONDO = _get_ontologia("Q_subfondo")
Q_SERIE = _get_ontologia("Q_serie")
Q_INSTITUCION = _get_ontologia("Q_institucion")

def sanitizar_texto(texto):
    if not texto:
        return ""
    return str(texto).strip()

def sanitizar_url_carmesi(url_str):
    if not url_str:
        return None
    url_str = url_str.strip()
    if '?' in url_str:
        base, query = url_str.split('?', 1)
        params = query.split('&')
        params_filtrados = [p for p in params if not p.startswith('nombreSerie=')]
        return base + '?' + '&'.join(params_filtrados)
    return url_str

# ==========================================
# 2 bis. MAPA DE CONTROL COMPARTIDO (entidades_compartidas / productores)
# ==========================================
def _cargar_mapa_control():
    data = cargar_json_seguro(MAPA_CONTROL_PATH)
    if not data or "documentos" not in data:
        # Estructura idéntica a la que espera/produce inyectar_registros.py,
        # aunque este script solo escriba en "entidades_compartidas".
        data = data or {}
        data.setdefault("documentos", {})
        data.setdefault("entidades_compartidas", {})
    return data

mapa_control = _cargar_mapa_control()

def _volcar_mapa_control():
    """Escritura atómica: fichero temporal + rename, igual que en
    inyectar_registros.py. El volumen de escrituras aquí es bajo (un
    productor por fondo, no por documento), así que se vuelca en cada
    cambio sin necesidad de la política de guardado diferido del otro script."""
    directorio = os.path.dirname(MAPA_CONTROL_PATH)
    fd, ruta_tmp = tempfile.mkstemp(dir=directorio, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(mapa_control, f, ensure_ascii=False, indent=2)
        os.replace(ruta_tmp, MAPA_CONTROL_PATH)
    except Exception:
        if os.path.exists(ruta_tmp):
            os.remove(ruta_tmp)
        raise

def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def registrar_entidad_compartida(clave_mapa, qid, tipo, label_es, id_referencia):
    """Misma forma que la homónima de inyectar_registros.py, para que ambos
    scripts lean/escriban el mismo namespace sin conflicto de esquema."""
    entidades = mapa_control["entidades_compartidas"]
    entrada = entidades.get(clave_mapa)
    if entrada is None:
        entrada = {"qid": qid, "tipo": tipo, "label_es": label_es, "documentos_relacionados": []}
        entidades[clave_mapa] = entrada
    elif entrada.get("qid") != qid:
        # Reconciliación de un QID simulado obsoleto ("WB_PRODUCTOR_...") por
        # el QID real — ver obtener_o_crear_productor(). Sin este `elif` el
        # QID real recién obtenido nunca se persistía en
        # mapa_items_inyectados_wikibase.json cuando la clave YA existía (solo
        # se fijaba en la rama de creación de más arriba), así que cada
        # reejecución repetía indefinidamente el ciclo aviso -> conflicto ->
        # reutilización en memoria sin corregir nunca el fichero.
        entrada["qid"] = qid
    if id_referencia and id_referencia not in entrada["documentos_relacionados"]:
        entrada["documentos_relacionados"].append(id_referencia)
    entrada["actualizado_en"] = _ahora()
    _volcar_mapa_control()

def generar_clave_productor(nombre: str) -> str:
    """Idéntica a la de transformar_registros.py — misma normalización, mismo
    prefijo — para que "Archivo AMO" resuelva siempre a la misma clave
    ("PRODUCTOR_archivo_amo") se cree desde el cuadro o desde un documento."""
    if not nombre:
        return "PRODUCTOR_DESCONOCIDO"
    texto = unicodedata.normalize('NFKD', nombre).encode('ASCII', 'ignore').decode('utf-8')
    slug = re.sub(r'[^a-z0-9]+', '_', texto.lower()).strip('_')
    return f"PRODUCTOR_{slug}"

_RE_QID_REAL = re.compile(r"^Q\d+$")

def _escribir_con_reutilizacion(item, contexto: str) -> str:
    """Escribe un ítem; si Wikibase rechaza por conflicto de etiqueta+
    descripción duplicada (ya existe un ítem REAL con ese mismo label y esa
    misma descripción), reutiliza el QID que el propio mensaje de error
    indica en vez de fallar. Mismo patrón que _escribir_con_reutilizacion()
    de 040_inyectar_registros.py, necesario aquí por el mismo motivo exacto
    que allí se documenta: el mapa de control local puede desincronizarse
    del estado real de Wikibase (p. ej. una entrada registrada durante una
    pasada en MODO_SIMULACION, descartada por obsoleta en
    obtener_o_crear_productor() porque su QID no es real, cuando el
    productor YA se había creado de verdad en una ejecución real anterior
    cuyo QID real nunca llegó a persistirse en el mapa local). Sin esta red
    de seguridad, reintentar la creación choca con el ítem real ya existente
    y el productor queda sin crear/reconciliar en esta ejecución."""
    try:
        res = item.write()
        return res.id
    except Exception as e:
        error_str = str(e)
        match_q = re.search(r"(Q\d+)", error_str)
        if match_q and ("already has label" in error_str or "ModificationFailed" in error_str):
            qid = match_q.group(1)
            _progreso.aviso(f"   ↪ [Reutilizado por conflicto de etiqueta] {contexto} -> {qid} (revise el mapa de control)")
            return qid
        raise

def obtener_o_crear_productor(nombre_fondo):
    """Devuelve el QID del Q_institucion productor de un fondo, reutilizando
    cualquier entidad ya registrada bajo la misma clave — tanto si la creó
    antes este mismo script (otro fondo homónimo, poco probable) como si la
    creó inyectar_registros.py a partir del campo ISAD 2.1 de un documento.

    Una entrada registrada durante una pasada en MODO_SIMULACION guarda un
    QID falso ("WB_PRODUCTOR_..."), no un QID real de Wikibase. Si se
    reutilizara tal cual en una ejecución real, wikibaseintegrator rechaza
    la declaración con "Invalid item ID" al no cumplir el patrón Q[0-9]+.
    Se descarta esa entrada y se recrea el productor de verdad."""
    nombre_fondo = sanitizar_texto(nombre_fondo)
    if not nombre_fondo:
        return None

    clave = generar_clave_productor(nombre_fondo)

    if clave in mapa_control["entidades_compartidas"]:
        qid = mapa_control["entidades_compartidas"][clave]["qid"]
        if _RE_QID_REAL.match(qid):
            registrar_entidad_compartida(clave, qid, "productores", nombre_fondo, f"CUADRO:{clave}")
            _progreso.avanzar(f"[Productor reutilizado] '{nombre_fondo}' -> {qid}")
            return qid
        if not MODO_SIMULACION:
            _progreso.aviso(
                f"   ⚠️ Entrada simulada obsoleta para '{clave}' ({qid}) — se descarta y se crea el productor real"
            )
            # No se borra la entrada aquí: registrar_entidad_compartida() la
            # sobrescribe más abajo en cuanto se obtenga el QID real.

    if MODO_SIMULACION:
        qid_simulado = f"WB_{clave}"
        registrar_entidad_compartida(clave, qid_simulado, "productores", nombre_fondo, f"CUADRO:{clave}")
        _progreso.aviso(f"[{clave}] {nombre_fondo} — productor simulado, sin escribir en Wikibase")
        return qid_simulado

    try:
        item = wbi.item.new()
        item.labels.set('es', nombre_fondo[:250])
        item.descriptions.set(
            'es',
            f"Agente productor de la documentación ({nombre_fondo}) (rico:CorporateBody)"[:250]
        )
        item.claims.add([Item(prop_nr=P_INSTANCIA_DE, value=Q_INSTITUCION)], action_if_exists=ActionIfExists.APPEND_OR_REPLACE)
        item.claims.add([String(prop_nr=P_ID_ORIGEN, value=clave)], action_if_exists=ActionIfExists.APPEND_OR_REPLACE)
        qid = _escribir_con_reutilizacion(item, f"productor '{nombre_fondo}'")
    except Exception as e:
        _progreso.aviso(f"   ❌ [FALLO] productor '{nombre_fondo}': {e}")
        return None

    registrar_entidad_compartida(clave, qid, "productores", nombre_fondo, f"CUADRO:{clave}")
    _progreso.avanzar(f"[CREADO] productor: '{nombre_fondo}' -> {qid}")
    time.sleep(0.4)
    return qid

# ==========================================
# 3. CREACIÓN DE ITEMS EN WIKIBASE
# ==========================================
_progreso: Progreso = None  # se inicializa en __main__, tras precontar el árbol
_contadores = {"creados": 0, "omitidos": 0, "actualizados": 0, "fallidos": 0}

def _completar_item_existente(qid, id_local, titulo, alcance_contenido=None, q_productor=None):
    """Añade P_alcance_contenido/P_productor a un ítem YA existente si aún no
    las tiene. Necesario porque, a diferencia de una inyección normal, aquí
    estamos retrocompletando propiedades nuevas sobre nodos del cuadro creados
    en ejecuciones anteriores de este mismo script — el "ya existe, se omite"
    original no debe impedir esta actualización."""
    if not alcance_contenido and not q_productor:
        return

    if MODO_SIMULACION:
        _progreso.aviso(f"[{id_local}] {titulo} — actualización simulada (alcance/productor), sin escribir")
        return

    try:
        item = wbi.item.get(qid)
        claims_nuevas = []

        ya_tiene_alcance = bool(item.claims.get(P_ALCANCE_CONTENIDO))
        if alcance_contenido and not ya_tiene_alcance:
            claims_nuevas.append(String(prop_nr=P_ALCANCE_CONTENIDO, value=alcance_contenido))

        ya_tiene_productor = bool(item.claims.get(P_PRODUCTOR))
        if q_productor and not ya_tiene_productor:
            claims_nuevas.append(Item(prop_nr=P_PRODUCTOR, value=q_productor))

        if not claims_nuevas:
            return

        item.claims.add(claims_nuevas, action_if_exists=ActionIfExists.APPEND_OR_REPLACE)
        item.write()
        _progreso.avanzar(f"[ACTUALIZADO] {id_local} ({qid}): +{len(claims_nuevas)} propiedad(es)")
        _contadores["actualizados"] += 1
        time.sleep(0.4)
    except Exception as e:
        _progreso.aviso(f"   ⚠️ [FALLO ACTUALIZACIÓN] '{id_local}' ({qid}): {e}")

def crear_o_obtener_item(id_local, titulo, q_tipo_agrupacion, descripcion, id_origen=None, q_padre_wb=None,
                          url_acceso=None, nivel=0, metadata_extra=None,
                          alcance_contenido=None, q_productor=None):
    id_local = sanitizar_texto(id_local)
    titulo = sanitizar_texto(titulo)
    id_origen = sanitizar_texto(id_origen) if id_origen else None

    descripcion_base = sanitizar_texto(descripcion)
    if id_origen and f"ID: {id_origen}" not in descripcion_base:
        descripcion = f"{descripcion_base} (ID: {id_origen})"
    else:
        descripcion = descripcion_base

    # P_alcance_contenido reutiliza el mismo texto que la descripción del
    # ítem, tal y como se acordó — sin el sufijo "(ID: ...)" añadido arriba,
    # que es un artefacto de presentación en Wikibase, no parte del alcance.
    alcance_contenido = sanitizar_texto(alcance_contenido) or descripcion_base

    url_limpia = sanitizar_url_carmesi(url_acceso)
    q_clase_ontologica = Q_AGRUPACION_DOC

    # Comprobación de existencia en el mapa estructurado
    if id_local in mapa_ids:
        elemento_guardado = mapa_ids[id_local]
        # Compatibilidad por si hubiera algún registro antiguo guardado como string simple
        qid_existente = elemento_guardado.get("qid") if isinstance(elemento_guardado, dict) else elemento_guardado
        _progreso.avanzar(f"[OMITIDO] {id_local} ya existe como {qid_existente}")
        _contadores["omitidos"] += 1
        # El ítem ya existe, pero puede ser de una ejecución anterior a la
        # incorporación de alcance/productor — se retrocompleta si falta.
        _completar_item_existente(qid_existente, id_local, titulo, alcance_contenido, q_productor)
        # Retrocompleta también el JSON LOCAL (mapa_cuadro_wikibase.json) con
        # claves introducidas en una versión posterior del pipeline
        # ("q_productor", "alcance_contenido", "qid_padre" — ver metadata_extra
        # en procesar_elemento). El "ya existe" de arriba solo evita repetir la
        # creación del ítem en Wikibase; no debe impedir que el fichero local
        # que lee 080_exportar_rdf_ttl.py se ponga al día, porque la mayoría de
        # nodos fondo/subfondo/serie ya existían de ejecuciones anteriores a
        # estos campos y nunca pasan por la rama de creación de más abajo.
        if isinstance(elemento_guardado, dict) and metadata_extra:
            cambios = False
            for clave_meta, valor_meta in metadata_extra.items():
                if elemento_guardado.get(clave_meta) != valor_meta:
                    elemento_guardado[clave_meta] = valor_meta
                    cambios = True
            if cambios:
                guardar_mapa_ids(mapa_ids)
        return qid_existente

    if MODO_SIMULACION:
        _progreso.aviso(f"[{id_local}] {titulo} — simulado, sin escribir en Wikibase")
        qid_simulado = f"WB_{id_local}"
        item_dict = {"qid": qid_simulado}
        if metadata_extra:
            item_dict.update(metadata_extra)
        mapa_ids[id_local] = item_dict
        guardar_mapa_ids(mapa_ids)
        _progreso.avanzar(f"[SIMULADO] {id_local}")
        return qid_simulado

    _progreso.actualizar(f"[CREANDO] {id_local}: {titulo}")

    try:
        item = wbi.item.new()

        item.labels.set('es', titulo[:250])
        item.descriptions.set('es', descripcion[:250])

        item.claims.add([Item(prop_nr=P_INSTANCIA_DE, value=q_clase_ontologica)], action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

        if q_tipo_agrupacion:
            item.claims.add([Item(prop_nr=P_TIPO_AGRUPACION, value=q_tipo_agrupacion)], action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

        item.claims.add([String(prop_nr=P_TITULO, value=titulo)], action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

        if id_origen:
            item.claims.add([String(prop_nr=P_ID_ORIGEN, value=str(id_origen))], action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

        if q_padre_wb:
            item.claims.add([Item(prop_nr=P_INCLUIDO_EN, value=q_padre_wb)], action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

        if url_limpia:
            item.claims.add([URL(prop_nr=P_URL_ACCESO, value=url_limpia)], action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

        if alcance_contenido:
            item.claims.add([String(prop_nr=P_ALCANCE_CONTENIDO, value=alcance_contenido)], action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

        if q_productor:
            item.claims.add([Item(prop_nr=P_PRODUCTOR, value=q_productor)], action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

        res = item.write()
        qid = res.id
    except Exception as e:
        _progreso.aviso(f"   ❌ [FALLO] '{id_local}' ({titulo}): {e}")
        _progreso.avanzar()
        _contadores["fallidos"] += 1
        return None

    item_dict = {"qid": qid}
    if metadata_extra:
        item_dict.update(metadata_extra)

    mapa_ids[id_local] = item_dict
    guardar_mapa_ids(mapa_ids)
    _progreso.avanzar(f"[CREADO] {id_local} -> {qid}")
    _contadores["creados"] += 1
    time.sleep(0.4)
    return qid

# ==========================================
# 4. PROCESAMIENTO RECURSIVO UNIFICADO
# ==========================================
def contar_elementos(elem):
    """Precuenta cuántos nodos tiene el árbol (raíz + fondos + subfondos +
    series), para poder mostrar un progreso [i/total] real antes de empezar."""
    total = 1
    tipo = elem.get("tipo")
    if tipo == "raiz":
        for sub in elem.get("elementos", []):
            total += contar_elementos(sub)
    elif tipo == "archivo_o_fondo":
        for sub in elem.get("subfondos", []):
            total += contar_elementos(sub)
        for sub in elem.get("series", []):
            total += contar_elementos(sub)
    return total

def procesar_elemento(elem, q_padre_wb=None, qid_raiz=None, nombre_fondo=None, fondo_id_actual=None,
                       subfondo_id_actual=None, q_productor_fondo=None, nivel=0):
    tipo = elem.get("tipo")

    if tipo == "raiz":
        nombre_raiz = sanitizar_texto(elem.get("nombre", "Proyecto Carmesí"))
        descripcion = f"Agrupación de fondos documentales del {nombre_raiz}"

        # La raíz ("Proyecto Carmesí") no es un fondo real y no tiene
        # productor propio — deliberadamente sin P_productor/P_alcance_contenido.
        qid_raiz_actual = crear_o_obtener_item(
            id_local="AGRUPACION_CARMESI",
            titulo=nombre_raiz,
            q_tipo_agrupacion=Q_AGRUPACION_FONDOS,
            descripcion=descripcion,
            id_origen="CARMESI",
            q_padre_wb=None,
            nivel=nivel,
            metadata_extra={"titulo": nombre_raiz, "tipo": "raiz", "qid_padre": ""}
        )

        for sub_elem in elem.get("elementos", []):
            procesar_elemento(
                sub_elem,
                q_padre_wb=qid_raiz_actual,
                qid_raiz=qid_raiz_actual,
                nombre_fondo=None,
                fondo_id_actual=None,
                subfondo_id_actual=None,
                q_productor_fondo=None,
                nivel=nivel+1
            )

    elif tipo == "archivo_o_fondo":
        archivo_id = elem.get("archivo_id")
        js_id = elem.get("js_id")
        titulo = sanitizar_texto(elem.get("titulo"))

        es_subfondo = (q_padre_wb is not None and q_padre_wb != qid_raiz)

        if not es_subfondo:
            q_tipo_agrupacion = Q_FONDO
            if archivo_id:
                id_local = f"FONDO_{archivo_id}"
                id_origen = str(archivo_id)
                f_id = str(archivo_id)
            else:
                id_local = f"FONDO_JS_{js_id}"
                id_origen = None
                f_id = str(js_id)

            descripcion = f"Fondo documental del {titulo}"
            contexto_fondo = titulo

            metadata = {
                "fondo_id": f_id,
                "titulo": titulo,
                "tipo": "fondo"
            }
            nuevo_fondo_id = f_id
            nuevo_subfondo_id = None

            # Productor del fondo: se crea/reutiliza UNA vez aquí, a partir
            # del propio título del fondo, y se propaga sin recalcular a
            # todos sus subfondos y series descendientes (ver más abajo).
            q_productor_actual = obtener_o_crear_productor(titulo)

            # Persistidos en mapa_cuadro_wikibase.json para que
            # 080_exportar_rdf_ttl.py pueda reconstruir P_productor,
            # P_alcance_contenido y la jerarquía P_incluido_en sin consultar
            # Wikibase en caliente (ver diseño de ese script).
            metadata["qid_padre"] = q_padre_wb or ""
            metadata["q_productor"] = q_productor_actual or ""
            metadata["alcance_contenido"] = descripcion
        else:
            q_tipo_agrupacion = Q_SUBFONDO
            if archivo_id:
                id_local = f"SUBFONDO_{archivo_id}"
                id_origen = str(archivo_id)
                sf_id = str(archivo_id)
            else:
                id_local = f"SUBFONDO_JS_{js_id}"
                id_origen = None
                sf_id = str(js_id)

            padre_desc = nombre_fondo if nombre_fondo else "fondo principal"
            descripcion = f"Subfondo documental perteneciente al {padre_desc}"
            contexto_fondo = nombre_fondo if nombre_fondo else titulo

            metadata = {
                "fondo_id": str(fondo_id_actual),
                "subfondo_id": sf_id,
                "titulo": titulo,
                "tipo": "subfondo"
            }
            nuevo_fondo_id = fondo_id_actual
            nuevo_subfondo_id = sf_id

            # El subfondo NO crea productor propio: hereda el de su fondo.
            q_productor_actual = q_productor_fondo

            metadata["qid_padre"] = q_padre_wb or ""
            metadata["q_productor"] = q_productor_actual or ""
            metadata["alcance_contenido"] = descripcion

        qid_actual = crear_o_obtener_item(
            id_local=id_local,
            titulo=titulo,
            q_tipo_agrupacion=q_tipo_agrupacion,
            descripcion=descripcion,
            id_origen=id_origen,
            q_padre_wb=q_padre_wb,
            nivel=nivel,
            metadata_extra=metadata,
            alcance_contenido=descripcion,
            q_productor=q_productor_actual
        )

        for subfondo in elem.get("subfondos", []):
            procesar_elemento(
                subfondo,
                q_padre_wb=qid_actual,
                qid_raiz=qid_raiz,
                nombre_fondo=contexto_fondo,
                fondo_id_actual=nuevo_fondo_id,
                subfondo_id_actual=nuevo_subfondo_id,
                q_productor_fondo=q_productor_actual,
                nivel=nivel+1
            )

        for serie in elem.get("series", []):
            procesar_elemento(
                serie,
                q_padre_wb=qid_actual,
                qid_raiz=qid_raiz,
                nombre_fondo=contexto_fondo,
                fondo_id_actual=nuevo_fondo_id,
                subfondo_id_actual=nuevo_subfondo_id,
                q_productor_fondo=q_productor_actual,
                nivel=nivel+1
            )

    elif tipo == "serie":
        serie_id = elem.get("serie_id")
        nombre_serie = sanitizar_texto(elem.get("nombre_serie") or elem.get("titulo"))
        url_serie = elem.get("url")
        id_local = f"SERIE_{serie_id}"
        padre_desc = nombre_fondo if nombre_fondo else "fondo documental"
        descripcion = f"Serie documental perteneciente al {padre_desc}"

        metadata = {
            "fondo_id": str(fondo_id_actual) if fondo_id_actual else "",
            "serie_id": str(serie_id) if serie_id else "",
            "titulo": nombre_serie,
            "tipo": "serie"
        }
        if subfondo_id_actual:
            metadata["subfondo_id"] = str(subfondo_id_actual)

        # La serie tampoco crea productor propio: hereda el del fondo (a
        # través de q_productor_fondo, que ya viene del subfondo si lo hay).
        metadata["qid_padre"] = q_padre_wb or ""
        metadata["q_productor"] = q_productor_fondo or ""
        metadata["alcance_contenido"] = descripcion

        crear_o_obtener_item(
            id_local=id_local,
            titulo=nombre_serie,
            q_tipo_agrupacion=Q_SERIE,
            descripcion=descripcion,
            id_origen=str(serie_id) if serie_id else None,
            q_padre_wb=q_padre_wb,
            url_acceso=url_serie,
            nivel=nivel,
            metadata_extra=metadata,
            alcance_contenido=descripcion,
            q_productor=q_productor_fondo
        )

# ==========================================
# 5. EJECUCIÓN DEL SCRIPT
# ==========================================
if __name__ == "__main__":
    try:
        with open(CUADRO_FILE, "r", encoding="utf-8") as f:
            cuadro_datos = json.load(f)

        print("=== CARGA DEL CUADRO DE CLASIFICACIÓN ===")
        print(f"Modo de ejecución: {'SIMULACIÓN (Consola)' if MODO_SIMULACION else 'ESCRITURA REAL (Wikibase)'}")

        total_nodos = contar_elementos(cuadro_datos)
        _progreso = Progreso(total_nodos, etiqueta="nodos del cuadro")

        procesar_elemento(cuadro_datos, q_padre_wb=None, qid_raiz=None, nombre_fondo=None,
                           fondo_id_actual=None, subfondo_id_actual=None, q_productor_fondo=None, nivel=0)

        _progreso.resumen(
            "RESUMEN DE EJECUCIÓN",
            creados=_contadores["creados"],
            omitidos=_contadores["omitidos"],
            actualizados=_contadores["actualizados"],
            fallidos=_contadores["fallidos"],
        )

    except FileNotFoundError:
        print(f"❌ No se encontró el archivo JSON con los datos: '{CUADRO_FILE}'")
    except Exception as e:
        print(f"❌ Ocurrió un error inesperado durante la ejecución: {e}")
