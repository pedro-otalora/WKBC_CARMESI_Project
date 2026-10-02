"""
generar_fichas_series.py
==========================
Genera y publica una página wiki (Template:FichaSerie) por cada serie
documental que ya tenga documentos inyectados, con la lista de documentos
que le pertenecen.

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
FICHERO_SALIDA_TXT = os.path.join(LOAD_FILES_PATH, "mapa_series_wikitext.txt")

MODULO_LUA = "CustomLabel"
PUBLICAR = True  # False = solo genera el .txt local, no publica ninguna página


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


def _clave_orden(valor: Any) -> tuple:
    valor_str = str(valor) if valor is not None else ""
    if valor_str.isdigit():
        return (0, int(valor_str), "")
    return (1, 0, valor_str)


def generar_fichas_series() -> Dict[str, str]:
    """Devuelve {titulo_pagina: wikitexto} — una entrada por serie con documentos."""
    cuadro = cargar_json(MAPA_CUADRO_PATH)
    ontologia = cargar_json(MAPA_ONTOLOGIA_PATH)
    registros = cargar_resueltos()

    if not cuadro:
        print(f"❌ {MAPA_CUADRO_PATH} vacío. Ejecute cargar_cuadro_clasificacion.py.")
        return {}
    if not registros:
        print(f"❌ {FICHERO_RESUELTO_PATH} vacío. Ejecute inyectar_registros.py.")
        return {}

    p_titulo = ontologia.get("P_titulo")
    if not p_titulo:
        print("❌ 'P_titulo' no existe en el mapa de ontología. Ejecute inyectar_ontologia.py.")
        return {}

    # Áreas 2 (Contexto/Productor) y 3 (Contenido/Alcance) de la ficha —
    # inyectadas sobre fondo/subfondo/serie por cargar_cuadro_clasificacion.py.
    # Si faltan en la ontología no se aborta (la plantilla usa {{#if:}} y
    # simplemente omite esas áreas), pero se avisa para no publicar fichas
    # incompletas sin darse cuenta.
    p_productor = ontologia.get("P_productor")
    p_alcance_contenido = ontologia.get("P_alcance_contenido")
    if not p_productor or not p_alcance_contenido:
        print(
            "⚠️  'P_productor' y/o 'P_alcance_contenido' no están en el mapa de ontología — "
            "las fichas se generarán sin esas áreas."
        )

    # Área 1 (fecha agregada) y Área 4 (lengua agregada) — propiedades
    # RiC-O "MembersWith" declaradas sobre fondo/subfondo/serie por
    # calcular_fechas_lengua_agrupacion.py (045). Al igual que p_productor
    # y p_alcance_contenido, aquí solo se resuelve el PID: el valor real lo
    # lee la plantilla en vivo desde el ítem Wikibase mediante propiedad_Qlnk.
    p_fecha_agrupacion = ontologia.get("P_fecha_agrupacion")
    p_lengua_agrupacion = ontologia.get("P_lengua_agrupacion")
    if not p_fecha_agrupacion or not p_lengua_agrupacion:
        print(
            "⚠️  'P_fecha_agrupacion' y/o 'P_lengua_agrupacion' no están en el mapa de ontología — "
            "las fichas se generarán sin esas áreas."
        )

    # Nivel de descripción (ISAD 1.4): sin propiedad RiC directa — RiC-CM lo
    # expresa mediante el tipo de entidad/agrupación elegido. Esta función
    # genera solo fichas de SERIE, así que el QID a mostrar es siempre el
    # mismo: el de la clase Q_serie, que es además el valor que
    # cargar_cuadro_clasificacion.py declara en P_tipo_agrupacion_doc sobre
    # cada ítem serie (q_tipo_agrupacion=Q_SERIE). Se resuelve dinámicamente
    # desde la ontología, nunca hardcodeado.
    q_clase_serie = ontologia.get("Q_serie")
    if not q_clase_serie:
        print("❌ 'Q_serie' no existe en el mapa de ontología. Ejecute inyectar_ontologia.py.")
        return {}

    qid_raiz = ""
    fondos_map: Dict[str, Any] = {}
    subfondos_map: Dict[str, Any] = {}
    series_map: Dict[str, Any] = {}  # qid de la serie -> sus propios datos del cuadro

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

    # Serie (QID) -> lista de documentos {id_doc, q_doc}, directamente desde la
    # fuente de verdad: ya trae P_incluido_en resuelto a un QID real.
    serie_q_a_docs: Dict[str, List[Dict[str, str]]] = {}
    for reg in registros:
        id_doc = str(reg.get("id_documento", "")).strip()
        q_doc = (reg.get("_wikibase") or {}).get("qid_documento")
        q_serie = str((reg.get("declaraciones_directas") or {}).get("P_incluido_en", "")).strip()
        if not id_doc or not q_doc or not re.match(r"^Q\d+$", q_serie):
            continue
        serie_q_a_docs.setdefault(q_serie, []).append({"id_doc": id_doc, "q_doc": q_doc})

    paginas: Dict[str, str] = {}

    for q_serie, info_serie in sorted(series_map.items(), key=lambda kv: _clave_orden(kv[1].get("serie_id"))):
        serie_id = info_serie.get("serie_id", "")
        fondo_id = str(info_serie.get("fondo_id", ""))
        subfondo_id = str(info_serie.get("subfondo_id", ""))

        q_fondo = fondos_map.get(fondo_id, {}).get("qid", "")
        q_subfondo = subfondos_map.get(subfondo_id, {}).get("qid", "") if subfondo_id else ""

        lineas = ["{{FichaSerie"]
        lineas.append(f"| id = {serie_id}")
        lineas.append(f"| qserie = {q_serie}")
        lineas.append(f"| nivel = {q_clase_serie}")
        lineas.append(f"| p_titulo = {p_titulo}")
        if p_productor:
            lineas.append(f"| p_productor = {p_productor}")
        if p_alcance_contenido:
            lineas.append(f"| p_alcance_contenido = {p_alcance_contenido}")
        if p_fecha_agrupacion:
            lineas.append(f"| p_fecha_agrupacion = {p_fecha_agrupacion}")
        if p_lengua_agrupacion:
            lineas.append(f"| p_lengua_agrupacion = {p_lengua_agrupacion}")
        if qid_raiz:
            lineas.append(f"| agrupacion = {qid_raiz}")
        if q_fondo:
            lineas.append(f"| fondo = {q_fondo}")
        if q_subfondo:
            lineas.append(f"| subfondo = {q_subfondo}")
        lineas.append(f"| serie = {q_serie}")

        docs_serie = sorted(
            serie_q_a_docs.get(q_serie, []),
            key=lambda d: _clave_orden(d["id_doc"]),
        )
        if docs_serie:
            docs_fmt = [
                f"* [[DOC-{d['id_doc']}|{{{{#invoke:{MODULO_LUA}|label_Q_desc|{d['q_doc']}}}}}]] ([[Item:{d['q_doc']}]])"
                for d in docs_serie
            ]
            lineas.append("| documentos = " + "\n".join(docs_fmt))
        else:
            lineas.append("| documentos = ")

        lineas.append("}}")

        paginas[f"SR-{serie_id}"] = "\n".join(lineas)

    return paginas


def ejecutar_generacion_series() -> None:
    print("=== GENERACIÓN DE FICHAS DE SERIE ===")

    paginas = generar_fichas_series()
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
    p = Progreso(len(paginas), etiqueta="fichas de serie")

    for titulo, texto in paginas.items():
        estado = publicar_pagina_wiki(titulo, texto, resumen="Regeneración automática de ficha de serie")
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
    ejecutar_generacion_series()
