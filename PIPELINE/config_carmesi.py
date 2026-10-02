"""
config_carmesi.py
==================
Módulo central de configuración del proyecto CARMESI (ISAD(G) -> RiC-O -> Wikibase).

Todos los demás scripts del pipeline (creación de Q, creación de P, inyección
de declaraciones, carga de registros documentales...) deben importar sus rutas
y su cliente de Wikibase desde aquí, en vez de repetir la configuración:

    from config_carmesi import wbi, LOAD_FILES_PATH, EXTRACT_FILES_PATH, MAP_FILE

Si cambia de instancia de Wikibase, de credenciales o de estructura de
carpetas, edite ÚNICAMENTE este fichero (o las variables de entorno / el
fichero .env que lee, ver más abajo) — ningún otro script debe tocarse.
"""

import os
import sys

# Directorio donde reside este propio fichero (config_carmesi.py), NO el
# directorio desde el que se invoque el script (que puede variar según cómo
# se ejecute: terminal, IDE, cron...). Todas las rutas del pipeline se anclan
# a este punto para que el proyecto sea portable sin tocar código.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Carga opcional de variables de entorno desde un fichero .env situado junto
# a este módulo. Si no tiene python-dotenv instalado, simplemente se ignora
# y se usan las variables de entorno ya definidas en el sistema.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
except ImportError:
    pass


# ==========================================
# 1. RUTAS DEL PIPELINE — único punto a editar
# ==========================================

# Carpeta raíz del proyecto: por defecto, la misma carpeta donde vive este
# script (config_carmesi.py) — así el proyecto es autocontenido y portable
# (basta con copiar la carpeta a otra máquina para que todo siga funcionando).
# Se puede sobrescribir con la variable de entorno CARMESI_PIPELINE_PATH si
# prefiere separar el código de los datos (p. ej. datos en un disco externo).
PIPELINE_PATH = os.environ.get("CARMESI_PIPELINE_PATH", SCRIPT_DIR)

LOAD_FILES_PATH = os.path.join(PIPELINE_PATH, "LOAD_FILES")
EXTRACT_FILES_PATH = os.path.join(PIPELINE_PATH, "EXTRACT_FILES")
EXTRACT_FILES_FONS_PATH = os.path.join(EXTRACT_FILES_PATH, "EXTRACT_FONS")
TEMPLATES_PATH = os.path.join(PIPELINE_PATH, "TEMPLATES")

# Fichero donde se persiste el mapa id_local -> ID real de Wikibase (Qxx/Pxx)
MAP_FILE = os.path.join(LOAD_FILES_PATH, "mapa_ontologia_wikibase.json")

# En Colab estas carpetas ya existían en Drive; en local hay que garantizarlo.
for _p in (PIPELINE_PATH, LOAD_FILES_PATH, EXTRACT_FILES_PATH, EXTRACT_FILES_FONS_PATH, TEMPLATES_PATH):
    os.makedirs(_p, exist_ok=True)


# ==========================================
# 2. CONEXIÓN A WIKIBASE — único punto a editar
# ==========================================

# Nombre de la instancia de Wikibase Cloud (la parte antes de ".wikibase.cloud").
# Cambiar aquí (o vía variable de entorno) basta para apuntar a otra instancia
# de pruebas sin tocar ningún otro script.
WIKIBASE_INSTANCE = os.environ.get("CARMESI_WIKIBASE_INSTANCE", "carmesi-test-15")

MEDIAWIKI_API_URL = f"https://{WIKIBASE_INSTANCE}.wikibase.cloud/w/api.php"
SPARQL_ENDPOINT_URL = f"https://{WIKIBASE_INSTANCE}.wikibase.cloud/query/sparql"
WIKIBASE_URL = f"https://{WIKIBASE_INSTANCE}.wikibase.cloud"
USER_AGENT = "CarmesiWikibaseBot/1.0 (script_inyector_wbi)"

# Credenciales del usuario Bot — NUNCA escritas en texto plano en este fichero.
# Defínalas como variables de entorno del sistema, o cree un fichero .env
# junto a config_carmesi.py (mismo directorio) con este contenido:
#
#   CARMESI_BOT_USER=admin
#   CARMESI_BOT_PASSWORD=su_contraseña_aqui
#   CARMESI_WIKIBASE_INSTANCE=carmesi-test-15
#
# El fichero .env NO debe subirse nunca a un repositorio (añádalo a .gitignore).
USUARIO_BOT = os.environ.get("CARMESI_BOT_USER")
PASSWORD_BOT = os.environ.get("CARMESI_BOT_PASSWORD")

