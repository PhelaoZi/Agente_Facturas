"""Precio de venta real por formato, deducido de las facturas (solo lectura).

No existe una lista de precios en la base: el precio real de un barril o una
botella es la SUMA de la linea de producto mas la linea de logistica que le
corresponde (estructura de doble linea, ver CLAUDE.md). Este modulo reconstruye
esa suma leyendo `ventas` + `productos`.

Vive aparte de costos.py a proposito: costos.py habla con la capa de costos
(recetas, insumos, SKU) y esto habla con la capa de ventas. La dependencia va en
un solo sentido (costos.py importa este modulo, nunca al reves), por eso `_norm`
esta duplicado en ambos en vez de compartirse.
"""
import re
import unicodedata
from collections import defaultdict
from datetime import date, timedelta

# Todos los barriles son de 30L. Cuando los ultimos litros del fermentador no
# alcanzan a llenar uno, se despacha ese mismo barril con 20 o 25 litros y se
# factura como "Barril 25L": el precio escala con los litros. Por eso el precio
# se normaliza a este tamaño y queda UNA serie por cerveza en vez de tres.
LITROS_BARRIL_REFERENCIA = 30.0
CAPACIDAD_BARRIL_ESTANDAR_ML = 30000

# El envase PET es el costo del envase desechable traspasado al cliente.
_RE_PET = re.compile(r"^(barril(es)?\s+)?pet\b")

# Primer numero seguido de una unidad de volumen. El orden de la alternancia
# importa: las unidades largas van primero para que "ml" no se lea como "l".
# "cc?" cubre la errata "330c" del folio 4732.
_RE_CAPACIDAD = re.compile(r"(\d+(?:[.,]\d+)?)\s*(litros?|lts|lt|ml|cc?|l)\b")

# "barr?il" tolera la errata "Baril" (folios 4286 y 4518).
_FAMILIAS = [
    ("barril", re.compile(r"\bbarr?il(es)?\b")),
    ("botella", re.compile(r"\bbotellas?\b")),
    ("lata", re.compile(r"\blatas?\b")),
]

_RE_PALABRAS = re.compile(r"[a-z0-9]+")


def _norm(s):
    """Minusculas, sin tildes, espacios simples."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def _clase(nombre_norm):
    """Que es esta linea de la factura.

    - "logistica": desglose tributario, es parte del precio de la cerveza.
    - "pass_through": envase PET o carga de CO2, costo traspasado sin margen.
    - "cerveza": todo lo demas.
    """
    if "logist" in nombre_norm:
        return "logistica"
    if _RE_PET.match(nombre_norm) or "co2" in nombre_norm:
        return "pass_through"
    return "cerveza"


def _familia_y_capacidad(nombre_norm):
    """(familia, capacidad_ml) de una linea, o (None, None) si no se reconoce.

    Un barril sin capacidad escrita se asume de 30L: es el estandar, y los de
    20 o 25 siempre la escriben porque justamente son la excepcion.
    """
    familia = None
    for nombre_familia, patron in _FAMILIAS:
        if patron.search(nombre_norm):
            familia = nombre_familia
            break
    if familia is None:
        return None, None

    m = _RE_CAPACIDAD.search(nombre_norm)
    if m:
        valor = float(m.group(1).replace(",", "."))
        unidad = m.group(2)
        capacidad_ml = valor * 1000 if unidad.startswith("l") else valor
        return familia, int(round(capacidad_ml))
    if familia == "barril":
        return familia, CAPACIDAD_BARRIL_ESTANDAR_ML
    return familia, None


def _detectar_cerveza(nombre_norm, recetas):
    """Que cerveza nombra esta linea, o None.

    La factura casi nunca escribe el nombre completo de la receta ("Barril 30L
    Stout cafe/ca" para "Stout Café/Cacao"), asi que se cuenta cuantas palabras
    de la receta aparecen en la linea y gana la que calza en mas. Un empate
    devuelve None: preferimos no atribuir antes que atribuir mal.
    """
    palabras_linea = set(_RE_PALABRAS.findall(nombre_norm))
    mejor, mejor_puntaje, empatada = None, 0, False
    for receta in recetas:
        palabras = _RE_PALABRAS.findall(_norm(receta))
        puntaje = sum(1 for p in palabras if p in palabras_linea)
        if puntaje > mejor_puntaje:
            mejor, mejor_puntaje, empatada = receta, puntaje, False
        elif puntaje == mejor_puntaje and puntaje > 0:
            empatada = True
    if mejor_puntaje == 0 or empatada:
        return None
    return mejor


def clave_formato(familia, capacidad_ml):
    """Clave con que se agrupa un precio. Todos los barriles comparten clave
    porque su precio ya viene normalizado a 30L."""
    if familia is None:
        return None
    if familia == "barril":
        return "barril 30L"
    if capacidad_ml is None:
        return None
    return f"{familia} {int(capacidad_ml)}"


def clave_formato_desde_nombre(nombre):
    """Clave de formato a partir de un nombre suelto. La usa margenes() sobre
    los nombres de la tabla `formatos` ("Barril 30L acero", "Botella 330ml")."""
    familia, capacidad_ml = _familia_y_capacidad(_norm(nombre))
    return clave_formato(familia, capacidad_ml)
