"""
080_exportar_rdf_ttl.py
========================
Cierra el pipeline CARMESI (ISAD(G) -> RiC-O -> Wikibase) exportando el
contenido ya inyectado a un fichero Turtle (.ttl) conforme a RiC-O, como
dataset RDF nativo independiente de Wikibase — análogo en propósito y forma
al dataset de referencia publicado con RiC-O Converter (Giagnolini & Koch,
2025; https://rdm.inesctec.pt/dataset/cs-2024-009), que sirve de modelo de
empaquetado.

DISEÑO
------
No se consulta Wikibase en caliente (ni la API REST ni el endpoint SPARQL):
todo el dataset se reconstruye a partir de los ficheros locales que el propio
pipeline ya produce, que son su fuente de verdad determinista:

  - mapa_ontologia_wikibase.json   -> id_local (Q_x/P_x) -> QID/PID real
  - mapa_cuadro_wikibase.json      -> QID real de cada fondo/subfondo/serie
                                       del cuadro de clasificación (opcional:
                                       si no existe, se omite esa capa con
                                       un aviso, sin detener la exportación)
  - 1_creacion_Q.tsv / 2_creacion_P.tsv -> etiquetas (label_es) y datatypes
  - 3_declaraciones.tsv           -> equivalencia semántica de cada Q_x/P_x
                                       (P_uri = rdfs:isDefinedBy -> IRI RiC-O/
                                       SKOS/DC/OWL real; P_equivalente_a ->
                                       owl:sameAs, típicamente a Wikidata)
  - registros_inyectados_resuelto.jsonl -> UN documento por línea, con sus
                                       entidades secundarias y puntos de
                                       acceso ya resueltos a QID real (bloque
                                       "_wikibase"), tal como los dejó
                                       040_inyectar_registros.py

Esto hace la exportación reproducible sin conectividad y sin duplicar la
carga de la API/SPARQL de Wikibase Cloud; y, al depender solo de ficheros
producidos por pasos anteriores del propio pipeline, puede volver a
ejecutarse tras cada lote de inyección para regenerar el dataset completo.

DECISIÓN DE URIs (ajustar antes de publicar)
---------------------------------------------
Los sujetos del grafo NO usan las IRI de item de Wikibase
(https://<instancia>.wikibase.cloud/entity/Qxx) como identidad primaria,
para que el dataset sea portable y no dependa de qué instancia de pruebas
(TEST-15, TEST-19, producción futura) lo generó. En su lugar se acuñan URIs
propias bajo RDF_BASE_URI (configurable por variable de entorno
CARMESI_RDF_BASE_URI; por defecto un dominio de ejemplo a sustituir por el
dominio real de publicación del proyecto), usando el QID real como sufijo
estable — y se añade siempre un triple owl:sameAs de vuelta a la IRI del
ítem de Wikibase vivo, para mantener la trazabilidad hacia la instancia
editable. Las clases y propiedades RiC-O/SKOS/DC/OWL, en cambio, SÍ usan
directamente las IRI oficiales (vía P_uri/P_equivalente_a), nunca las IRI
locales de Wikibase: es lo que hace el dataset "conforme a RiC-O" y no un
mero volcado de Wikibase renombrado.

Los cuatro niveles del cuadro de clasificación (agrupación de fondos, fondo,
subfondo, serie) no tienen correspondencia 1:1 con el vocabulario oficial
ric-rst: (que solo define Fonds/Series/File/Collection) — se exportan como
individuos de skos:Concept enlazados por rico:hasRecordSetType, sin forzar
una equivalencia ric-rst: que no es exacta. Revísese este punto en el
artículo si se decide alinear explícitamente subfondo/agrupación de fondos
con alguno de los cuatro términos oficiales.

USO
---
    python 080_exportar_rdf_ttl.py [--limit N] [--out FICHERO.ttl]

Requiere `rdflib` (pip install rdflib). No requiere conexión a Wikibase ni
credenciales — a diferencia del resto del pipeline, este script NO importa
`config_carmesi` (evita exigir login solo para leer ficheros locales).
"""

import argparse
import csv
import json
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from rdflib import Graph, Namespace, URIRef, Literal, BNode
    from rdflib.namespace import RDF, RDFS, OWL, XSD
except ImportError:
    sys.exit(
        "[ERROR] Falta la librería rdflib.\n"
        "Instálela con: pip install rdflib"
    )

from progreso import Progreso

# ==========================================
# 0. CONFIGURACIÓN Y RUTAS
# ==========================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_PATH = os.environ.get("CARMESI_PIPELINE_PATH", SCRIPT_DIR)
LOAD_FILES_PATH = os.path.join(PIPELINE_PATH, "LOAD_FILES")

MAPA_ONTOLOGIA_PATH = os.path.join(LOAD_FILES_PATH, "mapa_ontologia_wikibase.json")
MAPA_CUADRO_PATH = os.path.join(LOAD_FILES_PATH, "mapa_cuadro_wikibase.json")
# Mapa de CONTROL compartido (020/040/045) — aquí se lee solo en modo
# lectura, para recuperar el label_es de entidades como el productor de un
# fondo (Q_institucion), que 080 no recorre por ninguna otra vía cuando esa
# entidad no aparece también como punto de acceso de ningún documento.
MAPA_CONTROL_PATH = os.path.join(LOAD_FILES_PATH, "mapa_items_inyectados_wikibase.json")
TABLA_Q = os.path.join(LOAD_FILES_PATH, "1_creacion_Q.tsv")
TABLA_P = os.path.join(LOAD_FILES_PATH, "2_creacion_P.tsv")
TABLA_DECLARACIONES = os.path.join(LOAD_FILES_PATH, "3_declaraciones.tsv")
FICHERO_RESUELTO_PATH = os.path.join(LOAD_FILES_PATH, "registros_inyectados_resuelto.jsonl")

