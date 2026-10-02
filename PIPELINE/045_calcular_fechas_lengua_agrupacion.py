"""
045_calcular_fechas_lengua_agrupacion.py
=========================================
Calcula y declara, sobre cada ítem Q_agrupacion_doc (fondo/subfondo/serie),
dos propiedades agregadas a partir de sus documentos miembro — la vertiente
de "descripción-resumen" de la descripción multidimensional de RiC-CM 1.0
§1.6.2.2 que aún faltaba tras la incorporación de P_productor/P_alcance_contenido
en cargar_cuadro_clasificacion.py (véase CARMESI-TEST-19_DOCUMENTACION.wikitext,
apartado 2.4.6):

- P_fecha_agrupacion (rico:hasOrHadAllMembersWithCreationDate): rango
  cronológico extremo (P_fecha_inicio mínimo / P_fecha_final máximo) de
  TODOS los documentos del conjunto — cierto por construcción para la
  cardinalidad "All". Se materializa como una entidad Q_fecha propia del
  conjunto (mismo patrón que crear_entidad_secundaria() de 040), nunca
  compartida entre nodos distintos aunque coincida el rango.

- P_lengua_agrupacion (rico:hasOrHadAllMembersWithLanguage): únicamente
  cuando TODOS los documentos del conjunto (con lengua declarada) comparten
  la misma, sin excepción — un solo documento con otra lengua, o con más de
  una lengua declarada, descarta la propiedad para ese nodo. Enlaza
  directamente al ítem Q_lengua ya existente (Q_castellano/Q_valenciano/
  Q_latin): no crea ninguna entidad nueva.

Ambas cifras se calculan mediante roll-up bottom-up (serie -> subfondo ->
fondo) sobre los propios resultados ya agregados de sus hijos, no
recalculando desde cero sobre los documentos en cada nivel — operación
válida porque mín/máx de fechas y la comprobación de valor único son
asociativas.

Requiere, en este orden: 010_inyectar_ontologia.py (con P_fecha_agrupacion y
P_lengua_agrupacion ya creadas), 020_cargar_cuadro_clasificacion.py e
040_inyectar_registros.py ya ejecutados. La fuente de los datos de cada
documento es registros_inyectados_resuelto.jsonl — la única que refleja qué
hay REALMENTE inyectado en Wikibase (no la salida de 030, que puede incluir
documentos cuya inyección real falló después).
"""

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from wikibaseintegrator.datatypes import Item, String
from wikibaseintegrator.wbi_enums import ActionIfExists

from config_carmesi import wbi, LOAD_FILES_PATH
from wb_claims import construir_claim
from progreso import Progreso

MAPA_ONTOLOGIA_PATH = os.path.join(LOAD_FILES_PATH, "mapa_ontologia_wikibase.json")
MAPA_CUADRO_PATH = os.path.join(LOAD_FILES_PATH, "mapa_cuadro_wikibase.json")
FICHERO_RESUELTO_PATH = os.path.join(LOAD_FILES_PATH, "registros_inyectados_resuelto.jsonl")

# Mapa de CONTROL compartido con 020_cargar_cuadro_clasificacion.py y
# 040_inyectar_registros.py — mismo fichero, mismo namespace
# "entidades_compartidas". Aquí se registran las nuevas entidades Q_fecha de
# agrupación bajo la clave "FECHA_AGRUP_<id_local>", para que una reejecución
# de este script no las duplique.
MAPA_CONTROL_PATH = os.path.join(LOAD_FILES_PATH, "mapa_items_inyectados_wikibase.json")

# Bandera de simulación: True para probar en consola, False para escribir en Wikibase
MODO_SIMULACION = False

# Sentinela: marca un nodo cuyos documentos con lengua declarada no
# coinciden todos en la misma (incluye el caso de un documento con más de
# una lengua declarada, que por definición no permite afirmar una única
# lengua compartida). Se usa como valor de object() propio para no
# confundirlo nunca con un QID real ni con None ("sin dato").
HETEROGENEO = object()

_RE_QID_REAL = re.compile(r"^Q\d+$")

# Valida sintaxis Y rango real (mes 01-12, día 01-31) de las cadenas ISO de
# precisión variable que produce el pipeline (YYYY / YYYY-MM / YYYY-MM-DD).
# Defensa en profundidad: aunque 030_transformar_registros.py ya no debería
# generar valores como "1950-10-36" o "2026-00-01" (corregido en su propio
# extraer_fecha_singular()), registros_inyectados_resuelto.jsonl es un
# fichero de solo escritura que conserva lo ya generado por ejecuciones
# anteriores del pipeline — una fecha inválida heredada de antes de esa
# corrección no debe contaminar el mín/máx del conjunto.
_RE_FECHA_VALIDA = re.compile(
    r"^\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?$"
)


