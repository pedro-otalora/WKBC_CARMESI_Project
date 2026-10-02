# Proyecto Carmesí — ISAD(G) → RiC-CM/RiC-O → Wikibase

Migración de los fondos archivísticos del **Proyecto Carmesí** (Catálogo de Archivos de la Región de Murcia en la Sociedad de la Información) desde su descripción original en norma **ISAD(G)** a un grafo de conocimiento basado en el modelo conceptual **RiC-CM** (Records in Contexts) y su ontología **RiC-O**, implementado sobre una instancia de **Wikibase**.

> **Fuente original de los datos:** Fundación Integra — [regmurcia.com](https://www.regmurcia.com/servlet/s.Sl?METHOD=FRMSENCILLA2&sit=c,373,m,139,serv,Carmesi). Fondos documentales de la Región de Murcia comprendidos entre los siglos **XIII y XIX**, con más de **8.000 documentos** migrados.
>
> **Instancia pública resultante:** [carmesi-test-19.wikibase.cloud](https://carmesi-test-19.wikibase.cloud/)

## ¿Qué hace este repositorio?

Contiene todo el proceso de migración de extremo a extremo:

1. **Extracción** de las fichas ISAD(G) y el cuadro de clasificación directamente de la interfaz web pública del repositorio original, mediante raspado web.
2. **Transformación** de cada ficha al modelo RiC-CM: normalización de fechas (ISO 8601), desglose de lengua y escritura, identificación de puntos de acceso.
3. **Inyección** en Wikibase de la ontología (ítems Q y propiedades P), del cuadro de clasificación y de cada documento, con sus entidades compartidas (agentes, lugares, materias) deduplicadas.
4. **Generación de páginas wiki** (fichas de documento y de serie) que leen en vivo las declaraciones de Wikibase mediante un módulo Lua, de forma que editar una propiedad actualiza automáticamente la ficha visible.
5. **Exportación** del dataset completo a RDF/Turtle, con URIs propias y las IRI oficiales de RiC-O/SKOS/Dublin Core/OWL para las clases y propiedades.

El perfil de aplicación resultante (ítems Q y propiedades P reales, con su correspondencia RiC-CM/RiC-O) está documentado en detalle en [`TEMPLATES/CARMESI-TEST-19_DOCUMENTACION.wikitext`](TEMPLATES/CARMESI-TEST-19_DOCUMENTACION.wikitext).

## Mapa de ficheros

```
WKBC_CARMESI_Project/
├── PIPELINE/                  # Scripts Python del pipeline, numerados por orden de ejecución
│   ├── EXTRACT_FILES/         # Salida de la fase de extracción (raspado web), entrada del pipeline
│   │   └── EXTRACT_FONS/      # Una ficha JSON por fondo documental
│   ├── LOAD_FILES/            # Tablas de inyección y ficheros de control/idempotencia del pipeline
│   ├── requirements.txt       # Dependencias Python del pipeline
│   └── .env.example           # Plantilla de configuración de credenciales e instancia
├── ONTOLOGY/                  # Diseño y documentación visual del modelo ontológico
├── TEMPLATES/                 # Plantillas y páginas wiki publicadas en la instancia Wikibase
└── OUTPUT_RDF/                 # Dataset final exportado en RDF/Turtle
```

> Las carpetas `EXTRACT_FILES/` y `LOAD_FILES/` residen dentro de `PIPELINE/` (y no en la raíz del repositorio), ya que los scripts anclan sus rutas al directorio de `config_carmesi.py` para que el proyecto sea autocontenido y portable sin tocar código.

### `PIPELINE/` — scripts del proceso, en orden de ejecución

| Script | Función |
|---|---|
| `config_carmesi.py` | Configuración central: rutas del proyecto y cliente de conexión a Wikibase (`wikibaseintegrator`). Único fichero a editar si cambia de instancia, credenciales o estructura de carpetas. |
| `progreso.py` | Utilidad común de registro de progreso, reutilizada por todos los scripts de inyección. |
| `wb_claims.py` | Funciones auxiliares para construir y declarar *claims* de Wikibase (con calificadores y referencias) de forma consistente en todo el pipeline. |
| `000_publicar_plantillas.py` | Publica las plantillas wiki (`Template:FichaDocumento`, `Template:FichaSerie`) y el módulo Lua `Module:CustomLabel` desde `TEMPLATES/`. |
| `010_inyectar_ontologia.py` | Crea los ítems Q y las propiedades P de la ontología a partir de `LOAD_FILES/1_creacion_Q.tsv` y `2_creacion_P.tsv`, e inyecta las declaraciones de `3_declaraciones.tsv`. Genera `mapa_ontologia_wikibase.json`. |
| `020_cargar_cuadro_clasificacion.py` | Recrea recursivamente el cuadro de clasificación (agrupación de fondos → fondo → subfondo → serie) como ítems enlazados jerárquicamente. |
| `030_transformar_registros.py` | Normaliza cada ficha ISAD(G) extraída: fechas ISO 8601, desglose de lengua/escritura, identificación de puntos de acceso. Fase puramente local, sin escritura en Wikibase. |
| `040_inyectar_registros.py` | Inyecta cada documento y sus entidades secundarias (fecha, instanciaciones) y compartidas (agentes, lugares, materias), deduplicadas. |
| `045_calcular_fechas_lengua_agrupacion.py` | Calcula y declara, por agregación ascendente, el rango cronológico y la lengua común de cada fondo/subfondo/serie. |
| `050_generar_wikitexto_cuadro.py` | Genera y publica la página wiki `CUADRO_CLASIFICACION` con la jerarquía completa. |
| `060_generar_fichas_series.py` | Genera y publica una página `SR-*` por cada serie documental. |
| `070_generar_fichas_documentos.py` | Genera y publica una página `DOC-*` por cada documento inyectado. |
| `080_exportar_rdf_ttl.py` | Reconstruye el dataset completo en RDF/Turtle a partir de los ficheros de control locales, sin consultar Wikibase en caliente. Produce `OUTPUT_RDF/carmesi_rico_dataset.ttl`. |

### `ONTOLOGY/` — diseño del modelo

- `Ontología y Perfil de Aplicación vTEST-19.gsheet` — tabla maestra del perfil de aplicación (crosswalk ISAD(G) → RiC-CM → Wikibase).
- `ontologia+perfil_carmesi_v19.drawio` / `.pdf` — diagrama visual del modelo de entidades y relaciones.

### `PIPELINE/EXTRACT_FILES/` — entrada del pipeline

- `cuadro_clasificacion_definitivo.json` — cuadro de clasificación completo extraído del repositorio original.
- `documentos_nivel2.json` — índice de documentos de nivel 2.
- `EXTRACT_FONS/FONDO_*.json` — una ficha por fondo documental, tal como se extrajo de la interfaz ISAD(G) original.

### `PIPELINE/LOAD_FILES/` — tablas de inyección y control

- `1_creacion_Q.tsv`, `2_creacion_P.tsv`, `3_declaraciones.tsv` — definición de la ontología a inyectar (ítems, propiedades y declaraciones entre ellas).
- `mapa_ontologia_wikibase.json` — mapa resultante Código local → QID/PID real, generado tras la inyección.
- `mapa_cuadro_wikibase.json` — mapa del cuadro de clasificación ya inyectado.
- `mapa_items_inyectar_wikibase.json` / `mapa_items_inyectados_wikibase.json` — ficheros de control de idempotencia (pendiente de inyectar / ya inyectado).
- `registros_inyectados_resuelto.jsonl` — un documento por línea, con todos sus QID reales ya resueltos.
- `mapa_pagina_cuadro_wikitext.txt`, `mapa_paginas_documentos_wikitext.txt`, `mapa_series_wikitext.txt` — mapas de control de las páginas wiki ya generadas.

### `TEMPLATES/` — documentación y plantillas publicadas en la wiki

- `CARMESI-TEST-19_MAIN.wikitext` — página principal de la instancia.
- `CARMESI-TEST-19_DOCUMENTACION.wikitext` — documentación técnica completa: modelo conceptual, perfil de aplicación, pipeline y limitaciones.
- `CARMESI-TEST-19_CONSULTAS.wikitext` — consultas SPARQL de ejemplo sobre el conjunto de datos.
- `Template_FichaDocumento.wikitext`, `Template_FichaSerie.wikitext` — plantillas wiki de las fichas dinámicas de documento y serie.
- `Module_CustomLabel.lua` — módulo Lua que resuelve en vivo, al renderizar cada ficha, las propiedades del ítem Wikibase correspondiente.

### `OUTPUT_RDF/`

- `carmesi_rico_dataset.ttl` — dataset completo exportado en RDF/Turtle, conforme a RiC-O, con URIs propias y `owl:sameAs` de vuelta a cada ítem Wikibase.

## Cómo ejecutar el pipeline

### Requisitos

- Python 3.9+
- Una instancia de Wikibase accesible (ver [wikibase.cloud](https://wikibase.cloud/) para desplegar una propia) con un usuario con permisos de edición.

### 1. Instalar dependencias

```bash
cd PIPELINE
pip install -r requirements.txt
```

### 2. Configurar las credenciales

```bash
cp .env.example .env
```

Y editar `.env` con los datos reales de su instancia:

```
CARMESI_WIKIBASE_INSTANCE=su-instancia
CARMESI_BOT_USER=su_usuario
CARMESI_BOT_PASSWORD=su_contraseña
```

> ⚠️ `.env` nunca debe subirse al repositorio (ya está excluido en `.gitignore`).

### 3. Ejecutar los scripts en orden

Cada script está numerado según el orden en que debe ejecutarse, y se invoca desde dentro de `PIPELINE/`. Todos son idempotentes: pueden relanzarse sin duplicar lo ya creado, gracias a los ficheros de control de `LOAD_FILES/` (ambas carpetas, `EXTRACT_FILES/` y `LOAD_FILES/`, se resuelven automáticamente como subcarpetas de `PIPELINE/`, sin necesidad de configuración adicional).

```bash
python 000_publicar_plantillas.py
python 010_inyectar_ontologia.py
python 020_cargar_cuadro_clasificacion.py
python 030_transformar_registros.py
python 040_inyectar_registros.py
python 045_calcular_fechas_lengua_agrupacion.py
python 050_generar_wikitexto_cuadro.py
python 060_generar_fichas_series.py
python 070_generar_fichas_documentos.py
python 080_exportar_rdf_ttl.py
```

> La fase de extracción (raspado web previo a `PIPELINE/EXTRACT_FILES/`) no forma parte de este pipeline numerado: es específica del repositorio ISAD(G) de origen y no se considera reutilizable para otras fuentes.

## Documentación técnica completa

Para el detalle del modelo conceptual, las decisiones de modelado razonadas, el perfil de aplicación completo (ítems Q y propiedades P con su correspondencia RiC-CM/RiC-O) y las limitaciones conocidas, véase [`TEMPLATES/CARMESI-TEST-19_DOCUMENTACION.wikitext`](TEMPLATES/CARMESI-TEST-19_DOCUMENTACION.wikitext) — el mismo contenido publicado en la [página de documentación de la instancia](https://carmesi-test-19.wikibase.cloud/wiki/DOCUMENTACION).

## Licencia

Este repositorio se distribue bajo licencia [MIT](LICENSE) para el código del pipeline. Los datos descriptivos originales (fichas ISAD(G)) proceden del Proyecto Carmesí, publicado por la Fundación Integra.