if not USUARIO_BOT or not PASSWORD_BOT:
    sys.exit(
        "[ERROR] Faltan credenciales de Wikibase.\n"
        "Defina CARMESI_BOT_USER y CARMESI_BOT_PASSWORD como variables de entorno,\n"
        "o cree un fichero .env junto a config_carmesi.py (ver comentario en el código)."
    )


# ==========================================
# 3. INSTANCIACIÓN DEL CLIENTE
#    (se ejecuta una única vez, en el momento en que otro script hace
#     `import config_carmesi` o `from config_carmesi import wbi`)
# ==========================================

try:
    from wikibaseintegrator import WikibaseIntegrator, wbi_login
    from wikibaseintegrator.wbi_config import config as wbi_config
except ImportError:
    sys.exit(
        "[ERROR] Falta la librería wikibaseintegrator.\n"
        "Instálela con: pip install -r requirements.txt"
    )

wbi_config['MEDIAWIKI_API_URL'] = MEDIAWIKI_API_URL
wbi_config['SPARQL_ENDPOINT_URL'] = SPARQL_ENDPOINT_URL
wbi_config['WIKIBASE_URL'] = WIKIBASE_URL
wbi_config['USER_AGENT'] = USER_AGENT

print(f"[CONFIG] Conectando a {WIKIBASE_URL} como '{USUARIO_BOT}'...")

try:
    login_instance = wbi_login.Clientlogin(user=USUARIO_BOT, password=PASSWORD_BOT)
    wbi = WikibaseIntegrator(login=login_instance)
    print("[CONFIG] Conexión establecida correctamente.")
except Exception as e:
    sys.exit(f"[ERROR] No se pudo conectar a Wikibase en {WIKIBASE_URL}: {e}")


# ==========================================
# 4. PUBLICACIÓN DE PÁGINAS DE CONTENIDO (no ítems/propiedades)
# ==========================================
def obtener_contenido_pagina(titulo: str):
    """Devuelve el wikitexto actual de una página, o None si no existe o hubo
    un error al consultarla."""
    try:
        session = login_instance.get_session()
        resp = session.get(MEDIAWIKI_API_URL, params={
            "action": "query",
            "titles": titulo,
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "format": "json",
        })
        resp.raise_for_status()
        paginas = resp.json().get("query", {}).get("pages", {})
        for pagina in paginas.values():
            if "missing" in pagina:
                return None
            revisiones = pagina.get("revisions")
            if revisiones:
                return revisiones[0]["slots"]["main"]["*"]
        return None
    except Exception:
        return None


def publicar_pagina_wiki(titulo: str, texto: str, resumen: str = "Actualización automática") -> str:
    """
    Crea o actualiza una página de contenido normal del wiki (p. ej. el
    cuadro de clasificación o una ficha) vía la Action API de MediaWiki —
    distinto de `wbi`, que solo gestiona ítems y propiedades, no páginas.

    Antes de escribir, compara con el contenido actual: si es idéntico, no
    hace ninguna edición — evita ensuciar el historial de revisiones con
    ediciones vacías cada vez que se regenera un lote completo.

    Reutiliza la sesión ya autenticada de `login_instance`, así que no hace
    falta un segundo login.

    Devuelve: "creada" | "actualizada" | "sin_cambios" | "error"
    """
    actual = obtener_contenido_pagina(titulo)
    if actual is not None and actual == texto:
        return "sin_cambios"

    try:
        session = login_instance.get_session()
        token = login_instance.get_edit_token()
    except Exception as e:
        print(f"[ERROR] No se pudo obtener sesión/token de edición: {e}")
        return "error"

    try:
        resp = session.post(MEDIAWIKI_API_URL, data={
            "action": "edit",
            "title": titulo,
            "text": texto,
            "token": token,
            "bot": 1,
            "summary": resumen,
            "format": "json",
        })
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[ERROR] Fallo de red publicando '{titulo}': {e}")
        return "error"

    if "edit" in data and data["edit"].get("result") == "Success":
        return "creada" if actual is None else "actualizada"

    print(f"[ERROR] No se pudo publicar la página '{titulo}': {data}")
    return "error"