def _es_fecha_valida(valor: Any) -> bool:
    return bool(valor) and isinstance(valor, str) and bool(_RE_FECHA_VALIDA.match(valor.strip()))


# ==========================================
# 1. CARGA DE FICHEROS DE APOYO
# ==========================================
def cargar_json(path: str, default: Any = None) -> Any:
    if default is None:
        default = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


ontologia: Dict[str, str] = cargar_json(MAPA_ONTOLOGIA_PATH)
if not ontologia:
    raise SystemExit(f"[ERROR] {MAPA_ONTOLOGIA_PATH} vacío. Ejecute primero 010_inyectar_ontologia.py.")


def _get_ontologia(clave: str) -> str:
    try:
        return ontologia[clave]
    except KeyError:
        raise SystemExit(
            f"[ERROR] Falta '{clave}' en {MAPA_ONTOLOGIA_PATH}. "
            f"Añada la propiedad a 2_creacion_P.tsv y relance 010_inyectar_ontologia.py."
        )


P_INSTANCIA_DE = _get_ontologia("P_instancia_de")
P_FECHA_EXPRESADA = _get_ontologia("P_fecha_expresada")
P_FECHA_INICIO = _get_ontologia("P_fecha_inicio")
P_FECHA_FINAL = _get_ontologia("P_fecha_final")
P_FECHA_AGRUPACION = _get_ontologia("P_fecha_agrupacion")
P_LENGUA_AGRUPACION = _get_ontologia("P_lengua_agrupacion")
Q_FECHA = _get_ontologia("Q_fecha")


def guardar_cuadro(cuadro: Dict[str, Any]) -> None:
    """Reescribe mapa_cuadro_wikibase.json con los campos agregados
    (q_fecha_agrupacion/fecha_inicio_agrupacion/fecha_final_agrupacion/
    q_lengua_agrupacion) que este script añade a cada nodo fondo/subfondo/
    serie — mismo fichero que puebla 020_cargar_cuadro_clasificacion.py, y
    que 080_exportar_rdf_ttl.py lee como fuente de verdad local."""
    with open(MAPA_CUADRO_PATH, "w", encoding="utf-8") as f:
        json.dump(cuadro, f, ensure_ascii=False, indent=4)


