"""
inyectar_registros.py
======================
Inyecta en Wikibase los registros documentales preparados por
transformar_registros.py (mapa_items_inyectar_wikibase.json).

Genera/actualiza DOS ficheros de salida, con propósitos distintos:

1. mapa_items_inyectados_wikibase.json — mapa de CONTROL, para idempotencia.
   Se lee y reescribe en cada ejecución. Guarda, por documento, su QID y el
   estado ("completo"/"parcial") y los QID de sus entidades secundarias
   (fecha, instanciaciones digitales, instanciación física); y, aparte, las
   entidades compartidas (agentes/lugares/materias/instituciones/productores)
   con la lista de documentos que las referencian. Si el script se interrumpe
   a mitad de un documento, la próxima ejecución retoma reutilizando lo que
   ya se creó en vez de duplicarlo.

2. registros_inyectados_resuelto.jsonl — FUENTE DE VERDAD, de solo escritura
   (append). Por cada documento completado con éxito, se añade una línea con
   el registro original completo (tal como salió de transformar_registros.py)
   más un bloque "_wikibase" con todos los QID ya resueltos: el documento, sus
   secundarias y sus puntos de acceso. Es el fichero a consultar para saber
   qué hay realmente en Wikibase y con qué IRI/QID corresponde cada campo —
   no se reescribe nunca entero, solo crece.
"""

import csv
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from wikibaseintegrator.wbi_enums import ActionIfExists

from config_carmesi import wbi, LOAD_FILES_PATH, MEDIAWIKI_API_URL, login_instance
from wb_claims import construir_claim
from progreso import Progreso

_progreso: Optional["Progreso"] = None  # se inicializa en main()

# ==========================================
# 0. CONFIGURACIÓN
# ==========================================

MAX_REGISTROS: Optional[int] = 0  # 0 = sin límite; útil para pruebas cortas

TABLA_P = os.path.join(LOAD_FILES_PATH, "2_creacion_P.tsv")
MAPA_ONTOLOGIA_PATH = os.path.join(LOAD_FILES_PATH, "mapa_ontologia_wikibase.json")
MAPA_CUADRO_PATH = os.path.join(LOAD_FILES_PATH, "mapa_cuadro_wikibase.json")
FICHERO_ENTRADA = os.path.join(LOAD_FILES_PATH, "mapa_items_inyectar_wikibase.json")
MAPA_CONTROL_PATH = os.path.join(LOAD_FILES_PATH, "mapa_items_inyectados_wikibase.json")
FICHERO_RESUELTO_PATH = os.path.join(LOAD_FILES_PATH, "registros_inyectados_resuelto.jsonl")

RATE_LIMIT_SLEEP = 0.2

PLACEHOLDERS_NO_RESUELTOS = ("PENDIENTE_", "SERIE_NO_DEFINIDA", "SIN_ID_ORIGEN")

# Salvaguarda adicional de red, activable/desactivable sin tocar código: antes
# de crear un punto de acceso nuevo (agente/lugar/materia/institución) se
# consulta en vivo si ya existe un ítem con el mismo P_id_origen — ver
# _buscar_qid_existente_por_id_origen() para el porqué y sus límites.
VERIFICAR_DUPLICADOS_EN_WIKIBASE = os.environ.get("CARMESI_VERIFICAR_DUPLICADOS", "1") != "0"


def _avisar(texto: str) -> None:
    """Línea que debe persistir (aviso/error). Usa Progreso si ya está
    inicializado (no rompe la línea dinámica); si no, print normal."""
    if _progreso is not None:
        _progreso.aviso(texto)
    else:
        print(texto)


def _actualizar(texto: str) -> None:
    """Actualiza la línea de progreso dinámica sin generar una línea nueva.
    Si Progreso aún no está inicializado, se ignora silenciosamente (solo
    ocurre en el arranque del módulo, antes de main())."""
    if _progreso is not None:
        _progreso.actualizar(texto)


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _es_placeholder_no_resuelto(valor: Any) -> bool:
    if not valor or not isinstance(valor, str):
        return True
    return any(valor.startswith(p) for p in PLACEHOLDERS_NO_RESUELTOS)


# ==========================================
# 1. CARGA DE FICHEROS DE APOYO
# ==========================================

