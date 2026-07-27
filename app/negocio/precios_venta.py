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


# Tolerancia en pesos para el residual: los montos del SII son enteros y
# arrastran redondeos de un peso.
TOLERANCIA_PESOS = 1.0

SQL_RECETAS = "SELECT nombre_cerveza FROM recetas"

# Solo facturas de venta sin nota de credito aplicada: una anulada no dice a
# cuanto se vende, y una parcial rebajaria el neto sin tocar las lineas de
# `productos`, dejando el residual corto.
SQL_LINEAS = """
    SELECT v.folio, v.fecha, v.monto_neto,
           p.nombre_producto, p.cantidad, p.total_linea
    FROM ventas v
    JOIN productos p ON p.folio = v.folio
                    AND p.tipo_documento = v.tipo_documento
    {join_cliente}
    WHERE v.tipo_documento != 61
      AND v.monto_neto_ajustado IS NULL
      AND v.monto_neto > 0
      {filtro_cliente}
    ORDER BY v.fecha, v.folio, p.id
"""

# Un cliente con descuento paga otro precio: el mismo algoritmo, aplicado solo
# a sus facturas, responde "¿cuánto me deja ESTE cliente?".
JOIN_CLIENTE = "JOIN clientes c ON c.rut_cliente = v.rut_cliente"
FILTRO_CLIENTE = "AND (c.razon_social ILIKE %s OR v.rut_cliente ILIKE %s)"


def _sql_lineas(cliente=None):
    """(sql, params) para la consulta de líneas, con filtro de cliente opcional."""
    if not cliente:
        return SQL_LINEAS.format(join_cliente="", filtro_cliente=""), ()
    sql = SQL_LINEAS.format(join_cliente=JOIN_CLIENTE, filtro_cliente=FILTRO_CLIENTE)
    return sql, (f"%{cliente}%", f"%{cliente}%")


def _leer_linea(fila, recetas):
    """Convierte una fila cruda en el registro con que trabaja el algoritmo."""
    nombre_norm = _norm(fila["nombre_producto"])
    clase = _clase(nombre_norm)
    familia, capacidad_ml = _familia_y_capacidad(nombre_norm)
    return {
        "nombre_norm": nombre_norm,
        "clase": clase,
        "familia": familia,
        "capacidad_ml": capacidad_ml,
        "cerveza": _detectar_cerveza(nombre_norm, recetas) if clase != "pass_through" else None,
        "cantidad": float(fila["cantidad"] or 0),
        "total_linea": float(fila["total_linea"] or 0),
        "logistica": 0.0,
    }


def _atribuir_nombrada(logisticas, cervezas):
    """Primera pasada: cada logistica que identifique UNA sola linea de cerveza
    le entrega su monto. Devuelve las que quedaron sin atribuir.

    El selector es la cerveza que nombra y/o la capacidad que nombra. La
    capacidad es necesaria por casos como "Logistica Barril 25L" (folio 4572),
    que no nombra cerveza pero senala sin ambiguedad al unico barril de 25L.
    """
    sin_atribuir = []
    for log in logisticas:
        _familia, capacidad = _familia_y_capacidad(log["nombre_norm"])
        candidatas = [
            c for c in cervezas
            if (log["cerveza"] is None or c["cerveza"] == log["cerveza"])
            and (capacidad is None or c["capacidad_ml"] == capacidad)
        ]
        # Un selector vacio calzaria con todas: eso no identifica nada.
        identifica_algo = log["cerveza"] is not None or capacidad is not None
        if identifica_algo and len(candidatas) == 1:
            candidatas[0]["logistica"] += log["total_linea"]
        else:
            sin_atribuir.append(log)
    return sin_atribuir


def _repartir_residual(residual, pendientes):
    """Segunda pasada: la logistica sin nombrar se reparte entre las lineas que
    no recibieron ninguna. Devuelve el motivo de descarte, o None si salio bien.

    En barriles se reparte POR LITRO, porque un barril parcial pago menos
    logistica en la misma proporcion en que lleva menos cerveza. En botellas y
    latas se reparte POR UNIDAD: son todas del mismo tamano y asi una errata de
    capacidad ("33cc" por "330cc") no deforma el reparto.
    """
    if residual <= TOLERANCIA_PESOS:
        return None
    if not pendientes:
        return "sin_base_de_reparto"
    familias = {c["familia"] for c in pendientes}
    if len(familias) > 1:
        return "familia_mixta"

    familia = familias.pop()
    if familia == "barril":
        pesos = [c["cantidad"] * (c["capacidad_ml"] or 0) / 1000.0 for c in pendientes]
    else:
        pesos = [c["cantidad"] for c in pendientes]
    total = sum(pesos)
    if total <= 0:
        return "sin_base_de_reparto"
    for linea, peso in zip(pendientes, pesos):
        linea["logistica"] += residual * peso / total
    return None


