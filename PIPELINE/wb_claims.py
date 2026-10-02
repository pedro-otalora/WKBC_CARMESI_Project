"""
wb_claims.py
============
Construcción de Claims de WikibaseIntegrator a partir del datatype declarado
en 2_creacion_P.tsv. Utilidad compartida por los scripts de inyección
(inyectar_ontologia.py, inyectar_registros.py) para no duplicar esta lógica.
"""

import re
from typing import Callable, Optional

from wikibaseintegrator.datatypes import Item, String, URL, ExternalID, Time, MonolingualText

DATATYPES_VALIDOS = {"wikibase-item", "string", "url", "external-id", "monolingualtext", "time"}

_QID_RE = re.compile(r"^Q\d+$")


def construir_claim_tiempo(pid: str, fecha_str: str) -> Optional[Time]:
    """Convierte cadenas 'YYYY', 'YYYY-MM' o 'YYYY-MM-DD' en un Claim Time de WBI
    con la precisión correspondiente (año=9, mes=10, día=11)."""
    if not fecha_str or not isinstance(fecha_str, str):
        return None

    cadena = fecha_str.strip()

    m = re.match(r"^(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$", cadena)
    if m:
        yyyy, mm, dd = m.groups()
        return Time(time=f"+{yyyy}-{mm}-{dd}T00:00:00Z", prop_nr=pid, precision=11)

    m = re.match(r"^(\d{4})-(0[1-9]|1[0-2])$", cadena)
    if m:
        yyyy, mm = m.groups()
        return Time(time=f"+{yyyy}-{mm}-00T00:00:00Z", prop_nr=pid, precision=10)

    m = re.match(r"^(\d{4})$", cadena)
    if m:
        yyyy = m.group(1)
        return Time(time=f"+{yyyy}-00-00T00:00:00Z", prop_nr=pid, precision=9)

    return None


def construir_claim(
    pid_wb: str,
    datatype: str,
    valor,
    resolver_item: Optional[Callable[[str], Optional[str]]] = None,
):
    """Construye un Claim de WBI según el datatype de la propiedad.

    Para 'wikibase-item': si `valor` ya es un QID (p. ej. "Q57"), se usa tal
    cual; si no, se resuelve mediante `resolver_item(valor)` (obligatorio en
    ese caso). Si la resolución falla (None), no se puede construir el claim
    y se devuelve None — quien llama debe decidir si eso es un error o un
    aviso a registrar.
    """
    if datatype == "wikibase-item":
        qid_destino = valor
        if not _QID_RE.match(str(valor)):
            if not resolver_item:
                raise ValueError(f"'{valor}' no es un QID y no se proporcionó resolver_item")
            qid_destino = resolver_item(valor)
        if not qid_destino:
            return None
        return Item(prop_nr=pid_wb, value=qid_destino)

    elif datatype == "string":
        return String(prop_nr=pid_wb, value=str(valor))

    elif datatype == "url":
        return URL(prop_nr=pid_wb, value=str(valor))

    elif datatype == "external-id":
        return ExternalID(prop_nr=pid_wb, value=str(valor))

    elif datatype == "monolingualtext":
        return MonolingualText(prop_nr=pid_wb, text=str(valor), language="es")

    elif datatype == "time":
        return construir_claim_tiempo(pid_wb, str(valor))

    else:
        raise ValueError(f"Datatype no soportado: {datatype}")