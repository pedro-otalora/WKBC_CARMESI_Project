"""
generar_fichas_documentos.py
==============================
Genera y publica una página wiki (Template:FichaDocumento) por cada documento
ya inyectado en Wikibase, a partir de registros_inyectados_resuelto.jsonl —
la fuente de verdad ya trae resuelto todo lo necesario (QID del documento, de
sus entidades secundarias y de sus puntos de acceso), así que este script no
necesita cruzar varios ficheros como hacía la versión de Colab.

Requiere, en este orden: inyectar_ontologia.py, cargar_cuadro_clasificacion.py
e inyectar_registros.py ya ejecutados.
"""

import json
import os
import re
from typing import Any, Dict, List

from config_carmesi import LOAD_FILES_PATH, publicar_pagina_wiki
from progreso import Progreso

MAPA_CUADRO_PATH = os.path.join(LOAD_FILES_PATH, "mapa_cuadro_wikibase.json")
MAPA_ONTOLOGIA_PATH = os.path.join(LOAD_FILES_PATH, "mapa_ontologia_wikibase.json")
FICHERO_RESUELTO_PATH = os.path.join(LOAD_FILES_PATH, "registros_inyectados_resuelto.jsonl")
FICHERO_SALIDA_TXT = os.path.join(LOAD_FILES_PATH, "mapa_paginas_documentos_wikitext.txt")

MODULO_LUA = "CustomLabel"
PUBLICAR = True  # False = solo genera el .txt local, no publica ninguna página

# Parámetro de plantilla -> id_local de la propiedad real. Se resuelven una
# sola vez al arrancar, contra el mapa de ontología — nunca se hardcodea un
# "Pxx", porque el número real lo decide Wikibase al crear la propiedad.
CAMPOS_DIRECTOS_PID = {
    "p_codigo_ref": "P_codigo_ref",
    "p_titulo": "P_titulo",
    "p_fecha": "P_fecha",
    "p_productor": "P_productor",
    "p_alcance_contenido": "P_alcance_contenido",
    "p_condiciones_acceso": "P_condiciones_acceso",
    "p_lengua": "P_lengua",
    "p_nota": "P_nota",
    "p_instanciacion_fisica": "P_instanciacion_fisica",
    "p_volumen_soporte": "P_volumen_soporte",
    "p_tipo_escritura": "P_tipo_escritura",
    "p_estado_conservacion": "P_estado_conservacion",
}


