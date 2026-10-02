"""
transformar_registros.py
=========================
Lee los ficheros FONDO_*.json exportados del sistema de origen, los normaliza
y produce mapa_items_inyectar_wikibase.json: un dataset "listo para inyectar"
que el script de carga de registros consumirá para escribir en Wikibase.

No escribe nada en Wikibase — es puramente una fase de transformación/QA,
por eso no importa `wbi` de config_carmesi, solo las rutas del proyecto.
"""

import os
import re
import json
import fnmatch
import unicodedata
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, List, Optional, Tuple

from config_carmesi import LOAD_FILES_PATH, EXTRACT_FILES_FONS_PATH
from progreso import Progreso

_progreso: Progreso = None  # se inicializa en generar_dataset_preparado()

# -----------------------------------------------------------------------------
# 1. CARGA DE CONFIGURACIÓN Y MAPEOS PREEXISTENTES
# -----------------------------------------------------------------------------
MAPA_ONTOLOGIA_PATH = os.path.join(LOAD_FILES_PATH, 'mapa_ontologia_wikibase.json')
MAPA_CUADRO_PATH = os.path.join(LOAD_FILES_PATH, 'mapa_cuadro_wikibase.json')
FICHERO_SALIDA = os.path.join(LOAD_FILES_PATH, 'mapa_items_inyectar_wikibase.json')

def cargar_json_seguro(path: str) -> Dict[str, Any]:
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Aviso al cargar {path}: {e}. Se usará un diccionario vacío.")
    return {}

# Cargamos ambas fuentes de verdad
ontologia = cargar_json_seguro(MAPA_ONTOLOGIA_PATH)
mapa_cuadro = cargar_json_seguro(MAPA_CUADRO_PATH)

# -----------------------------------------------------------------------------
# 2. GENERADOR DE ID SINTÉTICO Y LIMPIEZA DE TEXTO
# -----------------------------------------------------------------------------
def generar_clave_productor(nombre: str) -> str:
    """
    Genera una clave sintética determinista para productores sin ID de origen.
    Ejemplo: "Concejo de Murcia." -> "PRODUCTOR_concejo_de_murcia"
    """
    if not nombre:
        return "PRODUCTOR_DESCONOCIDO"

    texto = unicodedata.normalize('NFKD', nombre).encode('ASCII', 'ignore').decode('utf-8')
    slug = re.sub(r'[^a-z0-9]+', '_', texto.lower()).strip('_')

    return f"PRODUCTOR_{slug}"

def generar_clave_fecha(texto_fecha: str) -> str:
    """
    Genera una clave sintética determinista para una fecha expresada, para que
    dos documentos con la misma expresión de fecha (p. ej. "1500 (probable)")
    reutilicen la misma entidad Q_fecha en vez de crear una por cada uno
    (evita además el conflicto de etiqueta duplicada en Wikibase).
    """
    if not texto_fecha:
        return "FECHA_SIN_EXPRESAR"

    texto = unicodedata.normalize('NFKD', texto_fecha).encode('ASCII', 'ignore').decode('utf-8')
    slug = re.sub(r'[^a-z0-9]+', '_', texto.lower()).strip('_')

    return f"FECHA_{slug}"

def sanitizar_texto_wikibase(val: Any, max_len: int = 399) -> Optional[str]:
    """Limpia caracteres de control, normaliza Unicode, recorta y devuelve None si está vacío."""
    if val is None:
        return None

    if isinstance(val, (list, tuple)):
        elementos_limpios = [sanitizar_texto_wikibase(x, max_len) for x in val if x]
        if not elementos_limpios:
            return None
        s_unida = " ".join(elementos_limpios)
        resultado = s_unida[:max_len].strip()
        return resultado if resultado else None

    s = str(val)
    s = unicodedata.normalize('NFC', s)
    s = "".join(' ' if unicodedata.category(ch) in ('Cc', 'Zl', 'Zp') else ch for ch in s)
    s = re.sub(r'\s+', ' ', s)

    resultado = s[:max_len].strip()
    return resultado if resultado else None

