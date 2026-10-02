import csv
import json
import os
import time

from wikibaseintegrator.datatypes import Item, String, URL, ExternalID, Time, MonolingualText
from wikibaseintegrator.wbi_enums import ActionIfExists

from config_carmesi import wbi, LOAD_FILES_PATH, MAP_FILE
from progreso import Progreso

# Nombres de las tablas TSV
TABLA_Q = os.path.join(LOAD_FILES_PATH, '1_creacion_Q.tsv')
TABLA_P = os.path.join(LOAD_FILES_PATH, '2_creacion_P.tsv')
TABLA_DECLARACIONES = os.path.join(LOAD_FILES_PATH, '3_declaraciones.tsv')


# ==========================================
# 2. GESTIÓN DEL MAPA LOCAL -> WIKIBASE ID
# ==========================================
def cargar_mapa_ids():
    try:
        with open(MAP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def guardar_mapa_ids(mapa):
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=4)

mapa_ids = cargar_mapa_ids()
mapa_datatypes = {} # Guardará el datatype de cada propiedad local

# ==========================================
# 3. PASO 1: CREACIÓN DE ELEMENTOS (Q)
# ==========================================
def procesar_tabla_q(ruta_tsv):
    with open(ruta_tsv, "r", encoding="utf-8") as f:
        filas = list(csv.DictReader(f, delimiter="\t"))

    creados = omitidos = fallidos = 0
    p = Progreso(len(filas), etiqueta="elementos Q")

    for row in filas:
        id_local = row["id_local"].strip()
        label_es = row["label_es"].strip()
        desc_es = row["desc_es"].strip()

        if id_local in mapa_ids:
            p.avanzar(f"[OMITIDO] {id_local} ya mapeado como {mapa_ids[id_local]}")
            omitidos += 1
            continue

        try:
            item = wbi.item.new()
            item.labels.set('es', label_es)
            if desc_es:
                item.descriptions.set('es', desc_es)

            resultado = item.write()
            qid = resultado.id
            mapa_ids[id_local] = qid
            guardar_mapa_ids(mapa_ids)
            p.avanzar(f"[CREADO] {id_local} -> {qid} ({label_es})")
            creados += 1
            time.sleep(0.5)
        except Exception as e:
            p.aviso(f"   ❌ [FALLO Q] {id_local} ({label_es}): {e}")
            p.avanzar()
            fallidos += 1

    p.resumen("PASO 1: Elementos (Q)", creados=creados, omitidos=omitidos, fallidos=fallidos)
    return creados, omitidos, fallidos

# ==========================================
# 4. PASO 2: CREACIÓN DE PROPIEDADES (P)
# ==========================================
def procesar_tabla_p(ruta_tsv):
    with open(ruta_tsv, "r", encoding="utf-8") as f:
        filas = list(csv.DictReader(f, delimiter="\t"))

    creados = omitidos = fallidos = 0
    p = Progreso(len(filas), etiqueta="propiedades P")

    for row in filas:
        id_local = row["id_local"].strip()
        label_es = row["label_es"].strip()
        desc_es = row["desc_es"].strip()
        datatype = row["datatype"].strip()

        mapa_datatypes[id_local] = datatype

        if id_local in mapa_ids:
            p.avanzar(f"[OMITIDO] {id_local} ya mapeada como {mapa_ids[id_local]}")
            omitidos += 1
            continue

        try:
            prop = wbi.property.new(datatype=datatype)
            prop.labels.set('es', label_es)
            if desc_es:
                prop.descriptions.set('es', desc_es)

            resultado = prop.write()
            pid = resultado.id
            mapa_ids[id_local] = pid
            guardar_mapa_ids(mapa_ids)
            p.avanzar(f"[CREADA] {id_local} -> {pid} ({label_es}) [{datatype}]")
            creados += 1
            time.sleep(0.5)
        except Exception as e:
            p.aviso(f"   ❌ [FALLO P] {id_local} ({label_es}): {e}")
            p.avanzar()
            fallidos += 1

    p.resumen("PASO 2: Propiedades (P)", creadas=creados, omitidas=omitidos, fallidas=fallidos)
    return creados, omitidos, fallidos

# ==========================================
# 5. PASO 3: INYECCIÓN DE DECLARACIONES
# ==========================================
def construir_claim(pid_wb, datatype, valor):
    """Construye el objeto Claim según el datatype de la propiedad."""
    if datatype == "wikibase-item":
        # Resolver valor objetivo si es un id_local (ej. Q2.1 -> Q15)
        qid_destino = mapa_ids.get(valor, valor)
        return Item(prop_nr=pid_wb, value=qid_destino)

    elif datatype == "string":
        return String(prop_nr=pid_wb, value=valor)

    elif datatype == "url":
        return URL(prop_nr=pid_wb, value=valor)

    elif datatype == "external-id":
        return ExternalID(prop_nr=pid_wb, value=valor)

    elif datatype == "monolingualtext":
        return MonolingualText(prop_nr=pid_wb, text=valor, language="es")

    elif datatype == "time":
        # Formato esperado: +YYYY-MM-DDT00:00:00Z
        return Time(prop_nr=pid_wb, time=valor)

    else:
        raise ValueError(f"Datatype no soportado: {datatype}")