def cargar_json(path: str) -> Any:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def cargar_datatypes_propiedades(ruta_tsv: str) -> Dict[str, str]:
    if not os.path.isfile(ruta_tsv):
        sys.exit(f"[ERROR] No se encuentra la tabla de propiedades: {ruta_tsv}")
    datatypes = {}
    with open(ruta_tsv, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            datatypes[row["id_local"].strip()] = row["datatype"].strip()
    return datatypes


ontologia: Dict[str, str] = cargar_json(MAPA_ONTOLOGIA_PATH) or {}
mapa_cuadro: Dict[str, Any] = cargar_json(MAPA_CUADRO_PATH) or {}
datatypes_prop: Dict[str, str] = cargar_datatypes_propiedades(TABLA_P)

if not ontologia:
    sys.exit(f"[ERROR] {MAPA_ONTOLOGIA_PATH} está vacío o no existe. Ejecute primero inyectar_ontologia.py.")

registros_preparados = cargar_json(FICHERO_ENTRADA)
if not registros_preparados:
    sys.exit(f"[ERROR] No se encontraron registros en {FICHERO_ENTRADA}. Ejecute primero transformar_registros.py.")


def _cargar_mapa_control() -> Dict[str, Any]:
    data = cargar_json(MAPA_CONTROL_PATH)
    if not data or "documentos" not in data:
        return {"documentos": {}, "entidades_compartidas": {}}
    data.setdefault("entidades_compartidas", {})
    return data


mapa_control: Dict[str, Any] = _cargar_mapa_control()


GUARDAR_CADA_N_CAMBIOS = 25  # frecuencia de volcado a disco para cambios NO críticos (ver más abajo)
_cambios_sin_guardar = 0

def _volcar_mapa_control() -> None:
    """Escritura atómica real a disco: fichero temporal + rename."""
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


def guardar_mapa_control(forzar: bool = False) -> None:
    """`mapa_control` (en memoria) ya está actualizado por quien llama antes de
    invocar esta función — aquí solo se decide si el volcado a disco ocurre
    ahora o se aplaza.

    Escribir el mapa completo en cada evento reescribe un fichero cada vez más
    grande un número de veces innecesario si se hiciera para CUALQUIER cambio
    (p. ej. cada vez que un documento ya conocido vuelve a referenciar una
    entidad compartida ya existente). Por eso esos cambios de bajo riesgo se
    siguen agrupando cada GUARDAR_CADA_N_CAMBIOS.

    Pero la creación de una entidad NUEVA (un QID que aún no existía en el
    mapa) es distinta: si el proceso termina de forma no controlada entre la
    escritura en Wikibase y el volcado a disco de esa clave —una desconexión
    de red, un kill, un timeout que corta la respuesta HTTP ya con el ítem
    creado en el servidor— la próxima ejecución no encontrará esa clave en
    `entidades_compartidas` y creará un ítem duplicado para la misma entidad
    real. Aquí es donde `_escribir_con_reutilizacion()` no protege: solo
    reconoce el rechazo de Wikibase por etiqueta duplicada (un error ANTES de
    escribir), no un fallo de red DESPUÉS de escribir con éxito. Por eso todo
    llamador que registre una clave nueva debe pasar forzar=True — ver
    `registrar_entidad_compartida()` y el checkpoint "completo" de documento.
    """
    global _cambios_sin_guardar
    _cambios_sin_guardar += 1

    if not forzar and _cambios_sin_guardar < GUARDAR_CADA_N_CAMBIOS:
        return

    _volcar_mapa_control()
    _cambios_sin_guardar = 0


def registrar_entidad_compartida(clave_mapa: str, qid: str, tipo: str, label_es: str, id_doc: str) -> None:
    entidades = mapa_control["entidades_compartidas"]
    entrada = entidades.get(clave_mapa)
    es_entrada_nueva = entrada is None
    if entrada is None:
        entrada = {"qid": qid, "tipo": tipo, "label_es": label_es, "documentos_relacionados": []}
        entidades[clave_mapa] = entrada
    elif entrada.get("qid") != qid:
        # Reconciliación de un QID desincronizado (p. ej. un productor cuya
        # entrada quedó con un QID simulado obsoleto en
        # cargar_cuadro_clasificacion.py, o reutilizado por conflicto de
        # etiqueta en _escribir_con_reutilizacion() de este mismo script) —
        # sin esto, la rama "ya existe" de arriba nunca actualizaba el "qid"
        # almacenado y el fichero de control quedaba desincronizado para
        # siempre, repitiendo el mismo problema en cada reejecución.
        entrada["qid"] = qid
    if id_doc and id_doc not in entrada["documentos_relacionados"]:
        entrada["documentos_relacionados"].append(id_doc)
    entrada["actualizado_en"] = _ahora()
    # Forzar el volcado a disco cuando la clave es nueva: es el momento en que
    # existe una entidad recién creada en Wikibase sin aún ningún respaldo
    # local — la ventana de riesgo de duplicado descrita en guardar_mapa_control().
    guardar_mapa_control(forzar=es_entrada_nueva)


# ==========================================
# 2. RESOLUCIÓN DE Q/P
# ==========================================

def resolver_id(clave: Optional[str]) -> Optional[str]:
    """Resuelve un id_local a su QID/PID real, buscando en orden:
    entidades ya creadas en esta sesión (compartidas), ontología, cuadro de
    clasificación. Si `clave` ya es un QID/PID real, se devuelve tal cual."""
    if not clave:
        return None
    clave = str(clave).strip()

    if re.match(r"^[QP]\d+$", clave):
        return clave

    if clave in mapa_control["entidades_compartidas"]:
        return mapa_control["entidades_compartidas"][clave]["qid"]

    if clave in ontologia:
        return ontologia[clave]

    if clave in mapa_cuadro:
        val = mapa_cuadro[clave]
        return val.get("qid") if isinstance(val, dict) else val

    return None


def resolver_propiedad(id_local: str) -> Optional[str]:
    pid = resolver_id(id_local)
    if not pid:
        _avisar(f"      ⚠️ Propiedad '{id_local}' no existe en la ontología (¿ejecutó inyectar_ontologia.py con la tabla P al día?)")
    return pid


def _escribir_con_reutilizacion(item, contexto: str) -> str:
    """Escribe un item; si Wikibase rechaza por conflicto de etiqueta duplicada
    (label+description ya usados por otro ítem), reutiliza ese QID en vez de
    fallar. Red de seguridad ante un mapa de control desincronizado — en
    operación normal no debería activarse.

    NOTA: esto solo cubre el rechazo síncrono de Wikibase (error devuelto
    ANTES de crear el ítem). No cubre el caso en que la escritura tuvo éxito
    en el servidor pero la respuesta HTTP no llegó al cliente (timeout/corte
    de red tras el commit): ahí la excepción es de otro tipo y se propaga sin
    reutilizar nada. Ese escenario solo se mitiga minimizando la ventana entre
    "creado en Wikibase" y "registrado en el mapa de control" — ver
    guardar_mapa_control().
    """
    try:
        res = item.write()
        return res.id
    except Exception as e:
        error_str = str(e)
        match_q = re.search(r"(Q\d+)", error_str)
        if match_q and ("already has label" in error_str or "ModificationFailed" in error_str):
            qid = match_q.group(1)
            _avisar(f"      ↪ [Reutilizado por conflicto de etiqueta] {contexto} -> {qid} (revise el mapa de control)")
            return qid
        raise


def _buscar_qid_existente_por_id_origen(pid_id_origen: str, valor_id_origen: str) -> Optional[str]:
    """Salvaguarda adicional de red, EN VIVO, antes de crear un punto de
    acceso nuevo: pregunta al índice de búsqueda de Wikibase (CirrusSearch,
    sintaxis 'haswbstatement:PID=valor', el mismo mecanismo que usa Wikidata)
    si ya existe algún ítem con ese mismo P_id_origen.

    POR QUÉ HACE FALTA además del mapa de control local: el mapa local
    (mapa_control["entidades_compartidas"], forzado a disco en cuanto se crea
    una clave nueva — ver guardar_mapa_control()) cubre el caso normal y el de
    una interrupción limpia. No cubre el caso residual en el que la escritura
    tuvo éxito en el servidor de Wikibase pero la respuesta HTTP se perdió por
    red antes de llegar al cliente: ahí ni el mapa local ni el chequeo de
    "etiqueta duplicada" de _escribir_con_reutilizacion() se activan, porque
    Wikibase NO exige etiquetas únicas entre ítems (a diferencia de las
    propiedades) — dos ítems con el mismo label_es y la misma descripción se
    crean sin conflicto. Esta consulta en vivo es la única red de seguridad
    para ese caso concreto.

    LIMITACIÓN CONOCIDA (documentar en el artículo si se usa como argumento de
    fiabilidad): el índice de CirrusSearch no es necesariamente consistente en
    tiempo real; puede haber unos segundos de retraso de indexación tras crear
    un ítem. Por eso esta consulta es un complemento del mapa local dentro de
    una misma ejecución continua (donde el mapa ya resuelve el caso normal
    instantáneamente, sin red), y el salvavidas principal para una
    REANUDACIÓN tras un corte — momento en que ese margen de segundos ya ha
    pasado sobradamente. Si la consulta falla por cualquier motivo (red,
    permisos, CirrusSearch no disponible en esta instancia de Wikibase Cloud)
    se registra un aviso y se continúa como si no hubiese encontrado nada:
    nunca bloquea ni aborta la inyección. Puede desactivarse por completo con
    la variable de entorno CARMESI_VERIFICAR_DUPLICADOS=0.
    """
    if not VERIFICAR_DUPLICADOS_EN_WIKIBASE or not pid_id_origen or not valor_id_origen:
        return None
    try:
        session = login_instance.get_session()
        resp = session.get(
            MEDIAWIKI_API_URL,
            params={
                "action": "query",
                "list": "search",
                "srsearch": f"haswbstatement:{pid_id_origen}={valor_id_origen}",
                "srlimit": 5,
                "srnamespace": "0|120",  # namespace principal + namespace estándar de ítems en Wikibase Suite
                "format": "json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        resultados = resp.json().get("query", {}).get("search", [])
        for r in resultados:
            m = re.search(r"(Q\d+)\s*$", r.get("title", ""))
            if m:
                return m.group(1)
    except Exception as e:
        _avisar(f"      ⚠️ Comprobación en vivo de duplicados en Wikibase falló (se ignora, no bloquea la inyección): {e}")
    return None


# ==========================================
# 3. ENTIDADES SECUNDARIAS (fecha / instanciaciones — privadas de un documento)
# ==========================================

def crear_entidad_secundaria(datos: Optional[Dict[str, Any]], id_doc: str, tipo: str = "secundaria") -> Optional[str]:
    """Crea una entidad secundaria (Q_fecha, Q_instanciacion...) a partir de un
    dict con label_es/desc_es/P_instancia_de + el resto de sus atributos.

    Si `datos` trae "clave_mapa" (hoy: solo Q_fecha), se comprueba primero en
    `entidades_compartidas` — una consulta local, sin red — y se reutiliza el
    QID existente sin intentar escribir nada. Sin clave_mapa (instanciaciones
    digital/física, privadas de un documento), la idempotencia frente a
    reejecuciones la da el checkpoint de 'secundarias' del llamador."""
    if not datos:
        return None

    label_es = datos.get("label_es") or "Entidad secundaria"
    clave_mapa = datos.get("clave_mapa")

    if clave_mapa and clave_mapa in mapa_control["entidades_compartidas"]:
        qid = mapa_control["entidades_compartidas"][clave_mapa]["qid"]
        registrar_entidad_compartida(clave_mapa, qid, tipo, label_es, id_doc)
        _actualizar(f"[Reutilizada] '{label_es}' -> {qid}")
        return qid

    p_instancia_de = resolver_propiedad("P_instancia_de")
    q_tipo = resolver_id(datos.get("P_instancia_de", ""))

    item = wbi.item.new()
    item.labels.set("es", label_es[:250])
    if datos.get("desc_es"):
        item.descriptions.set("es", datos["desc_es"][:250])

    if p_instancia_de and q_tipo:
        item.claims.add(construir_claim(p_instancia_de, "wikibase-item", q_tipo), action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

    for k_prop, val in datos.items():
        if k_prop in ("label_es", "desc_es", "P_instancia_de", "clave_mapa") or val in (None, "", []):
            continue

        pid = resolver_propiedad(k_prop)
        if not pid:
            continue
        datatype = datatypes_prop.get(k_prop)
        if not datatype:
            _avisar(f"      ⚠️ Datatype desconocido para '{k_prop}', se omite")
            continue

        valores = val if isinstance(val, list) else [val]
        for v in valores:
            if not v:
                continue
            claim = construir_claim(pid, datatype, v, resolver_item=resolver_id)
            if claim is None:
                _avisar(f"      ⚠️ No se pudo resolver '{v}' para '{k_prop}', declaración omitida")
                continue
            item.claims.add(claim, action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

    qid = _escribir_con_reutilizacion(item, f"secundaria '{label_es}'")
    _actualizar(f"[CREADA] {tipo}: '{label_es}' -> {qid}")
    time.sleep(RATE_LIMIT_SLEEP)

    if clave_mapa:
        registrar_entidad_compartida(clave_mapa, qid, tipo, label_es, id_doc)

    return qid


# ==========================================
# 4. PUNTOS DE ACCESO (agentes/lugares/materias — compartidos entre documentos)
# ==========================================

def obtener_o_crear_punto_acceso(pa: Dict[str, Any], id_doc: str) -> Optional[str]:
    label_es = pa.get("label_es")
    clave_mapa = pa.get("clave_mapa")
    categoria = pa.get("categoria", "punto_acceso")

    if not label_es:
        return None

    if clave_mapa and clave_mapa in mapa_control["entidades_compartidas"]:
        qid = mapa_control["entidades_compartidas"][clave_mapa]["qid"]
        registrar_entidad_compartida(clave_mapa, qid, categoria, label_es, id_doc)
        return qid

    # Salvaguarda adicional: sin coincidencia en el mapa local, se pregunta en
    # vivo a Wikibase antes de crear nada — ver _buscar_qid_existente_por_id_origen().
    val_id_origen = pa.get("P_id_origen")
    if val_id_origen and clave_mapa:
        pid_id_origen = resolver_propiedad("P_id_origen")
        qid_existente = _buscar_qid_existente_por_id_origen(pid_id_origen, str(val_id_origen)) if pid_id_origen else None
        if qid_existente:
            _avisar(f"      ↪ [Reutilizado por comprobación en vivo] '{label_es}' (id_origen={val_id_origen}) -> {qid_existente} (mapa local desincronizado, corregido)")
            registrar_entidad_compartida(clave_mapa, qid_existente, categoria, label_es, id_doc)
            return qid_existente

    q_instancia = resolver_id(pa.get("instancia_de", "Q_institucion"))
    p_instancia_de = resolver_propiedad("P_instancia_de")

    item = wbi.item.new()
    item.labels.set("es", label_es[:250])
    item.descriptions.set("es", (pa.get("desc_es") or f"{categoria.capitalize()} del repositorio Carmesí")[:250])

    if p_instancia_de and q_instancia:
        item.claims.add(construir_claim(p_instancia_de, "wikibase-item", q_instancia), action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

    val_id_origen = pa.get("P_id_origen")
    if val_id_origen:
        pid = resolver_propiedad("P_id_origen")
        if pid:
            claim = construir_claim(pid, datatypes_prop["P_id_origen"], val_id_origen)
            item.claims.add(claim, action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

    val_url = pa.get("url")
    if val_url:
        pid = resolver_propiedad("P_url_acceso")
        if pid:
            claim = construir_claim(pid, datatypes_prop["P_url_acceso"], val_url)
            item.claims.add(claim, action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

    qid = _escribir_con_reutilizacion(item, f"punto de acceso '{label_es}'")
    _actualizar(f"[CREADO] {categoria}: '{label_es}' -> {qid}")
    time.sleep(RATE_LIMIT_SLEEP)

    if clave_mapa:
        registrar_entidad_compartida(clave_mapa, qid, categoria, label_es, id_doc)
    else:
        _avisar(f"      ⚠️ '{label_es}' sin clave_mapa (sin id de origen) — no se podrá reconciliar en futuras apariciones")

    return qid


# ==========================================
# 5. DOCUMENTO PRINCIPAL
# ==========================================

# Propiedades de texto/URL directas del documento (todas repetibles si el
# valor de origen es una lista, aunque en la práctica solo P_lengua lo es
# hoy). El datatype real de cada una se lee de 2_creacion_P.tsv, no se supone.
PROPS_DOCUMENTO = [
    "P_codigo_ref", "P_titulo", "P_alcance_contenido",
    "P_condiciones_acceso", "P_nota", "P_url_acceso", "P_lengua",
]


def procesar_registro(reg: Dict[str, Any]) -> Optional[str]:
    id_doc = str(reg["id_documento"])
    entrada_previa = mapa_control["documentos"].get(id_doc)

    if entrada_previa and entrada_previa.get("estado") == "completo":
        _actualizar(f"Documento {id_doc} ya completado ({entrada_previa['qid']})")
        return entrada_previa["qid"]

    secundarias_previas = (entrada_previa or {}).get("secundarias", {})
    secundarias = {
        "fecha": secundarias_previas.get("fecha"),
        "instanciaciones_digitales": list(secundarias_previas.get("instanciaciones_digitales") or []),
        "instanciacion_fisica": secundarias_previas.get("instanciacion_fisica"),
    }

    def _checkpoint(estado: str, qid_doc: Optional[str] = None) -> None:
        entrada = {
            "qid": qid_doc or (entrada_previa or {}).get("qid"),
            "titulo": reg.get("label_es"),
            "estado": estado,
            "secundarias": secundarias,
            "actualizado_en": _ahora(),
        }
        mapa_control["documentos"][id_doc] = entrada
        # El checkpoint "completo" (o cualquiera que acabe de fijar un QID de
        # documento nuevo) se fuerza a disco por la misma razón que las
        # entidades compartidas: sin forzar, una interrupción no controlada
        # tras crear el ítem documento pero antes del siguiente volcado de
        # lote llevaría a recrearlo en la próxima ejecución.
        guardar_mapa_control(forzar=(estado == "completo" or qid_doc is not None))

    secc = reg.get("Q_secundarios") or {}

    # --- Fecha (reconciliada por clave_mapa: se reutiliza si ya existe una
    # entidad Q_fecha idéntica, de este documento o de cualquier otro) ---
    if not secundarias["fecha"] and secc.get("entidad_fecha"):
        secundarias["fecha"] = crear_entidad_secundaria(secc["entidad_fecha"], id_doc, tipo="fecha")
        _checkpoint("parcial")

    # --- Instanciaciones digitales (0-2: PDF / DjVu) — privadas de este documento ---
    digitales_pendientes = secc.get("instanciaciones_digitales") or []
    ya_creadas = len(secundarias["instanciaciones_digitales"])
    for datos_inst in digitales_pendientes[ya_creadas:]:
        qid_inst = crear_entidad_secundaria(datos_inst, id_doc, tipo="instanciacion_digital")
        if qid_inst:
            secundarias["instanciaciones_digitales"].append(qid_inst)
            _checkpoint("parcial")

    # --- Instanciación física — privada de este documento ---
    if not secundarias["instanciacion_fisica"] and secc.get("instanciacion_fisica"):
        secundarias["instanciacion_fisica"] = crear_entidad_secundaria(secc["instanciacion_fisica"], id_doc, tipo="instanciacion_fisica")
        _checkpoint("parcial")

    # --- Documento principal ---
    item_doc = wbi.item.new()
    item_doc.labels.set("es", (reg.get("label_es") or "Sin título")[:250])
    item_doc.descriptions.set("es", (reg.get("desc_es") or "")[:250])

    decl = reg.get("declaraciones_directas", {})

    # P_instancia_de -> Q_documento
    pid = resolver_propiedad("P_instancia_de")
    qid_tipo = resolver_id(decl.get("P_instancia_de", "Q_documento"))
    if pid and qid_tipo:
        item_doc.claims.add(construir_claim(pid, "wikibase-item", qid_tipo), action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

    # P_incluido_en -> serie (puede venir sin resolver si el cuadro no la tenía)
    valor_serie = decl.get("P_incluido_en")
    if _es_placeholder_no_resuelto(valor_serie):
        if valor_serie:
            _avisar(f"      ⚠️ Documento {id_doc}: serie sin resolver ('{valor_serie}'), se omite P_incluido_en")
    else:
        pid = resolver_propiedad("P_incluido_en")
        if pid:
            item_doc.claims.add(construir_claim(pid, "wikibase-item", valor_serie), action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

    # Propiedades de texto/URL/lengua directas
    for k_prop in PROPS_DOCUMENTO:
        val = decl.get(k_prop)
        if val in (None, "", []):
            continue
        pid = resolver_propiedad(k_prop)
        if not pid:
            continue
        datatype = datatypes_prop.get(k_prop)
        if not datatype:
            continue
        valores = val if isinstance(val, list) else [val]
        for v in valores:
            if not v:
                continue
            claim = construir_claim(pid, datatype, v, resolver_item=resolver_id)
            if claim is None:
                _avisar(f"      ⚠️ Documento {id_doc}: no se pudo resolver '{v}' para '{k_prop}'")
                continue
            item_doc.claims.add(claim, action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

    # Relaciones con las entidades secundarias
    if secundarias["fecha"]:
        pid = resolver_propiedad("P_fecha")
        if pid:
            item_doc.claims.add(construir_claim(pid, "wikibase-item", secundarias["fecha"]), action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

    if secundarias["instanciaciones_digitales"]:
        pid = resolver_propiedad("P_instanciacion_digital")
        if pid:
            for qid_inst in secundarias["instanciaciones_digitales"]:
                item_doc.claims.add(construir_claim(pid, "wikibase-item", qid_inst), action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

    if secundarias["instanciacion_fisica"]:
        pid = resolver_propiedad("P_instanciacion_fisica")
        if pid:
            item_doc.claims.add(construir_claim(pid, "wikibase-item", secundarias["instanciacion_fisica"]), action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

    # Puntos de acceso (institución/persona/materia/lugar/productor)
    puntos_acceso = reg.get("puntos_acceso", [])
    puntos_resueltos = []
    for pa in puntos_acceso:
        pid = resolver_propiedad(pa.get("propiedad_registro", ""))
        if not pid:
            continue
        qid_pa = obtener_o_crear_punto_acceso(pa, id_doc)
        if qid_pa:
            item_doc.claims.add(construir_claim(pid, "wikibase-item", qid_pa), action_if_exists=ActionIfExists.APPEND_OR_REPLACE)
            puntos_resueltos.append({"categoria": pa.get("categoria"), "clave_mapa": pa.get("clave_mapa"), "qid": qid_pa})

    qid_doc = _escribir_con_reutilizacion(item_doc, f"documento {id_doc}")
    _checkpoint("completo", qid_doc)

    # --- Fuente de verdad: registro original + resolución completa ---
    registro_resuelto = dict(reg)
    registro_resuelto["_wikibase"] = {
        "qid_documento": qid_doc,
        "secundarias": secundarias,
        "puntos_acceso": puntos_resueltos,
        "inyectado_en": _ahora(),
    }
    with open(FICHERO_RESUELTO_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro_resuelto, ensure_ascii=False) + "\n")

    time.sleep(RATE_LIMIT_SLEEP)
    return qid_doc


# ==========================================
# 6. ORQUESTADOR
# ==========================================

def main() -> None:
    global _progreso

    total = len(registros_preparados)
    limite = MAX_REGISTROS or total
    print(f"=== INYECCIÓN DE REGISTROS: {total} disponibles (límite de esta ejecución: {limite}) ===")

    completados = omitidos = fallidos = 0
    _progreso = Progreso(limite, etiqueta="documentos")

    for reg in registros_preparados[:limite]:
        id_doc = str(reg.get("id_documento"))
        label = (reg.get("label_es") or "")[:60]

        entrada_previa = mapa_control["documentos"].get(id_doc)
        ya_completo = entrada_previa and entrada_previa.get("estado") == "completo"

        try:
            qid_doc = procesar_registro(reg)
            if ya_completo:
                omitidos += 1
                _progreso.avanzar(f"Documento {id_doc} — {label} [ya existía: {qid_doc}]")
            else:
                completados += 1
                _progreso.avanzar(f"Documento {id_doc} — {label} -> {qid_doc}")
        except Exception as e:
            fallidos += 1
            _progreso.aviso(f"   ❌ [FALLO] Documento {id_doc}: {e}")
            _progreso.avanzar(f"Documento {id_doc} — FALLIDO")

    _progreso.resumen(
        "RESUMEN DE INYECCIÓN DE REGISTROS",
        completados_en_esta_ejecucion=completados,
        ya_estaban_completos=omitidos,
        fallidos=fallidos,
    )
    guardar_mapa_control(forzar=True)  # asegura que el último lote de cambios queda en disco
    print(f"   Mapa de control: {MAPA_CONTROL_PATH}")
    print(f"   Fuente de verdad: {FICHERO_RESUELTO_PATH}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        if _progreso is not None:
            _progreso.limpiar()
        print("\nInterrumpido por el usuario. Guardando el progreso pendiente antes de salir...")
    finally:
        # Se ejecuta siempre (fin normal, interrupción, o excepción no
        # capturada): ningún camino de salida puede dejar cambios en memoria
        # sin volcar a disco, sin importar el lote de GUARDAR_CADA_N_CAMBIOS.
        guardar_mapa_control(forzar=True)