def limpiar_texto(texto: Optional[str]) -> Optional[str]:
    """Limpia cadenas con saltos de línea (\\r, \\n), tabuladores (\\t) y múltiples espacios."""
    if not texto or not isinstance(texto, str):
        return texto

    limpio = re.sub(r'[\r\n\t]+', ' ', texto)
    limpio = re.sub(r'\s*;\s*', '; ', limpio)
    limpio = re.sub(r'\s+', ' ', limpio)

    return limpio.strip()

# -----------------------------------------------------------------------------
# 3. NORMALIZADOR DE DETALLE ISAD(G)
# -----------------------------------------------------------------------------
def extraer_isad_normalizado(isad_raw: Any) -> Dict[str, str]:
    normalizado = {}
    if not isad_raw or not isinstance(isad_raw, dict):
        return normalizado

    patrones = {
        'isad_11': r'^1\.1',
        'isad_12': r'^1\.2',
        'isad_13': r'^1\.3',
        'isad_14': r'^1\.4',  # Nivel de descripción
        'isad_15': r'^1\.5',
        'isad_21': r'^2\.1',
        'isad_24': r'^2\.4',  # Forma de ingreso
        'isad_31': r'^3\.1',
        'isad_41': r'^4\.1',
        'isad_43': r'^4\.3',  # Lengua / escritura
        'isad_44': r'^4\.4',  # Características físicas y requisitos técnicos
        'isad_51': r'^5\.1',  # Localización de originales
        'isad_52': r'^5\.2',  # Localización de copias
        'isad_53': r'^5\.3',  # Unidades de descripción relacionadas
        'isad_54': r'^5\.4',  # Notas de publicaciones
        'isad_61': r'^6\.1',  # Notas
        'isad_71': r'^7\.1',
    }

    for raw_key, raw_val in isad_raw.items():
        if not raw_val:
            continue

        clean_key = re.sub(r'[\r\n\t]+', ' ', str(raw_key)).strip()
        clean_key = re.sub(r'\s+', ' ', clean_key)

        match_found = False
        for std_key, regex in patrones.items():
            if re.search(regex, clean_key):
                # Todos los campos, incluido isad_21 (productor), se normalizan igual:
                # saltos de línea y tabuladores se colapsan a un único espacio, de modo
                # que el motor de preparación siempre reciba una sola cadena limpia.
                val_str = limpiar_texto(str(raw_val)) if isinstance(raw_val, str) else raw_val
                normalizado[std_key] = val_str
                match_found = True
                break

        if not match_found:
            normalizado[clean_key] = limpiar_texto(str(raw_val)) if isinstance(raw_val, str) else raw_val

    return normalizado

# -----------------------------------------------------------------------------
# 4. HELPERS DE PARSEO (URL Y FECHAS)
# -----------------------------------------------------------------------------
def extraer_id_de_url(url: str) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    return params['id'][0] if 'id' in params else None

SIGLOS_MAP = {
    'XIII': ('1201', '1300'),
    'XIV':  ('1301', '1400'),
    'XV':   ('1401', '1500'),
    'XVI':  ('1501', '1600'),
    'XVII': ('1601', '1700'),
    'XVIII':('1701', '1800'),
    'XIX':  ('1801', '1900'),
    'XX':   ('1901', '2000'),
}

def extraer_fecha_singular(texto: str) -> Optional[str]:
    """Extrae y normaliza una única expresión de fecha a ISO 8601."""
    if not texto:
        return None

    match_iso = re.search(r'\b(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b', texto)
    if match_iso:
        yyyy, mm, dd = match_iso.groups()
        if mm == '00' and dd == '00':
            return yyyy
        elif dd == '00':
            return f"{yyyy}-{mm}"
        return f"{yyyy}-{mm}-{dd}"

    # Formato "DD-MM-AAAA" (o variantes de 1-2 dígitos). La versión anterior
    # de este patrón usaba rangos de dígitos sueltos ([0-3]?\d para el día,
    # 0?\d|1[0-2] para el mes) que aceptaban sintácticamente valores
    # imposibles — día 36, mes 00 — y los propagaba tal cual a ISO 8601
    # (p. ej. "1950-10-36" o "2026-00-01"), fechas que wb_claims.py descarta
    # silenciosamente al inyectar (con el consiguiente P_fecha_inicio/
    # P_fecha_final vacío) en vez de hacer fallar la carga. Se captura ahora
    # con un patrón laxo y se valida el rango real (día 1-31, mes 1-12) antes
    # de aceptar la lectura; si no es válida, se descarta sin devolver nada
    # y la función sigue probando los patrones más permisivos de abajo
    # (año-mes, año suelto) en vez de forzar una fecha inventada.
    match_es = re.search(r'\b(\d{1,2})-(\d{1,2})-(\d{4})\b', texto)
    if match_es:
        d_str, m_str, yyyy = match_es.groups()
        d, m = int(d_str), int(m_str)
        if 1 <= m <= 12 and 1 <= d <= 31:
            return f"{yyyy}-{m:02d}-{d:02d}"
        if _progreso is not None:
            _progreso.aviso(
                f"  ⚠️ Fecha con día/mes fuera de rango descartada: "
                f"'{d_str}-{m_str}-{yyyy}' (de '{texto.strip()}') — se intenta año-mes/año suelto"
            )

    match_ym = re.search(r'\b(\d{4})-(0[1-9]|1[0-2])\b', texto)
    if match_ym:
        return match_ym.group(0)

    match_y = re.search(r'\b(1\d{3}|20\d{2})\b', texto)
    if match_y:
        return match_y.group(0)

    return None