def cargar_resueltos() -> List[Dict[str, Any]]:
    registros = []
    if os.path.exists(FICHERO_RESUELTO_PATH):
        with open(FICHERO_RESUELTO_PATH, "r", encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if linea:
                    registros.append(json.loads(linea))
    return registros


# ==========================================
# 2. MAPA DE CONTROL COMPARTIDO (idéntico esquema/funciones que 020/040)
# ==========================================
def _cargar_mapa_control() -> Dict[str, Any]:
    data = cargar_json(MAPA_CONTROL_PATH)
    if not data or "documentos" not in data:
        data = data or {}
        data.setdefault("documentos", {})
        data.setdefault("entidades_compartidas", {})
    return data


mapa_control: Dict[str, Any] = _cargar_mapa_control()


def _volcar_mapa_control() -> None:
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


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def registrar_entidad_compartida(clave_mapa: str, qid: str, tipo: str, label_es: str, id_referencia: str) -> None:
    entidades = mapa_control["entidades_compartidas"]
    entrada = entidades.get(clave_mapa)
    if entrada is None:
        entrada = {"qid": qid, "tipo": tipo, "label_es": label_es, "documentos_relacionados": []}
        entidades[clave_mapa] = entrada
    else:
        entrada["qid"] = qid
    if id_referencia and id_referencia not in entrada["documentos_relacionados"]:
        entrada["documentos_relacionados"].append(id_referencia)
    entrada["actualizado_en"] = _ahora()
    _volcar_mapa_control()


# ==========================================
# 3. AGREGACIÓN: documentos -> serie -> subfondo -> fondo
# ==========================================
def construir_arbol(cuadro: Dict[str, Any]) -> Tuple[Dict, Dict, Dict]:
    series, subfondos, fondos = {}, {}, {}
    for id_local, datos in cuadro.items():
        if not isinstance(datos, dict):
            continue
        tipo = datos.get("tipo")
        if tipo == "serie":
            series[id_local] = datos
        elif tipo == "subfondo":
            subfondos[id_local] = datos
        elif tipo == "fondo":
            fondos[id_local] = datos
    return series, subfondos, fondos


def calcular_por_serie(registros: List[Dict[str, Any]], progreso: Optional[Progreso] = None) -> Dict[str, Dict[str, List]]:
    """QID de la serie -> {'fechas': [(ini, fin), ...], 'lenguas': [QID real | HETEROGENEO, ...]},
    leído directamente de registros_inyectados_resuelto.jsonl (documentos
    REALMENTE inyectados). P_incluido_en ya viene resuelto a un QID real
    desde 030 (usa mapa_cuadro_wikibase.json); P_lengua, en cambio, queda
    como id_local (p. ej. 'Q_castellano') y se resuelve aquí contra la
    ontología."""
    por_serie: Dict[str, Dict[str, List]] = {}
    fechas_invalidas_descartadas = 0
    for reg in registros:
        decl = reg.get("declaraciones_directas") or {}
        q_serie = str(decl.get("P_incluido_en", "")).strip()
        if not _RE_QID_REAL.match(q_serie):
            continue
        entrada = por_serie.setdefault(q_serie, {"fechas": [], "lenguas": []})

        ent_fecha = ((reg.get("Q_secundarios") or {}).get("entidad_fecha") or {})
        ini, fin = ent_fecha.get("P_fecha_inicio"), ent_fecha.get("P_fecha_final")
        if ini and fin:
            if _es_fecha_valida(ini) and _es_fecha_valida(fin):
                entrada["fechas"].append((ini, fin))
            else:
                fechas_invalidas_descartadas += 1
                if progreso is not None:
                    id_doc = reg.get("id_documento", "?")
                    progreso.aviso(
                        f"   ⚠️ Documento {id_doc}: fecha inválida descartada de la agregación "
                        f"('{ini}' / '{fin}')"
                    )

        lenguas_doc = decl.get("P_lengua") or []
        if len(lenguas_doc) == 1:
            qid_lengua = ontologia.get(lenguas_doc[0])
            if qid_lengua:
                entrada["lenguas"].append(qid_lengua)
        elif len(lenguas_doc) > 1:
            # Documento declarado en más de una lengua: no permite afirmar
            # una lengua única para el conjunto que lo contiene.
            entrada["lenguas"].append(HETEROGENEO)
        # 0 lenguas declaradas: el documento no aporta información, no
        # cuenta ni a favor ni en contra de la homogeneidad del conjunto.

    if fechas_invalidas_descartadas and progreso is not None:
        progreso.aviso(
            f"   ⚠️ Total: {fechas_invalidas_descartadas} fecha(s) inválida(s) heredada(s) "
            f"descartadas de la agregación (no afectan a los documentos, solo al cálculo agregado)"
        )

    return por_serie


def resumen_fechas(pares: List[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
    """Mín/máx lexicográfico sobre cadenas ISO 8601 de precisión variable
    (YYYY / YYYY-MM / YYYY-MM-DD) — el orden lexicográfico de estas cadenas
    coincide con el orden cronológico real. Simplificación asumida y
    documentada: una fecha de precisión reducida (p. ej. un año suelto) se
    compara tal cual, sin expandirla a su intervalo [inicio, fin] real."""
    if not pares:
        return None
    inicios = [i for i, _ in pares if i]
    finales = [f for _, f in pares if f]
    if not inicios or not finales:
        return None
    return min(inicios), max(finales)


def resumen_lengua(valores: List[Any]) -> Any:
    """None = sin dato; HETEROGENEO = más de una lengua entre los documentos
    con lengua declarada; en otro caso, el único QID compartido por todos."""
    limpios = [v for v in valores if v is not None]
    if not limpios:
        return None
    if HETEROGENEO in limpios:
        return HETEROGENEO
    if len(set(limpios)) > 1:
        return HETEROGENEO
    return limpios[0]


def construir_resultados(series: Dict, subfondos: Dict, fondos: Dict, por_serie: Dict) -> Dict[str, Dict[str, Any]]:
    resultados: Dict[str, Dict[str, Any]] = {}

    for id_local, datos in series.items():
        q = datos.get("qid")
        entrada = por_serie.get(q, {"fechas": [], "lenguas": []})
        resultados[id_local] = {
            "nivel": "serie",
            "qid": q,
            "fondo_id": str(datos.get("fondo_id") or ""),
            "subfondo_id": str(datos["subfondo_id"]) if datos.get("subfondo_id") else "",
            "fechas": resumen_fechas(entrada["fechas"]),
            "lengua": resumen_lengua(entrada["lenguas"]),
        }

    series_por_subfondo: Dict[str, List[Dict]] = {}
    series_directas_por_fondo: Dict[str, List[Dict]] = {}
    for r in resultados.values():
        if r["subfondo_id"]:
            series_por_subfondo.setdefault(r["subfondo_id"], []).append(r)
        else:
            series_directas_por_fondo.setdefault(r["fondo_id"], []).append(r)

    for id_local, datos in subfondos.items():
        sub_id = str(datos.get("subfondo_id") or "")
        hijos = series_por_subfondo.get(sub_id, [])
        resultados[id_local] = {
            "nivel": "subfondo",
            "qid": datos.get("qid"),
            "fondo_id": str(datos.get("fondo_id") or ""),
            "subfondo_id": sub_id,
            "fechas": resumen_fechas([h["fechas"] for h in hijos if h["fechas"]]),
            "lengua": resumen_lengua([h["lengua"] for h in hijos if h["lengua"] is not None]),
        }

    subfondos_por_fondo: Dict[str, List[Dict]] = {}
    for r in resultados.values():
        if r["nivel"] == "subfondo":
            subfondos_por_fondo.setdefault(r["fondo_id"], []).append(r)

    for id_local, datos in fondos.items():
        f_id = str(datos.get("fondo_id") or "")
        hijos = subfondos_por_fondo.get(f_id, []) + series_directas_por_fondo.get(f_id, [])
        resultados[id_local] = {
            "nivel": "fondo",
            "qid": datos.get("qid"),
            "fondo_id": f_id,
            "subfondo_id": "",
            "fechas": resumen_fechas([h["fechas"] for h in hijos if h["fechas"]]),
            "lengua": resumen_lengua([h["lengua"] for h in hijos if h["lengua"] is not None]),
        }

    return resultados


# ==========================================
# 4. ENTIDAD Q_fecha DE AGRUPACIÓN (por nodo, nunca compartida entre nodos)
# ==========================================
def obtener_o_crear_fecha_agrupacion(id_local: str, ini: str, fin: str, progreso: Progreso) -> Optional[str]:
    clave = f"FECHA_AGRUP_{id_local}"
    label = f"{ini} - {fin}"

    if clave in mapa_control["entidades_compartidas"]:
        qid_previo = mapa_control["entidades_compartidas"][clave]["qid"]
        if _RE_QID_REAL.match(str(qid_previo)):
            return qid_previo
        if not MODO_SIMULACION:
            progreso.aviso(f"   ⚠️ Entrada simulada obsoleta para '{clave}' ({qid_previo}) — se descarta y se crea la entidad real")

    if MODO_SIMULACION:
        qid_sim = f"WB_{clave}"
        registrar_entidad_compartida(clave, qid_sim, "fecha_agrupacion", label, id_local)
        return qid_sim

    try:
        item = wbi.item.new()
        item.labels.set("es", label[:250])
        # El label ("{ini} - {fin}") y, sin más, la descripción también se
        # construían únicamente a partir del rango de fechas — si dos nodos
        # DISTINTOS del cuadro (p. ej. dos series de fondos diferentes)
        # agregan al mismo rango exacto, Wikibase rechaza el segundo ítem por
        # conflicto de etiqueta+descripción duplicada (mismo mecanismo que ya
        # se resolvió para P_id_origen en 020_cargar_cuadro_clasificacion.py
        # con el sufijo "(ID: ...)" y para los puntos de acceso de
        # 030_transformar_registros.py con "(Id: ...)"). Aquí NO se reutiliza
        # el ítem encontrado en conflicto (a diferencia de
        # 040_inyectar_registros.py): cada nodo debe tener su propia entidad
        # Q_fecha — "nunca compartida entre nodos distintos aunque coincida
        # el rango" (ver cabecera del script) — así que la solución es
        # garantizar que el PAR label+descripción sea siempre único añadiendo
        # el id_local del nodo a la descripción.
        item.descriptions.set("es", f"Expresión cronológica de {label} ({id_local}) (rico:Date)"[:250])
        item.claims.add([Item(prop_nr=P_INSTANCIA_DE, value=Q_FECHA)], action_if_exists=ActionIfExists.APPEND_OR_REPLACE)
        item.claims.add([String(prop_nr=P_FECHA_EXPRESADA, value=label)], action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

        claims_tiempo = []
        claim_ini = construir_claim(P_FECHA_INICIO, "time", ini)
        claim_fin = construir_claim(P_FECHA_FINAL, "time", fin)
        if claim_ini:
            claims_tiempo.append(claim_ini)
        if claim_fin:
            claims_tiempo.append(claim_fin)
        if claims_tiempo:
            item.claims.add(claims_tiempo, action_if_exists=ActionIfExists.APPEND_OR_REPLACE)

        res = item.write()
        qid = res.id
    except Exception as e:
        progreso.aviso(f"   ❌ [FALLO] entidad fecha de agrupación '{id_local}' ({label}): {e}")
        return None

    registrar_entidad_compartida(clave, qid, "fecha_agrupacion", label, id_local)
    time.sleep(0.4)
    return qid


# ==========================================
# 5. DECLARACIÓN SOBRE EL ÍTEM Q_agrupacion_doc
# ==========================================
def _qid_de_claim_item(claims_existentes) -> Optional[str]:
    """Extrae el QID destino de una claim de tipo wikibase-item ya existente
    sobre el ítem (item.claims.get(pid) devuelve una lista). Se usa para
    recuperar, en una reejecución, el valor ya declarado en una pasada
    anterior y poder persistirlo en mapa_cuadro_wikibase.json aunque no se
    vuelva a escribir en Wikibase (ver más abajo)."""
    if not claims_existentes:
        return None
    try:
        valor = claims_existentes[0].mainsnak.datavalue.value
        return valor.get("id") if isinstance(valor, dict) else None
    except Exception:
        return None


def declarar_sobre_nodo(id_local: str, r: Dict[str, Any], progreso: Progreso) -> Tuple[str, str, Dict[str, Any]]:
    """Devuelve (estado, mensaje, metadata_cuadro). metadata_cuadro son las
    claves a retrocompletar en mapa_cuadro_wikibase.json[id_local]
    (q_fecha_agrupacion/fecha_inicio_agrupacion/fecha_final_agrupacion y/o
    q_lengua_agrupacion) — necesarias porque 080_exportar_rdf_ttl.py no
    consulta Wikibase en caliente y solo puede exportar lo que esté en ese
    fichero local."""
    metadata_cuadro: Dict[str, Any] = {}
    qid_nodo = r.get("qid")
    if not qid_nodo or not _RE_QID_REAL.match(str(qid_nodo)):
        return "omitido", f"{id_local}: sin QID real en el cuadro, se omite", metadata_cuadro

    pendientes = []  # (pid, ("fecha", ini, fin) | ("lengua", qid), descripcion)
    if r["fechas"]:
        ini, fin = r["fechas"]
        pendientes.append((P_FECHA_AGRUPACION, ("fecha", ini, fin), f"fechas {ini}—{fin}"))
    if r["lengua"] not in (None, HETEROGENEO):
        pendientes.append((P_LENGUA_AGRUPACION, ("lengua", r["lengua"]), f"lengua {r['lengua']}"))

    if not pendientes:
        return "sin_datos", f"{id_local}: sin fechas ni lengua homogénea, nada que declarar", metadata_cuadro

    if MODO_SIMULACION:
        return "simulado", f"{id_local} ({qid_nodo}) — simulado: " + ", ".join(p[2] for p in pendientes), metadata_cuadro

    try:
        item = wbi.item.get(qid_nodo)
    except Exception as e:
        return "fallido", f"{id_local} ({qid_nodo}): fallo al leer el ítem — {e}", metadata_cuadro

    claims_nuevas = []
    descripciones = []
    for pid, spec, desc in pendientes:
        claim_existente = item.claims.get(pid)
        if claim_existente:
            # Ya declarado en una ejecución anterior — no se duplica en
            # Wikibase, pero se recupera el QID real ya declarado para
            # sincronizar el fichero local si todavía no lo tuviera.
            qid_valor_existente = _qid_de_claim_item(claim_existente)
            if qid_valor_existente:
                if spec[0] == "fecha":
                    metadata_cuadro["q_fecha_agrupacion"] = qid_valor_existente
                    metadata_cuadro["fecha_inicio_agrupacion"] = spec[1]
                    metadata_cuadro["fecha_final_agrupacion"] = spec[2]
                else:
                    metadata_cuadro["q_lengua_agrupacion"] = qid_valor_existente
            continue
        if spec[0] == "fecha":
            _, ini, fin = spec
            q_fecha_agrup = obtener_o_crear_fecha_agrupacion(id_local, ini, fin, progreso)
            if not q_fecha_agrup:
                continue
            claims_nuevas.append(Item(prop_nr=pid, value=q_fecha_agrup))
            metadata_cuadro["q_fecha_agrupacion"] = q_fecha_agrup
            metadata_cuadro["fecha_inicio_agrupacion"] = ini
            metadata_cuadro["fecha_final_agrupacion"] = fin
        else:
            _, q_lengua = spec
            claims_nuevas.append(Item(prop_nr=pid, value=q_lengua))
            metadata_cuadro["q_lengua_agrupacion"] = q_lengua
        descripciones.append(desc)

    if not claims_nuevas:
        sufijo = " (metadatos locales sincronizados)" if metadata_cuadro else ""
        return "sin_cambios", f"{id_local} ({qid_nodo}): ya estaba completo{sufijo}", metadata_cuadro

    try:
        item.claims.add(claims_nuevas, action_if_exists=ActionIfExists.APPEND_OR_REPLACE)
        item.write()
    except Exception as e:
        return "fallido", f"{id_local} ({qid_nodo}): fallo al escribir — {e}", metadata_cuadro

    time.sleep(0.4)
    return "actualizado", f"{id_local} ({qid_nodo}): +{len(claims_nuevas)} propiedad(es) — " + ", ".join(descripciones), metadata_cuadro


# ==========================================
# 6. EJECUCIÓN PRINCIPAL
# ==========================================
if __name__ == "__main__":
    print("=== CÁLCULO DE FECHAS Y LENGUA AGREGADAS DEL CUADRO DE CLASIFICACIÓN ===")
    print(f"Modo de ejecución: {'SIMULACIÓN (Consola)' if MODO_SIMULACION else 'ESCRITURA REAL (Wikibase)'}")

    cuadro = cargar_json(MAPA_CUADRO_PATH)
    if not cuadro:
        raise SystemExit(f"[ERROR] {MAPA_CUADRO_PATH} vacío. Ejecute primero 020_cargar_cuadro_clasificacion.py.")

    registros = cargar_resueltos()
    if not registros:
        raise SystemExit(f"[ERROR] {FICHERO_RESUELTO_PATH} vacío. Ejecute primero 040_inyectar_registros.py.")

    progreso = Progreso(len(registros), etiqueta="documentos (validación de fechas)")
    series, subfondos, fondos = construir_arbol(cuadro)
    por_serie = calcular_por_serie(registros, progreso)
    progreso.limpiar()
    resultados = construir_resultados(series, subfondos, fondos, por_serie)

    progreso = Progreso(len(resultados), etiqueta="nodos del cuadro")
    contadores = {"actualizados": 0, "sin_cambios": 0, "sin_datos": 0, "simulados": 0, "omitidos": 0, "fallidos": 0}
    CLAVE_CONTADOR = {
        "actualizado": "actualizados", "sin_cambios": "sin_cambios", "sin_datos": "sin_datos",
        "simulado": "simulados", "omitido": "omitidos", "fallido": "fallidos",
    }

    cuadro_modificado = False
    for id_local, r in resultados.items():
        estado, mensaje, metadata_cuadro = declarar_sobre_nodo(id_local, r, progreso)
        contadores[CLAVE_CONTADOR[estado]] += 1

        if metadata_cuadro and id_local in cuadro and isinstance(cuadro[id_local], dict):
            for clave_meta, valor_meta in metadata_cuadro.items():
                if cuadro[id_local].get(clave_meta) != valor_meta:
                    cuadro[id_local][clave_meta] = valor_meta
                    cuadro_modificado = True

        if estado == "fallido":
            progreso.aviso(f"   ❌ {mensaje}")
            progreso.avanzar(f"{id_local}: FALLIDO")
        else:
            progreso.avanzar(mensaje)

    if cuadro_modificado:
        guardar_cuadro(cuadro)

    progreso.resumen(
        "RESUMEN DE CÁLCULO DE FECHAS Y LENGUA AGREGADAS",
        actualizados=contadores["actualizados"],
        sin_cambios=contadores["sin_cambios"],
        sin_datos=contadores["sin_datos"],
        simulados=contadores["simulados"],
        omitidos=contadores["omitidos"],
        fallidos=contadores["fallidos"],
    )