def cargar_json(path: str, default: Any = None) -> Any:
    if default is None:
        default = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def cargar_resueltos() -> List[Dict[str, Any]]:
    registros = []
    if os.path.exists(FICHERO_RESUELTO_PATH):
        with open(FICHERO_RESUELTO_PATH, "r", encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if linea:
                    registros.append(json.loads(linea))
    return registros


def resolver_pids(ontologia: Dict[str, str]) -> Dict[str, str]:
    pids = {}
    faltantes = []
    for param, id_local in CAMPOS_DIRECTOS_PID.items():
        pid = ontologia.get(id_local)
        if not pid:
            faltantes.append(id_local)
        pids[param] = pid or ""
    if faltantes:
        print(f"❌ Faltan en el mapa de ontología: {faltantes}")
        print("   Ejecute inyectar_ontologia.py con la tabla P al día antes de generar las fichas.")
        return {}
    return pids


def generar_fichas_documentos() -> Dict[str, str]:
    """Devuelve {titulo_pagina: wikitexto} — una entrada por documento."""
    cuadro = cargar_json(MAPA_CUADRO_PATH)
    ontologia = cargar_json(MAPA_ONTOLOGIA_PATH)
    registros = cargar_resueltos()

    if not cuadro:
        print(f"❌ {MAPA_CUADRO_PATH} vacío. Ejecute cargar_cuadro_clasificacion.py.")
        return {}
    if not registros:
        print(f"❌ {FICHERO_RESUELTO_PATH} vacío. Ejecute inyectar_registros.py.")
        return {}

    pids = resolver_pids(ontologia)
    if not pids:
        return {}

    # Nivel de descripción (ISAD 1.4): sin propiedad RiC directa — RiC-CM lo
    # expresa mediante el propio tipo de entidad elegido (apartado 2.4.3 de
    # la documentación), no mediante texto libre. Se resuelve una sola vez
    # el QID de la CLASE Q_documento (rico:Record) desde la ontología —
    # nunca hardcodeado como "Q2" — y se informa igual en todas las fichas,
    # ya que todo documento generado por este script es, por definición, una
    # instancia de esa clase.
    q_clase_documento = ontologia.get("Q_documento")
    if not q_clase_documento:
        print("❌ Falta 'Q_documento' en el mapa de ontología. Ejecute inyectar_ontologia.py.")
        return {}

    qid_raiz = ""
    fondos_map: Dict[str, Any] = {}
    subfondos_map: Dict[str, Any] = {}
    series_map: Dict[str, Any] = {}

    for datos in cuadro.values():
        if not isinstance(datos, dict):
            continue
        tipo = datos.get("tipo")
        if tipo == "raiz":
            qid_raiz = datos.get("qid", "")
        elif tipo == "fondo":
            fondos_map[str(datos.get("fondo_id"))] = datos
        elif tipo == "subfondo":
            subfondos_map[str(datos.get("subfondo_id"))] = datos
        elif tipo == "serie" and datos.get("qid"):
            series_map[datos["qid"]] = datos

    incidencias = {"serie": 0, "fondo": 0, "subfondo": 0}
    paginas: Dict[str, str] = {}

    for reg in registros:
        id_doc = str(reg.get("id_documento", "")).strip()
        wb = reg.get("_wikibase") or {}
        q_documento = wb.get("qid_documento")
        if not id_doc or not q_documento:
            continue

        dir_d = reg.get("declaraciones_directas") or {}
        pendiente = reg.get("pendiente_sin_mapear") or {}

        # --- Clasificación: serie / subfondo / fondo / agrupación ---
        q_serie = str(dir_d.get("P_incluido_en", "")).strip()
        info_serie = series_map.get(q_serie, {}) if re.match(r"^Q\d+$", q_serie) else {}
        if not info_serie:
            incidencias["serie"] += 1

        serie_id_wiki = info_serie.get("serie_id", "")
        fondo_id = str(info_serie.get("fondo_id", ""))
        subfondo_id = str(info_serie.get("subfondo_id", ""))

        q_fondo = fondos_map.get(fondo_id, {}).get("qid", "")
        if not q_fondo:
            incidencias["fondo"] += 1

        q_subfondo = ""
        if subfondo_id:
            q_subfondo = subfondos_map.get(subfondo_id, {}).get("qid", "")
            if not q_subfondo:
                incidencias["subfondo"] += 1

        # --- Instanciaciones digitales: PDF / DjVu, casando por orden con sus QID ---
        pdf_url = djvu_url = ""
        originales_dig = (reg.get("Q_secundarios") or {}).get("instanciaciones_digitales") or []
        for datos_inst in originales_dig:
            mimetype = datos_inst.get("P_mimetype")
            url = datos_inst.get("P_url_acceso")
            if mimetype == "application/pdf":
                pdf_url = url or ""
            elif mimetype == "image/vnd.djvu":
                djvu_url = url or ""
        if not pdf_url and dir_d.get("P_url_acceso"):
            pdf_url = dir_d["P_url_acceso"]

        # --- Puntos de acceso, ya resueltos en la fuente de verdad ---
        listas_pa: Dict[str, List[str]] = {"personas": [], "instituciones": [], "materias": [], "lugares": []}
        for pa in wb.get("puntos_acceso") or []:
            cat = pa.get("categoria")
            qid_pa = pa.get("qid")
            if cat in listas_pa and qid_pa:
                enlace = f"{{{{#invoke:{MODULO_LUA}|label_Qlnk|{qid_pa}}}}}"
                if enlace not in listas_pa[cat]:
                    listas_pa[cat].append(enlace)

        def _vinetas(items: List[str]) -> str:
            return "\n".join(f"* {i}" for i in items)

        # --- Construcción de la plantilla ---
        lineas = [
            "{{FichaDocumento",
            f"| id = {id_doc}",
            f"| qdocumento = {q_documento}",
            f"| nivel = {q_clase_documento}",
        ]
        for param, pid in pids.items():
            lineas.append(f"| {param} = {pid}")

        if qid_raiz:
            lineas.append(f"| qagrupacion = {qid_raiz}")
        if q_fondo:
            lineas.append(f"| qfondo = {q_fondo}")
        if q_subfondo:
            lineas.append(f"| qsubfondo = {q_subfondo}")
        if q_serie and info_serie:
            lineas.append(f"| qserie = {q_serie}")
            lineas.append(f"| serie_id = {serie_id_wiki}")

        # Campos que NO tienen propiedad real en Wikibase: solo texto literal.
        # "nivel" ya no se alimenta aquí del texto libre ISAD 1.4 (pendiente
        # sin mapear): ver "nivel = {q_clase_documento}" más arriba.
        opcionales_literales = {
            "ingreso": pendiente.get("forma_ingreso_isad_24"),
            "originales": pendiente.get("localizacion_originales_isad_51"),
            "copias": pendiente.get("localizacion_copias_isad_52"),
            "relacionadas": pendiente.get("unidades_relacionadas_isad_53"),
            "publicaciones": pendiente.get("notas_publicaciones_isad_54"),
            "nota_archivero": pendiente.get("nota_archivero_isad_71"),
            "pdf": pdf_url,
            "djvu": djvu_url,
        }
        for param, valor in opcionales_literales.items():
            if valor:
                lineas.append(f"| {param} = {valor}")

        for param, valor in (
            ("personas", _vinetas(listas_pa["personas"])),
            ("instituciones", _vinetas(listas_pa["instituciones"])),
            ("materias", _vinetas(listas_pa["materias"])),
            ("lugares", _vinetas(listas_pa["lugares"])),
        ):
            if valor:
                lineas.append(f"| {param} = {valor}")

        lineas.append("}}")

        paginas[f"DOC-{id_doc}"] = "\n".join(lineas)

    if any(incidencias.values()):
        print(f"⚠️ Incidencias de resolución: {incidencias}")

    return paginas


def ejecutar_generacion_documentos() -> None:
    print("=== GENERACIÓN DE FICHAS DE DOCUMENTO ===")

    paginas = generar_fichas_documentos()
    if not paginas:
        print("❌ No se generó ninguna ficha.")
        return

    with open(FICHERO_SALIDA_TXT, "w", encoding="utf-8") as f:
        f.write("\n\n".join(paginas.values()))
    print(f"Fichero local con {len(paginas)} ficha(s) en: {FICHERO_SALIDA_TXT}")

    if not PUBLICAR:
        print("ℹ️ PUBLICAR = False — no se ha publicado nada en el wiki.")
        return

    resultados = {"creada": 0, "actualizada": 0, "sin_cambios": 0, "error": 0}
    p = Progreso(len(paginas), etiqueta="fichas de documento")

    for titulo, texto in paginas.items():
        estado = publicar_pagina_wiki(titulo, texto, resumen="Regeneración automática de ficha de documento")
        resultados[estado] = resultados.get(estado, 0) + 1
        if estado == "error":
            p.aviso(f"   ❌ Fallo al publicar '{titulo}'")
        p.avanzar(f"'{titulo}': {estado}")

    p.resumen(
        "RESUMEN DE PUBLICACIÓN",
        creadas=resultados["creada"],
        actualizadas=resultados["actualizada"],
        sin_cambios=resultados["sin_cambios"],
        fallidas=resultados["error"],
    )


if __name__ == "__main__":
    ejecutar_generacion_documentos()