def parsear_fechas_isad(texto_fecha: str) -> Tuple[Optional[str], Optional[str]]:
    """Parsea cadenas de ISAD(G) 1.3 devolviendo (fecha_inicio, fecha_fin)."""
    if not texto_fecha or not isinstance(texto_fecha, str):
        return None, None

    cadena = texto_fecha.strip()

    match_siglo = re.search(r'\b(?:S\.|SIGLO)\s*([IVXLCDM]+)\b', cadena, re.IGNORECASE)
    if match_siglo:
        romano = match_siglo.group(1).upper()
        if romano in SIGLOS_MAP:
            return SIGLOS_MAP[romano]

    if '/' in cadena:
        partes = cadena.split('/')
        f_ini = extraer_fecha_singular(partes[0])
        f_fin = extraer_fecha_singular(partes[1])
        if f_ini and not f_fin:
            f_fin = f_ini
        return f_ini, f_fin

    f_unica = extraer_fecha_singular(cadena)
    if f_unica:
        return f_unica, f_unica

    return None, None

# -----------------------------------------------------------------------------
# 4b. NORMALIZADOR DE LENGUA / ESCRITURA (ISAD 4.3)
# -----------------------------------------------------------------------------
# El campo de origen mezcla dos ejes en un mismo texto, p.ej.:
#   "Castellano; Cortesana" / "Castellano , Valenciano; Cortesana , Gótica aragonesa"
#   "Cortesana" (sin lengua explícita) / "Castellano; Albalaes" (tipo documental, ignorado)
# Vocabulario cerrado — id_local tal como se crearon en 1_creacion_Q.tsv.

LENGUA_MAP = {
    "castellano": "Q_castellano",
    "valenciano": "Q_valenciano",
    "latin": "Q_latin",
}

ESCRITURA_MAP = {
    "cortesana": "Q_escritura_cortesana",
    "precortesana": "Q_escritura_precortesana",
    "procesal": "Q_escritura_procesal",
    "gotica semicursiva": "Q_escritura_gotica_semicursiva",
    "gotica aragonesa": "Q_escritura_gotica_aragonesa",
    "humanistica": "Q_escritura_humanistica",
    "bastarda": "Q_escritura_bastarda",
    "hibrida": "Q_escritura_hibrida",
    "impresa": "Q_escritura_impresa",
    "caligrafia de la epoca a partir del siglo xviii": "Q_escritura_caligrafia_sXVIII",
}

# Tipos documentales detectados en el mismo campo pero fuera de alcance por ahora
# (ver P_tipo_documental, pendiente de modelar) — se ignoran silenciosamente, no
# cuentan como "no reconocidos".
TOKENS_TIPO_DOCUMENTAL_IGNORADOS = {"albalaes", "privilegios"}

def _clave_normalizada(texto: str) -> str:
    """minúsculas, sin tildes, espacios colapsados — para comparar contra los diccionarios."""
    t = unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('utf-8')
    return re.sub(r'\s+', ' ', t.strip().lower())