def procesar_tabla_declaraciones(ruta_tsv):
    # Agrupar declaraciones por sujeto para hacer lecturas/escrituras eficientes
    declaraciones_por_sujeto = {}

    with open(ruta_tsv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            id_sujeto = row["id_sujeto"].strip()
            id_propiedad = row["id_propiedad"].strip()
            valor = row["valor"].strip()

            if id_sujeto not in declaraciones_por_sujeto:
                declaraciones_por_sujeto[id_sujeto] = []

            declaraciones_por_sujeto[id_sujeto].append((id_propiedad, valor))

    actualizados = sin_cambios = fallidos = 0
    p = Progreso(len(declaraciones_por_sujeto), etiqueta="sujetos")

    for id_sujeto_local, lista_claims in declaraciones_por_sujeto.items():
        if id_sujeto_local not in mapa_ids:
            p.aviso(f"   ❌ [ERROR] Sujeto local '{id_sujeto_local}' no existe en el mapa de IDs. Se omite.")
            p.avanzar()
            fallidos += 1
            continue

        wb_id_sujeto = mapa_ids[id_sujeto_local]

        # Cargar la entidad correspondiente de Wikibase (Q o P)
        if wb_id_sujeto.startswith("Q"):
            entidad = wbi.item.get(entity_id=wb_id_sujeto)
        elif wb_id_sujeto.startswith("P"):
            entidad = wbi.property.get(entity_id=wb_id_sujeto)
        else:
            p.avanzar()
            continue

        claims_agregados = 0
        for id_prop_local, valor in lista_claims:
            if id_prop_local not in mapa_ids:
                p.aviso(f"   ❌ [ERROR] Propiedad local '{id_prop_local}' no existe en mapa de IDs.")
                continue

            pid_wb = mapa_ids[id_prop_local]
            datatype = mapa_datatypes.get(id_prop_local)

            if not datatype:
                p.aviso(f"   ❌ [ERROR] Datatype desconocido para propiedad '{id_prop_local}'.")
                continue

            try:
                claim = construir_claim(pid_wb, datatype, valor)
                entidad.claims.add([claim], action_if_exists=ActionIfExists.APPEND_OR_REPLACE)
                claims_agregados += 1
            except Exception as e:
                p.aviso(f"   ❌ [FALLO] {id_prop_local} -> {valor}: {e}")

        if claims_agregados > 0:
            entidad.write()
            p.avanzar(f"[ACTUALIZADO] {id_sujeto_local} ({wb_id_sujeto}): {claims_agregados} declaración(es)")
            actualizados += 1
            time.sleep(0.5)
        else:
            p.avanzar(f"[SIN CAMBIOS] {id_sujeto_local} ({wb_id_sujeto})")
            sin_cambios += 1

    p.resumen("PASO 3: Declaraciones", actualizados=actualizados, sin_cambios=sin_cambios, fallidos=fallidos)
    return actualizados, sin_cambios, fallidos

# ==========================================
# 6. EJECUCIÓN PRINCIPAL
# ==========================================
if __name__ == "__main__":
    print("=== INYECCIÓN DE ONTOLOGÍA EN WIKIBASE CLOUD ===")

    q_creados, q_omitidos, q_fallidos = procesar_tabla_q(TABLA_Q)
    p_creados, p_omitidos, p_fallidos = procesar_tabla_p(TABLA_P)
    d_actualizados, d_sin_cambios, d_fallidos = procesar_tabla_declaraciones(TABLA_DECLARACIONES)

    total_fallos = q_fallidos + p_fallidos + d_fallidos
    print("\n=== RESUMEN GLOBAL ===")
    print(f"   Elementos Q   : {q_creados} creados, {q_omitidos} omitidos, {q_fallidos} fallidos")
    print(f"   Propiedades P : {p_creados} creadas, {p_omitidos} omitidas, {p_fallidos} fallidas")
    print(f"   Declaraciones : {d_actualizados} actualizados, {d_sin_cambios} sin cambios, {d_fallidos} fallidos")
    print("\n¡Proceso finalizado con éxito!" if total_fallos == 0 else f"\n⚠️ Proceso finalizado con {total_fallos} incidencia(s).")
