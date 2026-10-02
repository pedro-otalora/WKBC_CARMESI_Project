"""
000_publicar_plantillas.py
============================
Publica las plantillas y el módulo Lua desde TEMPLATES/ hacia sus páginas
correspondientes en el wiki. Ejecútelo cada vez que edite alguno de esos
ficheros localmente — gracias a la detección de cambios de
publicar_pagina_wiki(), solo genera una revisión nueva en las páginas cuyo
contenido haya cambiado de verdad.

No depende de ningún otro paso del pipeline (no toca la ontología ni los
registros), así que puede ejecutarse en cualquier momento.
"""

import os

from config_carmesi import TEMPLATES_PATH, publicar_pagina_wiki
from progreso import Progreso

# Fichero local (dentro de TEMPLATES_PATH) -> título de la página en el wiki.
# Ajuste esta tabla si renombra algún fichero o añade uno nuevo.
PLANTILLAS = {
    "Module_CustomLabel.lua": "Module:CustomLabel",
    "Template_FichaDocumento.wikitext": "Template:FichaDocumento",
    "Template_FichaSerie.wikitext": "Template:FichaSerie",
}


def _resolver_ruta_fichero(nombre_esperado: str, directorio: str) -> str:
    """Intenta localizar el fichero aunque el nombre real difiera ligeramente
    del esperado (p. ej. con un '.txt' añadido al descargarlo, o con otra
    combinación de mayúsculas/minúsculas) — evita un fallo por un detalle de
    nombre cuando el fichero sí está ahí."""
    ruta_exacta = os.path.join(directorio, nombre_esperado)
    if os.path.isfile(ruta_exacta):
        return ruta_exacta

    ruta_con_txt = os.path.join(directorio, nombre_esperado + ".txt")
    if os.path.isfile(ruta_con_txt):
        return ruta_con_txt

    if os.path.isdir(directorio):
        objetivo = nombre_esperado.lower()
        for real in os.listdir(directorio):
            if real.lower() == objetivo or real.lower().startswith(objetivo + "."):
                return os.path.join(directorio, real)

    return ruta_exacta  # no encontrado bajo ningún nombre candidato


def ejecutar_publicacion_plantillas() -> None:
    print("=== PUBLICACIÓN DE PLANTILLAS Y MÓDULO LUA ===")
    print(f"Origen: {TEMPLATES_PATH}")

    resultados = {"creada": 0, "actualizada": 0, "sin_cambios": 0, "error": 0}
    p = Progreso(len(PLANTILLAS), etiqueta="plantillas")

    for nombre_fichero, titulo_pagina in PLANTILLAS.items():
        ruta = _resolver_ruta_fichero(nombre_fichero, TEMPLATES_PATH)

        if not os.path.isfile(ruta):
            p.aviso(f"   ⚠️  No se encuentra '{nombre_fichero}' (ni variantes con .txt) — se omite '{titulo_pagina}'")
            if os.path.isdir(TEMPLATES_PATH):
                existentes = os.listdir(TEMPLATES_PATH)
                p.aviso(f"      Contenido real de {TEMPLATES_PATH}: {existentes}")
            else:
                p.aviso(f"      La carpeta {TEMPLATES_PATH} no existe.")
            resultados["error"] += 1
            p.avanzar(f"'{titulo_pagina}': no encontrada")
            continue

        if ruta != os.path.join(TEMPLATES_PATH, nombre_fichero):
            p.aviso(f"   ℹ️  Usando '{os.path.basename(ruta)}' para '{titulo_pagina}' (nombre esperado: '{nombre_fichero}')")

        with open(ruta, "r", encoding="utf-8") as f:
            contenido = f.read()

        estado = publicar_pagina_wiki(
            titulo_pagina,
            contenido,
            resumen=f"Actualización automática desde {nombre_fichero}",
        )
        resultados[estado] = resultados.get(estado, 0) + 1
        p.avanzar(f"'{titulo_pagina}' <- {nombre_fichero}: {estado}")

    p.resumen(
        "RESUMEN DE PUBLICACIÓN",
        creadas=resultados["creada"],
        actualizadas=resultados["actualizada"],
        sin_cambios=resultados["sin_cambios"],
        fallidas=resultados["error"],
    )


if __name__ == "__main__":
    ejecutar_publicacion_plantillas()