def parsear_lengua_escritura(texto_isad_43: Optional[str]) -> Tuple[List[str], List[str], List[str]]:
    """Devuelve (lenguas_q, escrituras_q, tokens_no_reconocidos).

    Con ';': el lado izquierdo SOLO puede ser lengua, el derecho SOLO escritura
    (así "Castellano; Albalaes" descarta "Albalaes" sin contaminar la lengua).
    Sin ';': el campo es una lista suelta de tokens que pueden ser lengua O
    escritura (p. ej. "Latín" o "Valenciano" sueltos, sin escritura asociada) —
    cada token se comprueba contra ambos vocabularios.
    """
    lenguas_q: List[str] = []
    escrituras_q: List[str] = []
    no_reconocidos: List[str] = []

    if not texto_isad_43:
        return lenguas_q, escrituras_q, no_reconocidos

    def _anadir_lengua(token: str) -> bool:
        clave = _clave_normalizada(token)
        if clave in LENGUA_MAP:
            q = LENGUA_MAP[clave]
            if q not in lenguas_q:
                lenguas_q.append(q)
            return True
        return False

    def _anadir_escritura(token: str) -> bool:
        clave = _clave_normalizada(token)
        if clave in ESCRITURA_MAP:
            q = ESCRITURA_MAP[clave]
            if q not in escrituras_q:
                escrituras_q.append(q)
            return True
        if clave in TOKENS_TIPO_DOCUMENTAL_IGNORADOS:
            return True  # reconocido, pero fuera de alcance: se descarta sin aviso
        return False

    texto = texto_isad_43.strip()

    if ';' in texto:
        parte_lengua, parte_resto = texto.split(';', 1)

        for token in parte_lengua.split(','):
            if not token.strip():
                continue
            if not _anadir_lengua(token):
                no_reconocidos.append(token.strip())

        for token in parte_resto.split(','):
            if not token.strip():
                continue
            if not _anadir_escritura(token):
                no_reconocidos.append(token.strip())
    else:
        # Sin ';': cada token puede ser lengua o escritura (p. ej. "Latín" solo)
        for token in texto.split(','):
            if not token.strip():
                continue
            if _anadir_lengua(token):
                continue
            if _anadir_escritura(token):
                continue
            no_reconocidos.append(token.strip())

    return lenguas_q, escrituras_q, no_reconocidos