# IRI del ítem Wikibase vivo (para el owl:sameAs de trazabilidad). Debe
# coincidir con la instancia que realmente generó registros_inyectados_
# resuelto.jsonl — ajústese si se exporta desde otra instancia.
WIKIBASE_INSTANCE = os.environ.get("CARMESI_WIKIBASE_INSTANCE", "carmesi-test-19")
WIKIBASE_ENTITY_BASE = f"https://{WIKIBASE_INSTANCE}.wikibase.cloud/entity/"

# Dominio propio del dataset exportado. CAMBIAR antes de una publicación
# real por el dominio definitivo del proyecto (o por un IRI de ejemplo
# reconocido, p. ej. bajo el dominio de la propia institución archivística).
RDF_BASE_URI = os.environ.get("CARMESI_RDF_BASE_URI", "https://datos.carmesi.regmurcia.es/")


# ==========================================
# 1. NAMESPACES
# ==========================================

RICO = Namespace("https://www.ica.org/standards/RiC/ontology#")
RIC_RST = Namespace("https://www.ica.org/standards/RiC/vocabularies/recordSetTypes#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
SCHEMA = Namespace("http://schema.org/")
DCTERMS = Namespace("http://purl.org/dc/terms/")
WD = Namespace("https://www.wikidata.org/entity/")
BASE = Namespace(RDF_BASE_URI)

# Predicados/clases especiales que no cuelgan de RiC-O pero forman parte del
# esqueleto RDF básico y siempre se resuelven igual, con independencia de lo
# que diga 3_declaraciones.tsv (que ya los declara, pero se fijan aquí como
# red de seguridad si algún día esa tabla cambiase).
PREDICADO_FIJO = {
    "P_instancia_de": RDF.type,
    "P_subclase_de": RDFS.subClassOf,
    "P_equivalente_a": OWL.sameAs,
    "P_uri": RDFS.isDefinedBy,
}


# ==========================================
# 2. CARGA DE FICHEROS DE APOYO (id_local <-> semántica real)
# ==========================================

def cargar_json_seguro(path: str) -> Dict[str, Any]:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def cargar_tabla_tsv(path: str) -> List[Dict[str, str]]:
    if not os.path.isfile(path):
        sys.exit(f"[ERROR] No se encuentra la tabla: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def resolver_uri_declarada(valor: str) -> URIRef:
    """3_declaraciones.tsv mezcla IRI completas (P_uri) con QID desnudos de
    Wikidata (P_equivalente_a, p. ej. 'Q1321') — normaliza ambos a URIRef."""
    if re.match(r"^Q\d+$", valor):
        return WD[valor]
    return URIRef(valor)


def construir_mapas_ontologia() -> Tuple[Dict[str, str], Dict[str, URIRef], Dict[str, URIRef], Dict[str, str], Dict[str, str]]:
    """Devuelve:
    - id_a_real: id_local -> QID/PID real (Qxx/Pxx)
    - id_a_uri: id_local -> IRI semántica real (desde P_uri)
    - id_a_sameas: id_local -> IRI de alineación externa (desde P_equivalente_a)
    - id_a_label: id_local -> etiqueta es (de 1_creacion_Q.tsv / 2_creacion_P.tsv)
    - id_a_datatype: id_local de propiedad -> datatype Wikibase (de 2_creacion_P.tsv)
    """
    id_a_real = cargar_json_seguro(MAPA_ONTOLOGIA_PATH)
    if not id_a_real:
        sys.exit(f"[ERROR] {MAPA_ONTOLOGIA_PATH} vacío o inexistente. Ejecute antes 010_inyectar_ontologia.py.")

    id_a_uri: Dict[str, URIRef] = {}
    id_a_sameas: Dict[str, URIRef] = {}
    for fila in cargar_tabla_tsv(TABLA_DECLARACIONES):
        sujeto, prop, valor = fila["id_sujeto"].strip(), fila["id_propiedad"].strip(), fila["valor"].strip()
        if prop == "P_uri":
            id_a_uri[sujeto] = resolver_uri_declarada(valor)
        elif prop == "P_equivalente_a":
            id_a_sameas[sujeto] = resolver_uri_declarada(valor)

    id_a_label: Dict[str, str] = {}
    for fila in cargar_tabla_tsv(TABLA_Q):
        id_a_label[fila["id_local"].strip()] = fila.get("label_es", "").strip()

    id_a_datatype: Dict[str, str] = {}
    for fila in cargar_tabla_tsv(TABLA_P):
        id_local = fila["id_local"].strip()
        id_a_label[id_local] = fila.get("label_es", "").strip()
        id_a_datatype[id_local] = fila.get("datatype", "").strip()

    return id_a_real, id_a_uri, id_a_sameas, id_a_label, id_a_datatype


# ==========================================
# 3. RESOLUCIÓN DE PREDICADOS Y CLASES
# ==========================================

class Ontologia:
    """Envuelve los mapas cargados y ofrece resolución predicado/clase con
    aviso (una sola vez por id_local no resuelto, para no inundar la consola
    en una exportación de miles de documentos)."""

    def __init__(self, progreso: Optional[Progreso] = None):
        (self.id_a_real, self.id_a_uri, self.id_a_sameas,
         self.id_a_label, self.id_a_datatype) = construir_mapas_ontologia()
        self.mapa_cuadro = cargar_json_seguro(MAPA_CUADRO_PATH)
        self.entidades_compartidas = (cargar_json_seguro(MAPA_CONTROL_PATH) or {}).get("entidades_compartidas", {})
        self._avisados: set = set()
        self._progreso = progreso

    def _avisar_una_vez(self, clave: str, texto: str) -> None:
        if clave in self._avisados:
            return
        self._avisados.add(clave)
        if self._progreso is not None:
            self._progreso.aviso(texto)
        else:
            print(texto)

    def predicado(self, id_local: str) -> Optional[URIRef]:
        """IRI RiC-O/SKOS/DC/OWL de una propiedad, a partir de su id_local."""
        if id_local in PREDICADO_FIJO:
            return PREDICADO_FIJO[id_local]
        uri = self.id_a_uri.get(id_local)
        if uri is None:
            self._avisar_una_vez(
                f"pred:{id_local}",
                f"   ⚠️ Sin equivalencia RiC-O/SKOS/DC declarada para '{id_local}' "
                f"(revise 3_declaraciones.tsv) — se omite en la exportación.",
            )
        return uri

    def clase(self, id_local_o_qid: str) -> Optional[URIRef]:
        """IRI de clase (rico:Record, skos:Concept...) a partir del id_local
        de la clase (p. ej. 'Q_documento') o de su QID real si ya viene
        resuelto (algunas rutas del pipeline guardan directamente el QID)."""
        if id_local_o_qid in self.id_a_uri:
            return self.id_a_uri[id_local_o_qid]
        # Si nos llega un QID real en vez de un id_local, buscamos qué
        # id_local se le asignó en mapa_ontologia_wikibase.json.
        for local, real in self.id_a_real.items():
            if real == id_local_o_qid and local in self.id_a_uri:
                return self.id_a_uri[local]
        self._avisar_una_vez(
            f"clase:{id_local_o_qid}",
            f"   ⚠️ Sin clase RiC-O/SKOS declarada para '{id_local_o_qid}' — "
            f"se omite el triple rdf:type correspondiente.",
        )
        return None

    def sameas(self, id_local: str) -> Optional[URIRef]:
        return self.id_a_sameas.get(id_local)

    def label(self, id_local: str) -> Optional[str]:
        return self.id_a_label.get(id_local)

    def id_local_de_real(self, real: str) -> Optional[str]:
        """Búsqueda inversa: QID/PID real -> id_local. Necesaria cuando se
        recibe un QID ya resuelto (p. ej. q_lengua_agrupacion, calculado por
        045 contra la ontología) y hace falta su id_local para reutilizar
        emitir_individuo_controlado(), que indexa por id_local."""
        for local, r in self.id_a_real.items():
            if r == real:
                return local
        return None

    def label_entidad_compartida(self, qid: str) -> Optional[str]:
        """label_es de una entidad del mapa de control compartido
        (mapa_items_inyectados_wikibase.json), indexada por QID real. Se usa
        para dar rdfs:label a entidades que 080 no recorre por otra vía (p.
        ej. el productor de un fondo, si no aparece también como punto de
        acceso de ningún documento)."""
        for entrada in self.entidades_compartidas.values():
            if isinstance(entrada, dict) and entrada.get("qid") == qid:
                return entrada.get("label_es")
        return None


# ==========================================
# 4. ACUÑACIÓN DE URIs LOCALES (individuos del dataset)
# ==========================================

COLECCION_POR_CLASE_RICO = {
    RICO.Record: "record",
    RICO.RecordSet: "recordSet",
    RICO.Agent: "agent",
    RICO.Person: "agent",
    RICO.CorporateBody: "agent",
    RICO.Place: "place",
    RICO.Date: "date",
    RICO.Instantiation: "instantiation",
    RICO.Language: "language",
    SKOS.Concept: "concept",
}

# mapa_cuadro_wikibase.json guarda el tipo de nodo como cadena en minúsculas
# ("fondo"/"subfondo"/"serie"/"raiz" — clave "tipo", ver
# 020_cargar_cuadro_clasificacion.py), nunca como "Q_fondo"/"Q_serie". Esta
# tabla traduce esa cadena al id_local de la CLASE correspondiente, para
# resolver el QID real de P_tipo_agrupacion_doc contra mapa_ontologia_wikibase.json.
TIPO_CUADRO_A_ID_LOCAL_CLASE = {
    "fondo": "Q_fondo",
    "subfondo": "Q_subfondo",
    "serie": "Q_serie",
    "raiz": "Q_agrupacion_fondos",
}


def uri_entidad(qid: str, clase_rico: Optional[URIRef]) -> URIRef:
    coleccion = COLECCION_POR_CLASE_RICO.get(clase_rico, "entity")
    return BASE[f"{coleccion}/{qid}"]


def uri_wikibase_item(qid: str) -> URIRef:
    return URIRef(f"{WIKIBASE_ENTITY_BASE}{qid}")


# ==========================================
# 5. SERIALIZACIÓN DE VALORES LITERALES
# ==========================================

_DATATYPE_A_XSD = {
    "string": None,          # literal simple, sin tipo XSD explícito
    "url": XSD.anyURI,
    "external-id": None,
    "monolingualtext": None,  # se gestiona aparte (lang tag)
}


def literal_para_datatype(valor: str, datatype: str, lang: Optional[str] = "es") -> Literal:
    if datatype == "time":
        return literal_fecha(valor)
    if datatype == "monolingualtext":
        return Literal(valor, lang=lang)
    xsd_type = _DATATYPE_A_XSD.get(datatype)
    if xsd_type:
        return Literal(valor, datatype=xsd_type)
    return Literal(valor, lang=lang) if datatype == "string" else Literal(valor)


def literal_fecha(valor: str) -> Literal:
    """Normaliza 'YYYY', 'YYYY-MM' o 'YYYY-MM-DD' a xsd:date/xsd:gYearMonth/
    xsd:gYear, en paralelo a construir_claim_tiempo() de wb_claims.py."""
    v = valor.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", v):
        return Literal(v, datatype=XSD.date)
    if re.match(r"^\d{4}-\d{2}$", v):
        return Literal(v, datatype=XSD.gYearMonth)
    if re.match(r"^\d{4}$", v):
        return Literal(v, datatype=XSD.gYear)
    return Literal(v)


# ==========================================
# 6. EXPORTACIÓN DE UN DOCUMENTO (+ secundarias + puntos de acceso)
# ==========================================

# Propiedades cuyo valor es un código/identificador opaco o un token técnico
# (no prosa en español) — no deben llevar etiqueta de idioma @es. Detectado al
# revisar la primera exportación de prueba: 'rico:identifier "2740"@es' o
# 'dcterms:format "application/pdf"@es' son literales mal tipados en RDF/RiC-O
# (RiC-O define rico:identifier y dcterms:format como cadenas simples, no como
# texto en un idioma natural).
PROPIEDADES_SIN_ETIQUETA_IDIOMA = {"P_id_origen", "P_codigo_ref", "P_mimetype"}


def emitir_literal_si_hay(g: Graph, onto: Ontologia, sujeto: URIRef, id_local_prop: str, valor: Any, lang: str = "es") -> None:
    if valor in (None, "", []):
        return
    pred = onto.predicado(id_local_prop)
    if pred is None:
        return
    datatype = onto.id_a_datatype.get(id_local_prop, "string")
    lang_efectivo = None if id_local_prop in PROPIEDADES_SIN_ETIQUETA_IDIOMA else lang
    valores = valor if isinstance(valor, list) else [valor]
    for v in valores:
        if not v:
            continue
        g.add((sujeto, pred, literal_para_datatype(str(v), datatype, lang_efectivo)))


def emitir_tipo(g: Graph, onto: Ontologia, sujeto: URIRef, id_local_clase: str) -> Optional[URIRef]:
    clase = onto.clase(id_local_clase)
    if clase:
        g.add((sujeto, RDF.type, clase))
    return clase


def qid_valido(valor: Any) -> bool:
    return isinstance(valor, str) and re.match(r"^Q\d+$", valor) is not None


class ExportadorRDF:
    def __init__(self, onto: Ontologia):
        self.onto = onto
        self.g = Graph()
        for prefijo, ns in (("rico", RICO), ("ric-rst", RIC_RST), ("skos", SKOS),
                             ("schema", SCHEMA), ("dcterms", DCTERMS), ("wd", WD),
                             ("owl", OWL), ("", BASE)):
            self.g.bind(prefijo, ns)
        self._individuos_controlados_emitidos: set = set()  # dedupe Q_castellano, Q_escritura_*, etc.
        self._secundarios_emitidos: set = set()  # dedupe fecha/instanciación por qid (reutilizadas entre documentos)
        self._puntos_acceso_emitidos: set = set()  # dedupe agentes/lugares/materias compartidos

        # -- diagnóstico de duplicados en el dato de origen (no en este script) --
        # Si la reconciliación de entidades_compartidas de 040_inyectar_registros.py
        # ha funcionado, cada entidad real (misma categoria + P_id_origen) debe
        # corresponder a un único QID. Aquí solo se detecta y se informa; el script
        # de exportación no fusiona QIDs (fusionarlos exige editar Wikibase, no el
        # dataset RDF derivado). Clave: (categoria, P_id_origen) -> {QIDs vistos}.
        self._origen_a_qids: Dict[Tuple[str, str], set] = {}
        self._origen_a_label: Dict[Tuple[str, str], str] = {}

    # -- vocabulario controlado (lengua, tipo de escritura) --------------
    def emitir_individuo_controlado(self, id_local: str) -> Optional[URIRef]:
        """Emite (una sola vez) un individuo de vocabulario cerrado como
        Q_castellano o Q_escritura_cortesana: su clase, su rdfs:label y, si
        la tiene, su alineación owl:sameAs (p. ej. a Wikidata)."""
        if id_local in self._individuos_controlados_emitidos:
            qid = self.onto.id_a_real.get(id_local)
            clase = self.onto.clase(self._id_local_clase_de(id_local))
            return uri_entidad(qid, clase) if qid else None

        qid = self.onto.id_a_real.get(id_local)
        if not qid:
            return None

        id_local_clase = self._id_local_clase_de(id_local)
        clase = self.onto.clase(id_local_clase) or SKOS.Concept
        sujeto = uri_entidad(qid, clase)

        self.g.add((sujeto, RDF.type, clase))
        label = self.onto.label(id_local)
        if label:
            self.g.add((sujeto, RDFS.label, Literal(label, lang="es")))
        sameas = self.onto.sameas(id_local)
        if sameas:
            self.g.add((sujeto, OWL.sameAs, sameas))
        self.g.add((sujeto, OWL.sameAs, uri_wikibase_item(qid)))

        self._individuos_controlados_emitidos.add(id_local)
        return sujeto

    @staticmethod
    def _id_local_clase_de(id_local: str) -> str:
        """Heurística basada en el prefijo del id_local del vocabulario
        controlado (ver 1_creacion_Q.tsv): Q_castellano/valenciano/latin son
        instancias de Q_lengua; Q_escritura_* son instancias de Q_tipo_escritura."""
        if id_local.startswith("Q_escritura_"):
            return "Q_tipo_escritura"
        if id_local in ("Q_castellano", "Q_valenciano", "Q_latin"):
            return "Q_lengua"
        return id_local

    # -- entidad documento -------------------------------------------------
    def exportar_documento(self, reg: Dict[str, Any]) -> None:
        wb = reg.get("_wikibase") or {}
        qid_doc = wb.get("qid_documento")
        if not qid_doc:
            return  # documento sin resolver en Wikibase (no debería ocurrir en el fichero resuelto)

        clase_doc = self.onto.clase("Q_documento")
        sujeto = uri_entidad(qid_doc, clase_doc)
        if clase_doc:
            self.g.add((sujeto, RDF.type, clase_doc))
        self.g.add((sujeto, OWL.sameAs, uri_wikibase_item(qid_doc)))

        label = reg.get("label_es")
        if label:
            self.g.add((sujeto, RDFS.label, Literal(label, lang="es")))
            self.g.add((sujeto, RICO.title, Literal(label, lang="es")))
        desc = reg.get("desc_es")
        if desc:
            self.g.add((sujeto, RICO.generalDescription, Literal(desc, lang="es")))

        decl = reg.get("declaraciones_directas", {})

        # -- P_incluido_en: ya viene como QID real (resuelto en el cuadro de
        # clasificación por transformar_registros.py), no como id_local --
        valor_serie = decl.get("P_incluido_en")
        if qid_valido(valor_serie):
            pred = self.onto.predicado("P_incluido_en")
            if pred:
                clase_rs = self.onto.clase("Q_agrupacion_doc")
                self.g.add((sujeto, pred, uri_entidad(valor_serie, clase_rs)))
            self._emitir_recordset_cuadro(valor_serie)

        # -- propiedades de texto/URL directas --
        for prop in ("P_codigo_ref", "P_titulo", "P_alcance_contenido",
                     "P_condiciones_acceso", "P_nota", "P_url_acceso"):
            emitir_literal_si_hay(self.g, self.onto, sujeto, prop, decl.get(prop))

        # -- lengua: valores son id_local del vocabulario controlado --
        pred_lengua = self.onto.predicado("P_lengua")
        if pred_lengua:
            for id_local_lengua in (decl.get("P_lengua") or []):
                obj = self.emitir_individuo_controlado(id_local_lengua)
                if obj:
                    self.g.add((sujeto, pred_lengua, obj))

        # -- entidades secundarias del propio documento --
        secc_wb = wb.get("secundarias") or {}
        secc_datos = reg.get("Q_secundarios") or {}

        qid_fecha = secc_wb.get("fecha")
        if qid_fecha:
            self._emitir_fecha(qid_fecha, secc_datos.get("entidad_fecha") or {})
            pred = self.onto.predicado("P_fecha")
            if pred:
                self.g.add((sujeto, pred, uri_entidad(qid_fecha, self.onto.clase("Q_fecha"))))

        digitales_datos = secc_datos.get("instanciaciones_digitales") or []
        for i, qid_inst in enumerate(secc_wb.get("instanciaciones_digitales") or []):
            datos_inst = digitales_datos[i] if i < len(digitales_datos) else {}
            self._emitir_instanciacion(qid_inst, datos_inst, digital=True)
            pred = self.onto.predicado("P_instanciacion_digital")
            if pred:
                self.g.add((sujeto, pred, uri_entidad(qid_inst, self.onto.clase("Q_instanciacion"))))

        qid_fisica = secc_wb.get("instanciacion_fisica")
        if qid_fisica:
            self._emitir_instanciacion(qid_fisica, secc_datos.get("instanciacion_fisica") or {}, digital=False)
            pred = self.onto.predicado("P_instanciacion_fisica")
            if pred:
                self.g.add((sujeto, pred, uri_entidad(qid_fisica, self.onto.clase("Q_instanciacion"))))

        # -- puntos de acceso (agentes/lugares/materias/productor), compartidos --
        for pa in wb.get("puntos_acceso") or []:
            qid_pa = pa.get("qid")
            categoria = pa.get("categoria")
            clave_mapa_pa = pa.get("clave_mapa")
            if not qid_pa:
                continue
            self._emitir_punto_acceso(qid_pa, categoria, clave_mapa_pa, reg)
            id_local_prop = {
                "instituciones": "P_institucion", "personas": "P_persona",
                "materias": "P_materia", "lugares": "P_lugar",
                "productores": "P_productor",
            }.get(categoria)
            if not id_local_prop:
                continue
            pred = self.onto.predicado(id_local_prop)
            if pred:
                clase_pa = {
                    "instituciones": "Q_institucion", "personas": "Q_persona",
                    "materias": "Q_materia", "lugares": "Q_lugar",
                    "productores": "Q_institucion",
                }.get(categoria)
                self.g.add((sujeto, pred, uri_entidad(qid_pa, self.onto.clase(clase_pa))))

    # -- entidad fecha ------------------------------------------------------
    def _emitir_fecha(self, qid: str, datos: Dict[str, Any]) -> None:
        if qid in self._secundarios_emitidos:
            return
        self._secundarios_emitidos.add(qid)
        clase = self.onto.clase("Q_fecha")
        sujeto = uri_entidad(qid, clase)
        if clase:
            self.g.add((sujeto, RDF.type, clase))
        self.g.add((sujeto, OWL.sameAs, uri_wikibase_item(qid)))
        for prop in ("P_fecha_expresada",):
            emitir_literal_si_hay(self.g, self.onto, sujeto, prop, datos.get(prop))
        for prop in ("P_fecha_inicio", "P_fecha_final"):
            emitir_literal_si_hay(self.g, self.onto, sujeto, prop, datos.get(prop))

    # -- entidad instanciación (física o digital) ---------------------------
    def _emitir_instanciacion(self, qid: str, datos: Dict[str, Any], digital: bool) -> None:
        if qid in self._secundarios_emitidos:
            return
        self._secundarios_emitidos.add(qid)
        clase = self.onto.clase("Q_instanciacion")
        sujeto = uri_entidad(qid, clase)
        if clase:
            self.g.add((sujeto, RDF.type, clase))
        self.g.add((sujeto, OWL.sameAs, uri_wikibase_item(qid)))
        label = datos.get("label_es")
        if label:
            self.g.add((sujeto, RDFS.label, Literal(label, lang="es")))

        if digital:
            emitir_literal_si_hay(self.g, self.onto, sujeto, "P_url_acceso", datos.get("P_url_acceso"))
            emitir_literal_si_hay(self.g, self.onto, sujeto, "P_mimetype", datos.get("P_mimetype"))
        else:
            emitir_literal_si_hay(self.g, self.onto, sujeto, "P_volumen_soporte", datos.get("P_volumen_soporte"))
            emitir_literal_si_hay(self.g, self.onto, sujeto, "P_estado_conservacion", datos.get("P_estado_conservacion"))
            pred_escritura = self.onto.predicado("P_tipo_escritura")
            if pred_escritura:
                for id_local_escritura in (datos.get("P_tipo_escritura") or []):
                    obj = self.emitir_individuo_controlado(id_local_escritura)
                    if obj:
                        self.g.add((sujeto, pred_escritura, obj))

    # -- puntos de acceso compartidos (agente/lugar/materia) ---------------
    def _emitir_punto_acceso(self, qid: str, categoria: str, clave_mapa: Optional[str], reg_actual: Dict[str, Any]) -> None:
        if qid in self._puntos_acceso_emitidos:
            return
        self._puntos_acceso_emitidos.add(qid)

        clase_por_categoria = {
            "instituciones": "Q_institucion", "personas": "Q_persona",
            "materias": "Q_materia", "lugares": "Q_lugar",
            "productores": "Q_institucion",
        }
        id_local_clase = clase_por_categoria.get(categoria, "Q_institucion")
        clase = self.onto.clase(id_local_clase)
        sujeto = uri_entidad(qid, clase)
        if clase:
            self.g.add((sujeto, RDF.type, clase))
        self.g.add((sujeto, OWL.sameAs, uri_wikibase_item(qid)))

        # Recuperamos el bloque de datos original de este punto de acceso
        # (label_es, url, P_id_origen) buscando en 'puntos_acceso' del propio
        # registro actual — es la única copia con esos campos disponible en
        # el fichero resuelto. Se empareja por 'clave_mapa' (identificador
        # único real de la entidad, p. ej. "PA_PERSONAS_2740"), NUNCA solo por
        # 'categoria': una condición anterior aquí (`pa.get("q_resolucion")
        # == qid or pa.get("clave_mapa") and pa.get("categoria") ==
        # categoria`) hacía, por precedencia de operadores, que CUALQUIER
        # punto de acceso de la misma categoría en el documento coincidiera
        # con el primer QID que se procesara — colgando el label_es/
        # P_id_origen de una persona a otra persona distinta del mismo
        # documento. Ese bug de emparejamiento (no una duplicación real en
        # Wikibase) es lo que producía el falso positivo de "entidades
        # duplicadas" reportado en la primera exportación de prueba.
        for pa in reg_actual.get("puntos_acceso", []):
            coincide = (pa.get("clave_mapa") == clave_mapa) if clave_mapa else (pa.get("q_resolucion") == qid)
            if coincide:
                if pa.get("label_es"):
                    self.g.add((sujeto, RDFS.label, Literal(pa["label_es"], lang="es")))
                emitir_literal_si_hay(self.g, self.onto, sujeto, "P_id_origen", pa.get("P_id_origen"))
                emitir_literal_si_hay(self.g, self.onto, sujeto, "P_url_acceso", pa.get("url"))

                id_origen = pa.get("P_id_origen")
                if id_origen:
                    clave = (categoria, str(id_origen))
                    self._origen_a_qids.setdefault(clave, set()).add(qid)
                    if pa.get("label_es"):
                        self._origen_a_label[clave] = pa["label_es"]
                break

    # -- recordset del cuadro de clasificación (fondo/subfondo/serie) ------
    def _emitir_recordset_cuadro(self, qid: str) -> None:
        if not self.onto.mapa_cuadro or qid in self._secundarios_emitidos:
            return
        self._secundarios_emitidos.add(qid)

        entrada = None
        for _clave, val in self.onto.mapa_cuadro.items():
            if isinstance(val, dict) and val.get("qid") == qid:
                entrada = val
                break
        if not entrada:
            return

        clase = self.onto.clase("Q_agrupacion_doc")
        sujeto = uri_entidad(qid, clase)
        if clase:
            self.g.add((sujeto, RDF.type, clase))
        self.g.add((sujeto, OWL.sameAs, uri_wikibase_item(qid)))
        if entrada.get("titulo"):
            self.g.add((sujeto, RDFS.label, Literal(entrada["titulo"], lang="es")))

        # 1.4 Nivel de descripción / RiC-R081 hasRecordSetType: la clave real
        # es "tipo" ("fondo"/"subfondo"/"serie"/"raiz" en minúsculas), no
        # "nivel" — ver TIPO_CUADRO_A_ID_LOCAL_CLASE más arriba.
        id_local_clase_nivel = TIPO_CUADRO_A_ID_LOCAL_CLASE.get(entrada.get("tipo"))
        if id_local_clase_nivel:
            qid_nivel = self.onto.id_a_real.get(id_local_clase_nivel)
            pred_tipo = self.onto.predicado("P_tipo_agrupacion_doc")
            if pred_tipo and qid_nivel:
                self.g.add((sujeto, pred_tipo, uri_entidad(qid_nivel, SKOS.Concept)))

        # Alcance y contenido (RiC-A38, literal) — persistido en el cuadro
        # local por 020_cargar_cuadro_clasificacion.py.
        emitir_literal_si_hay(self.g, self.onto, sujeto, "P_alcance_contenido", entrada.get("alcance_contenido"))

        # Productor (RiC-R027, rico:hasOrHadCreator) — entidad Q_institucion
        # creada/reutilizada por obtener_o_crear_productor() en 020.
        qid_productor = entrada.get("q_productor")
        if qid_productor:
            pred_productor = self.onto.predicado("P_productor")
            if pred_productor:
                self.g.add((sujeto, pred_productor, uri_entidad(qid_productor, self.onto.clase("Q_institucion"))))
            self._emitir_entidad_productor(qid_productor)

        # Fecha agregada de todos los miembros (RiC-R081i,
        # rico:hasOrHadAllMembersWithCreationDate) — entidad Q_fecha propia
        # del nodo, calculada/declarada por 045_calcular_fechas_lengua_agrupacion.py.
        qid_fecha_agrup = entrada.get("q_fecha_agrupacion")
        if qid_fecha_agrup:
            pred_fecha_agrup = self.onto.predicado("P_fecha_agrupacion")
            if pred_fecha_agrup:
                self.g.add((sujeto, pred_fecha_agrup, uri_entidad(qid_fecha_agrup, self.onto.clase("Q_fecha"))))
            ini = entrada.get("fecha_inicio_agrupacion") or ""
            fin = entrada.get("fecha_final_agrupacion") or ""
            self._emitir_fecha(qid_fecha_agrup, {
                "P_fecha_expresada": f"{ini} - {fin}" if (ini or fin) else None,
                "P_fecha_inicio": ini or None,
                "P_fecha_final": fin or None,
            })

        # Lengua homogénea de todos los miembros (sin código RiC-CM 1.0
        # propio; rico:hasOrHadAllMembersWithLanguage) — enlaza directamente
        # al ítem Q_lengua ya existente (Q_castellano/Q_valenciano/Q_latin),
        # sin crear ninguna entidad nueva, igual que 045 en Wikibase.
        qid_lengua_agrup = entrada.get("q_lengua_agrupacion")
        if qid_lengua_agrup:
            pred_lengua_agrup = self.onto.predicado("P_lengua_agrupacion")
            if pred_lengua_agrup:
                id_local_lengua = self.onto.id_local_de_real(qid_lengua_agrup)
                obj = self.emitir_individuo_controlado(id_local_lengua) if id_local_lengua else None
                if obj is None:
                    clase_lengua = self.onto.clase("Q_lengua")
                    obj = uri_entidad(qid_lengua_agrup, clase_lengua)
                    if clase_lengua:
                        self.g.add((obj, RDF.type, clase_lengua))
                    self.g.add((obj, OWL.sameAs, uri_wikibase_item(qid_lengua_agrup)))
                self.g.add((sujeto, pred_lengua_agrup, obj))

        # Jerarquía ascendente del cuadro (serie -> subfondo/fondo -> raíz),
        # vía P_incluido_en — reconstruida desde "qid_padre", persistido por
        # 020 en cada nodo (nunca estuvo en el cuadro antes de este fix).
        qid_padre = entrada.get("qid_padre")
        if qid_padre:
            pred_incluido = self.onto.predicado("P_incluido_en")
            if pred_incluido:
                self.g.add((sujeto, pred_incluido, uri_entidad(qid_padre, clase)))
            self._emitir_recordset_cuadro(qid_padre)

    # -- entidad productora de un fondo/subfondo/serie ---------------------
    def _emitir_entidad_productor(self, qid: str) -> None:
        """Emite (una sola vez) la entidad Q_institucion productora de una
        agrupación documental, cuando aún no se ha emitido por otra vía (p.
        ej. como punto de acceso de algún documento individual) — el
        productor se declara a nivel de fondo/subfondo/serie
        (020_cargar_cuadro_clasificacion.py), no necesariamente coincide con
        el punto de acceso 'institución'/'productor' de ningún documento
        concreto del conjunto. Reutiliza el dedupe de puntos de acceso
        porque es exactamente el mismo tipo de entidad compartida."""
        if qid in self._puntos_acceso_emitidos:
            return
        self._puntos_acceso_emitidos.add(qid)
        clase = self.onto.clase("Q_institucion")
        sujeto = uri_entidad(qid, clase)
        if clase:
            self.g.add((sujeto, RDF.type, clase))
        self.g.add((sujeto, OWL.sameAs, uri_wikibase_item(qid)))
        label = self.onto.label_entidad_compartida(qid)
        if label:
            self.g.add((sujeto, RDFS.label, Literal(label, lang="es")))

    # -- diagnóstico: entidades de origen (misma categoria + P_id_origen) que
    # se resolvieron a más de un QID, señal de que la reconciliación de
    # entidades_compartidas de 040_inyectar_registros.py no las dedupicó --
    def duplicados_detectados(self) -> List[Tuple[str, str, str, List[str]]]:
        """Devuelve (categoria, id_origen, label, [qids]) para cada entidad de
        origen con más de un QID, ordenado por número de duplicados desc."""
        filas = []
        for (categoria, id_origen), qids in self._origen_a_qids.items():
            if len(qids) > 1:
                label = self._origen_a_label.get((categoria, id_origen), "")
                filas.append((categoria, id_origen, label, sorted(qids)))
        filas.sort(key=lambda f: len(f[3]), reverse=True)
        return filas


# ==========================================
# 7. ORQUESTADOR
# ==========================================

def iter_registros_resueltos(path: str, limite: int = 0) -> Iterable[Dict[str, Any]]:
    if not os.path.isfile(path):
        sys.exit(
            f"[ERROR] No se encuentra {path}.\n"
            "Ejecute antes 040_inyectar_registros.py (genera este fichero al inyectar)."
        )
    with open(path, "r", encoding="utf-8") as f:
        for i, linea in enumerate(f):
            if limite and i >= limite:
                break
            linea = linea.strip()
            if not linea:
                continue
            yield json.loads(linea)


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporta el dataset CARMESI inyectado en Wikibase a Turtle (RiC-O).")
    parser.add_argument("--limit", type=int, default=0, help="Limita el número de documentos exportados (0 = todos).")
    parser.add_argument("--out", type=str, default=os.path.join(LOAD_FILES_PATH, "carmesi_rico_dataset.ttl"),
                         help="Ruta del fichero .ttl de salida.")
    args = parser.parse_args()

    total = sum(1 for _ in iter_registros_resueltos(FICHERO_RESUELTO_PATH, args.limit or 0)) if args.limit == 0 else args.limit
    progreso = Progreso(total, etiqueta="documentos")

    onto = Ontologia(progreso=progreso)
    exportador = ExportadorRDF(onto)

    exportados = errores = 0
    for reg in iter_registros_resueltos(FICHERO_RESUELTO_PATH, args.limit):
        id_doc = reg.get("id_documento", "?")
        try:
            exportador.exportar_documento(reg)
            exportados += 1
            progreso.avanzar(f"Documento {id_doc} exportado")
        except Exception as e:
            errores += 1
            progreso.aviso(f"   ❌ [FALLO] Documento {id_doc}: {e}")
            progreso.avanzar(f"Documento {id_doc} — FALLIDO")

    duplicados = exportador.duplicados_detectados()

    progreso.resumen(
        "RESUMEN DE EXPORTACIÓN RDF",
        documentos_exportados=exportados,
        documentos_fallidos=errores,
        triples_totales=len(exportador.g),
        individuos_vocabulario_controlado=len(exportador._individuos_controlados_emitidos),
        entidades_secundarias=len(exportador._secundarios_emitidos),
        puntos_acceso_compartidos=len(exportador._puntos_acceso_emitidos),
        entidades_de_origen_duplicadas=len(duplicados),
    )

    if duplicados:
        print(
            "\n   ⚠️  AVISO DE CALIDAD DE DATOS (no es un error de este script): "
            f"{len(duplicados)} entidad(es) de origen (persona/institución/materia/lugar) "
            "tienen más de un QID de Wikibase asociado. Esto indica que la reconciliación "
            "de 'entidades_compartidas' en 040_inyectar_registros.py no dedupicó esa entidad "
            "— revise mapa_items_inyectados_wikibase.json y, si procede, funda los ítems "
            "duplicados en Wikibase antes de reinyectar/reexportar a escala completa.\n"
            "   Peores casos (categoría, id_origen, etiqueta, QIDs):"
        )
        for categoria, id_origen, label, qids in duplicados[:15]:
            print(f"     - [{categoria}] id_origen={id_origen!r} \"{label}\" -> {len(qids)} QIDs: {', '.join(qids)}")
        if len(duplicados) > 15:
            print(f"     ... y {len(duplicados) - 15} entidad(es) duplicada(s) más.")

    exportador.g.serialize(destination=args.out, format="turtle")
    print(f"\n   Fichero generado: {args.out}")
    print(f"   Base URI del dataset: {RDF_BASE_URI} (ajuste CARMESI_RDF_BASE_URI antes de publicar)")


if __name__ == "__main__":
    main()
