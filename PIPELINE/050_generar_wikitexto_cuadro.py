"""
generar_wikitexto_cuadro.py
=============================
Genera el wikitexto del cuadro de clasificación (jerarquía Fondo > Subfondo >
Serie) a partir de mapa_cuadro_wikibase.json, listo para pegar en la página
wiki correspondiente.

No escribe en Wikibase — es una fase de generación puramente local, igual que
transformar_registros.py. El wikitexto emitido usa invocaciones a un módulo
Lua/Scribunto del wiki ({{#invoke:CustomLabel|label_Q|<QID>}}) que MediaWiki
evalúa al renderizar la página; este script no ejecuta ni necesita ese módulo,
solo produce el texto que lo invoca.
"""

import json
import os
from typing import Any, Dict, List

from config_carmesi import LOAD_FILES_PATH, publicar_pagina_wiki

MAPA_CUADRO_PATH = os.path.join(LOAD_FILES_PATH, 'mapa_cuadro_wikibase.json')
FICHERO_SALIDA_TXT = os.path.join(LOAD_FILES_PATH, 'mapa_pagina_cuadro_wikitext.txt')
TITULO_PAGINA = "CUADRO_CLASIFICACION"


def cargar_json(path: str) -> Dict[str, Any]:
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def _clave_orden(valor: Any) -> tuple:
    """Clave de ordenación segura para IDs mixtos (numéricos y no numéricos,
    p. ej. '12' junto a 'JS3'). Devolver siempre una tupla del mismo tipo
    evita el TypeError de sorted() al comparar int con str directamente:
    los numéricos se ordenan primero y entre sí por valor; el resto, después,
    alfabéticamente."""
    valor_str = str(valor) if valor is not None else ""
    if valor_str.isdigit():
        return (0, int(valor_str), "")
    return (1, 0, valor_str)