# -----------------------------------------------------------------------------
# 5. MOTOR DE PREPARACIÓN DE REGISTROS
# -----------------------------------------------------------------------------
def simular_procesamiento_registro(reg: Dict[str, Any], mapa: Dict[str, Any], json_root_data: Dict[str, Any]) -> Dict[str, Any]:
    id_doc = str(reg.get('id_documento') or '')

    detalle_isad = reg.get('detalle_isad_g') or {}
    isad = extraer_isad_normalizado(detalle_isad.get('isad_g'))

    titulo_doc = sanitizar_texto_wikibase(isad.get('isad_12') or reg.get('titulo_nivel2') or 'Sin título', 399)
    fechas_doc = limpiar_texto(isad.get('isad_13') or reg.get('fecha_nivel2') or '')
    if fechas_doc:
        fechas_doc = fechas_doc.rstrip('.')

    label_es = sanitizar_texto_wikibase(titulo_doc, 249)

    serie_info = reg.get('serie_info') or {}
    serie_id = str(serie_info.get('serie_id') or '')
    nombre_serie = limpiar_texto(serie_info.get('nombre_serie') or '')

    # --- RESOLUCIÓN ROBUSTA DE LA SERIE DESDE EL MAPA_CUADRO ---
    clave_serie_plana = f"SERIE_{serie_id}" if serie_id else ""
    elemento_serie = mapa_cuadro.get(clave_serie_plana) or mapa_cuadro.get(serie_id)

    if isinstance(elemento_serie, dict):
        q_serie = elemento_serie.get("qid")
    else:
        q_serie = elemento_serie or (f"PENDIENTE_SERIE_{serie_id}" if serie_id else "SERIE_NO_DEFINIDA")

    siglas_archivo = limpiar_texto(reg.get('sigla_fondo') or json_root_data.get('fondo') or '')

    contexto_archivo = f" · {siglas_archivo}" if siglas_archivo else ""
    rango_fechas = f" ({fechas_doc})" if fechas_doc else ""
    serie_str = nombre_serie or q_serie or "Serie no especificada"

    desc_es = sanitizar_texto_wikibase(f"Documento de la serie {serie_str}{contexto_archivo}{rango_fechas} (id: {id_doc}) (rico:Record)", 249)

    # --- PUNTOS DE ACCESO (institución / persona / materia / lugar) ---
    puntos_acceso_simulados = []
    pas_config = [
        ('instituciones', 'P_institucion', 'Q_institucion'),
        ('personas', 'P_persona', 'Q_persona'),
        ('materias', 'P_materia', 'Q_materia'),
        ('lugares', 'P_lugar', 'Q_lugar')
    ]

    DESC_TEMPLATES = {
        'instituciones': "Institución o entidad relacionada con el fondo documental",
        'personas': "Persona relacionada con el fondo documental",
        'materias': "Concepto o materia temática de la documentación",
        'lugares': "Lugar o ámbito geográfico relacionado"
    }

    ONTOLOGY_CLASSES = {
        'instituciones': "rico:CorporateBody",
        'personas': "rico:Person",
        'materias': "skos:Concept",
        'lugares': "rico:Place"
    }

    pa_data = detalle_isad.get('puntos_acceso') or {}
    for cat, p_claim, q_instance in pas_config:
        items = pa_data.get(cat, [])
        for item in items:
            if isinstance(item, dict):
                pa_id = extraer_id_de_url(item.get('url'))
                pa_label = sanitizar_texto_wikibase(item.get('nombre'), 249)
                clave_mapa = f"PA_{cat.upper()}_{pa_id}" if pa_id else None
                q_res = mapa.get(clave_mapa) if clave_mapa else None

                base_desc = DESC_TEMPLATES.get(cat, "Punto de acceso documental")
                id_str = f" (Id: {pa_id})" if pa_id else ""
                class_str = f" ({ONTOLOGY_CLASSES.get(cat, 'rico:Instantiation')})"
                pa_desc = sanitizar_texto_wikibase(f"{base_desc}{id_str}{class_str}", 249)

                puntos_acceso_simulados.append({
                    "categoria": cat,
                    "propiedad_registro": p_claim,
                    "instancia_de": q_instance,
                    "label_es": pa_label,
                    "desc_es": pa_desc,
                    "P_id_origen": pa_id,
                    "clave_mapa": clave_mapa,
                    "q_resolucion": q_res or (f"PENDIENTE_{clave_mapa}" if clave_mapa else "SIN_ID_ORIGEN"),
                    "url": item.get('url')
                })

    # --- PRODUCTOR (ISAD 2.1) — un único agente por registro ---
    # isad_21 ya llega limpio (sin \r\n\t) desde extraer_isad_normalizado; se trata
    # siempre como una sola cadena, nunca se divide en líneas/productores múltiples.
    if 'isad_21' in isad:
        campo_productor = isad['isad_21']

        if isinstance(campo_productor, list):
            nombre_productor = sanitizar_texto_wikibase(" ".join(str(x) for x in campo_productor if x), 249)
        else:
            nombre_productor = sanitizar_texto_wikibase(str(campo_productor), 249)

        if nombre_productor:
            clave_productor = generar_clave_productor(nombre_productor)
            q_productor = mapa.get(clave_productor) or f"PENDIENTE_{clave_productor}"

            puntos_acceso_simulados.append({
                "categoria": "productores",
                "propiedad_registro": "P_productor",
                "instancia_de": "Q_institucion",
                "label_es": nombre_productor,
                "desc_es": sanitizar_texto_wikibase(f"Agente productor de la documentación ({siglas_archivo}) (rico:CorporateBody)", 249),
                "clave_mapa": clave_productor,
                "P_id_origen": clave_productor,
                "q_resolucion": q_productor
            })

    # --- INSTANCIACIONES DIGITALES: una entidad Q_instanciacion por formato ---
    # (sin calificador de mimetype: cada formato es una instanciación propia,
    # con su propio P_url_acceso + P_mimetype como declaraciones planas)
    instanciaciones_digitales = []
    url_pdf = detalle_isad.get('url_pdf')
    url_djvu = detalle_isad.get('url_djvu')

    if url_pdf:
        instanciaciones_digitales.append({
            "label_es": sanitizar_texto_wikibase(f"Instanciación digital (PDF) del documento {id_doc} - {titulo_doc}", 249),
            "desc_es": sanitizar_texto_wikibase(f"Copia digital en formato PDF del documento id {id_doc} (rico:Instantiation)", 249),
            "P_instancia_de": "Q_instanciacion",
            "P_url_acceso": url_pdf,
            "P_mimetype": "application/pdf"
        })
    if url_djvu:
        instanciaciones_digitales.append({
            "label_es": sanitizar_texto_wikibase(f"Instanciación digital (DjVu) del documento {id_doc} - {titulo_doc}", 249),
            "desc_es": sanitizar_texto_wikibase(f"Copia digital en formato DjVu del documento id {id_doc} (rico:Instantiation)", 249),
            "P_instancia_de": "Q_instanciacion",
            "P_url_acceso": url_djvu,
            "P_mimetype": "image/vnd.djvu"
        })

    # --- LENGUA / ESCRITURA (ISAD 4.3) — normalizadas contra el vocabulario cerrado ---
    lenguas_q, escrituras_q, tokens_no_reconocidos = parsear_lengua_escritura(isad.get('isad_43'))
    avisos = []
    if tokens_no_reconocidos:
        aviso = f"Documento {id_doc}: términos de lengua/escritura no reconocidos: {tokens_no_reconocidos}"
        avisos.append(aviso)
        if _progreso is not None:
            _progreso.aviso(f"  ⚠️ {aviso}")
        else:
            print(f"  ⚠️ {aviso}")

    # --- INSTANCIACIÓN FÍSICA ---
    volumen = sanitizar_texto_wikibase(isad.get('isad_15'), 399)
    conservacion = sanitizar_texto_wikibase(isad.get('isad_44'), 399)
    instanciacion_fisica = None
    if volumen or conservacion or escrituras_q:
        detalles_soporte = f" ({volumen})" if volumen else ""
        instanciacion_fisica = {
            "label_es": sanitizar_texto_wikibase(f"Instanciación física del documento {id_doc} - {titulo_doc}", 249),
            "desc_es": sanitizar_texto_wikibase(f"Soporte analógico o expediente físico del documento id {id_doc}{detalles_soporte} (rico:Instantiation)", 249),
            "P_instancia_de": "Q_instanciacion",
            "P_volumen_soporte": volumen,
            "P_estado_conservacion": conservacion,
            "P_tipo_escritura": escrituras_q,  # lista: 0, 1 o varias Q_escritura_*
        }

    # --- ENTIDAD FECHA ---
    q_fecha = None
    if fechas_doc:
        f_ini, f_fin = parsear_fechas_isad(fechas_doc)

        if f_ini and f_fin and f_ini != f_fin:
            info_rango = f"Expresión cronológica de {f_ini} a {f_fin}"
        elif f_ini:
            info_rango = f"Expresión cronológica de {f_ini}"
        else:
            info_rango = f"Expresión cronológica textual: {fechas_doc}"

        q_fecha = {
            "label_es": sanitizar_texto_wikibase(fechas_doc, 249),
            "desc_es": sanitizar_texto_wikibase(f"{info_rango} (rico:Date)", 249),
            "P_instancia_de": "Q_fecha",
            "clave_mapa": generar_clave_fecha(fechas_doc),
            "P_fecha_expresada": sanitizar_texto_wikibase(fechas_doc, 399),  # rico:expressedDate
            "P_fecha_inicio": f_ini,
            "P_fecha_final": f_fin
        }

    # Campos ISAD(G) recogidos pero sin propiedad RiC-O asignada todavía (áreas 2.4,
    # 3.5 y 3.6, y la nota general). Se conservan aquí para no perder el dato, pero
    # NO se envían como declaraciones — el script de inyección debe ignorar este bloque.
    pendiente_sin_mapear = {
        "nivel_descripcion_isad_14": sanitizar_texto_wikibase(isad.get('isad_14'), 399),
        "forma_ingreso_isad_24": sanitizar_texto_wikibase(isad.get('isad_24'), 399),
        "localizacion_originales_isad_51": sanitizar_texto_wikibase(isad.get('isad_51'), 399),
        "localizacion_copias_isad_52": sanitizar_texto_wikibase(isad.get('isad_52'), 399),
        "unidades_relacionadas_isad_53": sanitizar_texto_wikibase(isad.get('isad_53'), 399),
        "notas_publicaciones_isad_54": sanitizar_texto_wikibase(isad.get('isad_54'), 399),
        "nota_archivero_isad_71": sanitizar_texto_wikibase(isad.get('isad_71'), 399),
    }
    pendiente_sin_mapear = {k: v for k, v in pendiente_sin_mapear.items() if v}

    return {
        "status": "READY_TO_INJECT",
        "id_documento": id_doc,
        "label_es": label_es,
        "desc_es": desc_es,
        "declaraciones_directas": {
            "P_instancia_de": "Q_documento",
            "P_incluido_en": q_serie,
            "P_codigo_ref": sanitizar_texto_wikibase(isad.get('isad_11') or reg.get('signatura_nivel2'), 399),
            "P_titulo": titulo_doc,
            "P_alcance_contenido": sanitizar_texto_wikibase(isad.get('isad_31'), 399),
            "P_condiciones_acceso": sanitizar_texto_wikibase(isad.get('isad_41'), 399),
            "P_lengua": lenguas_q,  # lista: 0, 1 o varias Q_castellano/Q_valenciano/Q_latin
            "P_url_acceso": detalle_isad.get('url_detalle'),
            "P_nota": sanitizar_texto_wikibase(isad.get('isad_61'), 399),
        },
        "puntos_acceso": puntos_acceso_simulados,
        "Q_secundarios": {
            "instanciaciones_digitales": instanciaciones_digitales,
            "instanciacion_fisica": instanciacion_fisica,
            "entidad_fecha": q_fecha
        },
        "pendiente_sin_mapear": pendiente_sin_mapear,
        "avisos": avisos,
    }

