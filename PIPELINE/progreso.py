"""
progreso.py
============
Utilidad compartida de progreso en consola: una línea dinámica que se
sobreescribe en su sitio (en vez de una línea nueva por elemento procesado)
y un resumen final con formato uniforme. La usan todos los scripts del
pipeline que recorren una lista de elementos (documentos, páginas, fondos,
series, plantillas...).

Uso típico:

    from progreso import Progreso

    p = Progreso(total=len(items), etiqueta="documentos")
    for item in items:
        p.avanzar(f"Procesando '{item}'...")
        ...
        if huele_mal:
            p.aviso(f"'{item}': algo a revisar")  # línea fija, no se pierde
    p.resumen("RESUMEN DE EJECUCIÓN", creados=10, omitidos=2, fallidos=0)
"""

import sys

ANCHO_LINEA = 100


class Progreso:
    def __init__(self, total: int, etiqueta: str = "elementos"):
        self.total = total
        self.etiqueta = etiqueta
        self.actual = 0

    def avanzar(self, texto: str = "") -> None:
        """Incrementa el contador y sobrescribe la línea de progreso."""
        self.actual += 1
        self._escribir(texto)

    def actualizar(self, texto: str) -> None:
        """Sobrescribe la línea de progreso sin avanzar el contador (p. ej.
        para matizar el texto de un mismo ítem en varios sub-pasos)."""
        self._escribir(texto)

    def _escribir(self, texto: str) -> None:
        linea = f"[{self.actual}/{self.total}] {texto}"
        if len(linea) > ANCHO_LINEA:
            linea = linea[: ANCHO_LINEA - 1] + "…"
        sys.stdout.write(f"\r{linea:<{ANCHO_LINEA}}")
        sys.stdout.flush()

    def aviso(self, texto: str) -> None:
        """Imprime una línea que debe conservarse (error, aviso importante)
        sin que la próxima línea de progreso la borre: salta de línea antes
        y deja la línea de progreso siguiente empezar limpia."""
        sys.stdout.write("\n" + texto + "\n")
        sys.stdout.flush()

    def limpiar(self) -> None:
        """Borra la línea dinámica, dejando la consola lista para el resumen."""
        sys.stdout.write("\r" + " " * ANCHO_LINEA + "\r")
        sys.stdout.flush()

    def resumen(self, titulo: str = "RESUMEN DE EJECUCIÓN", **contadores) -> None:
        """Limpia la línea dinámica e imprime un resumen final uniforme a
        partir de contadores con nombre: resumen(creados=10, fallidos=0)."""
        self.limpiar()
        print(f"\n=== {titulo} ===")
        for nombre, valor in contadores.items():
            print(f"   - {nombre.replace('_', ' ').capitalize()}: {valor}")