# =============================================================================
# 2. CONSTRUCTOR DE LA JERARQUÍA DEL CUADRO
# =============================================================================
def generar_wikitexto_cuadro(mapa_cuadro: Dict[str, Any]) -> str:
    """
    Procesa el mapa de cuadro basándose estrictamente en su estructura de
    raíz, fondos, subfondos y series con sus respectivos IDs.
    """
    lineas_wikitexto = []

    lineas_wikitexto.append("== Cuadro de Clasificación ==")
    lineas_wikitexto.append("")

    # 1. Identificar elemento raíz
    raiz_qid = None
    for clave, datos in mapa_cuadro.items():
        if isinstance(datos, dict) and datos.get("tipo") == "raiz":
            raiz_qid = datos.get("qid")
            break

    if raiz_qid:
        lineas_wikitexto.append(f"* <big>'''{{{{#invoke:CustomLabel|label_Q|{raiz_qid}}}}} ([[Item:{raiz_qid}]])'''</big>")

    # 2. Organizar elementos por fondo_id
    fondos_dict: Dict[str, Dict[str, Any]] = {}

    for clave, datos in mapa_cuadro.items():
        if not isinstance(datos, dict):
            continue

        tipo = datos.get("tipo")
        fondo_id = datos.get("fondo_id")
        qid = datos.get("qid")

        if not qid or tipo == "raiz":
            continue

        if tipo == "fondo":
            if fondo_id not in fondos_dict:
                fondos_dict[fondo_id] = {"fondo": datos, "subfondos": {}, "series_directas": []}
            else:
                fondos_dict[fondo_id]["fondo"] = datos

        elif tipo == "subfondo":
            subfondo_id = datos.get("subfondo_id")
            if fondo_id not in fondos_dict:
                fondos_dict[fondo_id] = {"fondo": None, "subfondos": {}, "series_directas": []}
            fondos_dict[fondo_id]["subfondos"][subfondo_id] = {"subfondo": datos, "series": []}

        elif tipo == "serie":
            subfondo_id = datos.get("subfondo_id")
            if fondo_id not in fondos_dict:
                fondos_dict[fondo_id] = {"fondo": None, "subfondos": {}, "series_directas": []}

            if subfondo_id:
                if subfondo_id not in fondos_dict[fondo_id]["subfondos"]:
                    fondos_dict[fondo_id]["subfondos"][subfondo_id] = {"subfondo": None, "series": []}
                fondos_dict[fondo_id]["subfondos"][subfondo_id]["series"].append(datos)
            else:
                fondos_dict[fondo_id]["series_directas"].append(datos)

    contador = {"fondos": 0, "subfondos": 0, "series": 0}

    # 3. Construir el wikitexto de manera ordenada y determinista
    for fondo_id in sorted(fondos_dict.keys(), key=_clave_orden):
        estructura_fondo = fondos_dict[fondo_id]
        datos_fondo = estructura_fondo["fondo"]

        if datos_fondo:
            q_fondo = datos_fondo.get("qid")
            prefijo_fondo = "**" if raiz_qid else "*"
            lineas_wikitexto.append(f"{prefijo_fondo} <big>{{{{#invoke:CustomLabel|label_Q|{q_fondo}}}}} ([[Item:{q_fondo}]])</big>")
            contador["fondos"] += 1
        elif estructura_fondo["subfondos"] or estructura_fondo["series_directas"]:
            print(f"   ⚠️ fondo_id '{fondo_id}' tiene subfondos/series pero no se encontró su propio ítem 'fondo' en el mapa")

        # Series que cuelgan directamente del fondo (sin subfondo)
        series_directas = sorted(estructura_fondo["series_directas"], key=lambda s: _clave_orden(s.get("serie_id")))
        for serie in series_directas:
            q_serie = serie.get("qid")
            serie_id = serie.get("serie_id")
            prefijo_serie = "***" if (raiz_qid and datos_fondo) else "**"
            lineas_wikitexto.append(f"{prefijo_serie} [[SR-{serie_id}|{{{{#invoke:CustomLabel|label_Q|{q_serie}}}}}]] ([[Item:{q_serie}]])")
            contador["series"] += 1

        # Subfondos y sus series
        subfondos_dict = estructura_fondo["subfondos"]
        for subfondo_id in sorted(subfondos_dict.keys(), key=_clave_orden):
            sub_info = subfondos_dict[subfondo_id]
            datos_sub = sub_info["subfondo"]

            if datos_sub:
                q_sub = datos_sub.get("qid")
                prefijo_sub = "***" if (raiz_qid and datos_fondo) else "**"
                lineas_wikitexto.append(f"{prefijo_sub} <big>{{{{#invoke:CustomLabel|label_Q|{q_sub}}}}} ([[Item:{q_sub}]])</big>")
                contador["subfondos"] += 1
            elif sub_info["series"]:
                print(f"   ⚠️ subfondo_id '{subfondo_id}' (fondo '{fondo_id}') tiene series pero no se encontró su propio ítem 'subfondo'")

            series_sub = sorted(sub_info["series"], key=lambda s: _clave_orden(s.get("serie_id")))
            for serie in series_sub:
                q_serie = serie.get("qid")
                serie_id = serie.get("serie_id")
                prefijo_serie_sub = "****" if (raiz_qid and datos_fondo) else "***"
                lineas_wikitexto.append(f"{prefijo_serie_sub} [[SR-{serie_id}|{{{{#invoke:CustomLabel|label_Q|{q_serie}}}}}]] ([[Item:{q_serie}]])")
                contador["series"] += 1

    print(f"   Renderizados: {contador['fondos']} fondo(s), {contador['subfondos']} subfondo(s), {contador['series']} serie(s)")
    return "\n".join(lineas_wikitexto)


# =============================================================================
# 3. ORQUESTADOR
# =============================================================================
def ejecutar_generacion_cuadro():
    print("=== GENERACIÓN DEL CUADRO DE CLASIFICACIÓN ===")

    mapa_cuadro = cargar_json(MAPA_CUADRO_PATH)
    if not mapa_cuadro:
        print(f"❌ El mapa de cuadro está vacío o no se pudo cargar: {MAPA_CUADRO_PATH}")
        print("   Ejecute primero cargar_cuadro_clasificacion.py.")
        return

    wikitexto_generado = generar_wikitexto_cuadro(mapa_cuadro)

    with open(FICHERO_SALIDA_TXT, 'w', encoding='utf-8') as f:
        f.write(wikitexto_generado)

    resultado = publicar_pagina_wiki(TITULO_PAGINA, wikitexto_generado, resumen="Regeneración automática del cuadro de clasificación")

    print("\n=== RESUMEN ===")
    print(f"   - Fichero local: {FICHERO_SALIDA_TXT}")
    print(f"   - Página '{TITULO_PAGINA}': {resultado}")
    if resultado not in ("creada", "actualizada", "sin_cambios"):
        print(f"   ⚠️ No se pudo publicar — el fichero local sigue disponible para copiarlo a mano")


if __name__ == '__main__':
    ejecutar_generacion_cuadro()