# -----------------------------------------------------------------------------
# 6. ORQUESTADOR PRINCIPAL DE PARSEO Y TRANSFORMACIÓN
# -----------------------------------------------------------------------------
def generar_dataset_preparado(directorio_json: str = EXTRACT_FILES_FONS_PATH):
    global _progreso

    duplicados = []
    registros_listos = []
    total_avisos = 0
    errores_archivo = 0

    ids_vistos = set(mapa_cuadro.get('procesados', []))

    PATRON_FONDO = 'FONDO_*.json'

    print("=== PARSEO Y PREPARACIÓN DEL DATASET ===")
    print(f"Directorio de origen: {directorio_json}")

    # Primera pasada (barata: solo listar, no parsear) para conocer el total
    # de ficheros de fondo y poder mostrar un progreso [i/total] real.
    archivos_a_procesar = []
    for root, dirs, files in os.walk(directorio_json):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != 'sample_data']
        for file in fnmatch.filter(files, PATRON_FONDO):
            archivos_a_procesar.append(os.path.join(root, file))

    _progreso = Progreso(len(archivos_a_procesar), etiqueta="ficheros de fondo")

    for file_path in archivos_a_procesar:
        file = os.path.basename(file_path)
        _progreso.actualizar(f"Procesando {file}...")

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, dict) or 'registros' not in data:
                _progreso.avanzar(f"{file} (sin 'registros', omitido)")
                continue

            registros = data.get('registros', [])
            nuevos_en_fichero = 0

            for reg in registros:
                id_doc = str(reg.get('id_documento') or '')

                if id_doc in ids_vistos:
                    duplicados.append(id_doc)
                    continue

                res = simular_procesamiento_registro(reg, mapa_cuadro, data)
                total_avisos += len(res.get("avisos", []))

                registros_listos.append(res)
                ids_vistos.add(id_doc)
                nuevos_en_fichero += 1

            _progreso.avanzar(f"{file}: {nuevos_en_fichero} registro(s) nuevo(s)")

        except Exception as e:
            _progreso.aviso(f"  ❌ Error en archivo {file}: {e}")
            _progreso.avanzar()
            errores_archivo += 1

    with open(FICHERO_SALIDA, 'w', encoding='utf-8') as f:
        json.dump(registros_listos, f, ensure_ascii=False, indent=2)

    _progreso.resumen(
        "RESUMEN DE EJECUCIÓN",
        registros_preparados=len(registros_listos),
        omitidos_por_duplicidad=len(duplicados),
        avisos_lengua_escritura=total_avisos,
        ficheros_con_error=errores_archivo,
    )
    print(f"   Archivo generado: '{FICHERO_SALIDA}'")

if __name__ == '__main__':
    generar_dataset_preparado()