def _precio_de_linea(linea):
    """Precio neto por unidad, normalizado a barril de 30L cuando corresponde."""
    if linea["cantidad"] <= 0:
        return None
    precio = (linea["total_linea"] + linea["logistica"]) / linea["cantidad"]
    if linea["familia"] == "barril":
        litros = (linea["capacidad_ml"] or 0) / 1000.0
        if litros <= 0:
            return None
        precio *= LITROS_BARRIL_REFERENCIA / litros
    return precio


def _procesar_factura(filas, recetas, descartadas):
    """Devuelve las muestras de precio de una factura: (cerveza, formato, precio,
    unidades). Una factura ambigua no aporta ninguna y se cuenta aparte."""
    lineas = [_leer_linea(f, recetas) for f in filas]
    cervezas = [l for l in lineas if l["clase"] == "cerveza" and l["familia"]]
    logisticas = [l for l in lineas if l["clase"] == "logistica"]

    sin_atribuir = _atribuir_nombrada(logisticas, cervezas)

    # El residual es la linea "Logistica" exacta, que parse_dte no guarda en
    # `productos` (ITEMS_NO_CATALOGO), mas las logisticas que no identificaron
    # a nadie ("Logistic", "Logistica Cream/Scotch").
    neto = float(filas[0]["monto_neto"] or 0)
    residual = neto - sum(l["total_linea"] for l in lineas)
    residual += sum(l["total_linea"] for l in sin_atribuir)

    if residual < -TOLERANCIA_PESOS:
        descartadas["residual_negativo"] += 1
        return []

    pendientes = [c for c in cervezas if c["logistica"] == 0.0]
    motivo = _repartir_residual(residual, pendientes)
    if motivo:
        descartadas[motivo] += 1
        return []

    muestras = []
    for linea in cervezas:
        if not linea["cerveza"]:
            continue                      # no esta en el catalogo de recetas
        clave = clave_formato(linea["familia"], linea["capacidad_ml"])
        precio = _precio_de_linea(linea)
        if clave and precio is not None:
            muestras.append((linea["cerveza"], clave, precio, linea["cantidad"]))
    return muestras


def precios_por_formato(cur, dias=None, cliente=None):
    """Precio neto de venta por (cerveza, formato), deducido de las facturas.

    `dias` limita el PROMEDIO a los ultimos N dias (None = todo el historico);
    `precio_ultimo` sale siempre del historico completo.
    `cliente` (nombre o RUT, busqueda parcial) acota el calculo a las facturas
    de ese cliente: sirve para saber a que precio le vende uno con descuento.
    """
    cur.execute(SQL_RECETAS)
    recetas = [r["nombre_cerveza"] for r in cur.fetchall()]

    sql, params = _sql_lineas(cliente)
    cur.execute(sql, params)
    por_factura = defaultdict(list)
    for fila in cur.fetchall():
        por_factura[fila["folio"]].append(fila)

    descartadas = defaultdict(int)
    # Una factura puede traer la misma cerveza en dos lineas (un barril lleno y
    # uno parcial): se promedian ponderadas por unidades para que cada factura
    # aporte UNA muestra por formato.
    muestras = defaultdict(list)
    for folio, filas in por_factura.items():
        for cerveza, clave, precio, unidades in _procesar_factura(filas, recetas, descartadas):
            muestras[(cerveza, clave)].append(
                {"folio": folio, "fecha": filas[0]["fecha"], "precio": precio,
                 "unidades": unidades})

    corte = (date.today() - timedelta(days=dias)) if dias else None
    precios = []
    for (cerveza, clave), lista in muestras.items():
        por_folio = defaultdict(list)
        for m in lista:
            por_folio[m["folio"]].append(m)

        agregadas = []
        for folio, ms in por_folio.items():
            unidades = sum(m["unidades"] for m in ms) or 1.0
            precio = sum(m["precio"] * m["unidades"] for m in ms) / unidades
            agregadas.append({"folio": folio, "fecha": ms[0]["fecha"], "precio": precio})

        agregadas.sort(key=lambda m: (m["fecha"], m["folio"]))
        ultima = agregadas[-1]
        ventana = [m for m in agregadas if corte is None or m["fecha"] >= corte] or agregadas

        precios.append({
            "cerveza": cerveza,
            "formato": clave,
            "precio_ultimo": round(ultima["precio"], 2),
            "fecha_ultimo": ultima["fecha"],
            "folio_ultimo": ultima["folio"],
            "precio_promedio": round(sum(m["precio"] for m in ventana) / len(ventana), 2),
            "n_facturas": len(ventana),
        })

    precios.sort(key=lambda p: (p["cerveza"], p["formato"]))
    return {"precios": precios, "descartadas": dict(descartadas)}
