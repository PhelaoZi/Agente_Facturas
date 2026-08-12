# -*- coding: utf-8 -*-
"""Ingreso por producto, desde la vista canónica `v_ingreso_producto`.

Es la ÚNICA fuente de dinero por cerveza. No consultar `productos` ni
`v_ventas_producto` para esto: esas no incluyen la línea de logística, que es
cerca de la mitad del precio del barril, y devuelven un tercio de lo real.

La regla que ordena el módulo: **una cifra de plata por producto nunca sale
sola.** Va siempre con su período y su cobertura. Un "$33 millones" no dice si
son de un año o de tres, ni cuánto de eso se estimó — y así fue como se llegó a
este problema.

Sigue el contrato de `app/negocio/`: recibe un cursor, no maneja conexión.
"""

# El ingreso se reparte entre varias cervezas cuando la factura trae más de una
# y la logística no viene desglosada. Eso es una regla de negocio razonable,
# pero nadie puede verificarla contra el documento.
CALIDAD_ESTIMADA = "estimada"


def _alcance(desde, hasta):
    """Frase de alcance para acompañar la cifra.

    La arma Python con los filtros que de verdad llegaron, nunca el modelo, que
    puede olvidarlos.
    """
    if desde and hasta:
        return f"del {desde} al {hasta}"
    if desde:
        return f"desde el {desde}"
    if hasta:
        return f"hasta el {hasta}"
    return "todo el histórico, sin filtro de fecha"


def _cobertura(determinista, estimado):
    total = determinista + estimado
    if not total:
        return "sin ventas en el período consultado"
    pct = round(100 * estimado / total, 1)
    if not pct:
        return "100% determinístico (una sola cerveza por factura)"
    return (f"{100 - pct:.1f}% determinístico y {pct:.1f}% estimado "
            f"(facturas con varias cervezas, logística repartida a prorrata)")


def _filtro_fecha(desde, hasta, params):
    where = ""
    if desde:
        where += " AND fecha_evento >= %s"
        params.append(desde)
    if hasta:
        where += " AND fecha_evento <= %s"
        params.append(hasta)
    return where


def ranking(cur, desde=None, hasta=None, limite=10):
    """Ingreso neto por cerveza, de mayor a menor.

    Modelo de eventos: las facturas suman y las notas de crédito restan, cada
    una una vez.
    """
    # Los dos primeros parámetros son los de las columnas CASE; _filtro_fecha
    # agrega los de fecha si vienen, y el LIMIT va al final.
    params = [CALIDAD_ESTIMADA, CALIDAD_ESTIMADA]
    filtro = _filtro_fecha(desde, hasta, params)

    cur.execute(f"""
        SELECT cerveza,
               SUM(ingreso_neto_atribuido) AS ingreso,
               SUM(unidades)               AS unidades,
               SUM(CASE WHEN calidad <> %s THEN ABS(ingreso_neto_atribuido)
                        ELSE 0 END)        AS determinista,
               SUM(CASE WHEN calidad =  %s THEN ABS(ingreso_neto_atribuido)
                        ELSE 0 END)        AS estimado
        FROM v_ingreso_producto
        WHERE TRUE {filtro}
        GROUP BY cerveza
        ORDER BY ingreso DESC
        LIMIT %s
    """, params + [limite])

    cervezas, determinista_total, estimado_total = [], 0.0, 0.0
    for fila in cur.fetchall():
        determinista = float(fila["determinista"] or 0)
        estimado = float(fila["estimado"] or 0)
        determinista_total += determinista
        estimado_total += estimado
        suma = determinista + estimado
        cervezas.append({
            "cerveza": fila["cerveza"],
            "ingreso": float(fila["ingreso"] or 0),
            "unidades": float(fila["unidades"] or 0),
            "pct_estimado": round(100 * estimado / suma, 1) if suma else 0.0,
        })

    return {
        "cervezas": cervezas,
        "desde": desde,
        "hasta": hasta,
        "alcance": f"Ingreso por cerveza ({_alcance(desde, hasta)})",
        "cobertura": _cobertura(determinista_total, estimado_total),
    }


def por_cerveza(cur, cerveza, desde=None, hasta=None, limite_clientes=5):
    """Ingreso de una cerveza y quiénes se lo compraron."""
    params = [CALIDAD_ESTIMADA, CALIDAD_ESTIMADA, cerveza]
    filtro = _filtro_fecha(desde, hasta, params)

    cur.execute(f"""
        SELECT SUM(ingreso_neto_atribuido) AS ingreso,
               SUM(unidades)               AS unidades,
               SUM(CASE WHEN calidad <> %s THEN ABS(ingreso_neto_atribuido)
                        ELSE 0 END)        AS determinista,
               SUM(CASE WHEN calidad =  %s THEN ABS(ingreso_neto_atribuido)
                        ELSE 0 END)        AS estimado,
               COUNT(DISTINCT folio)       AS n_documentos
        FROM v_ingreso_producto
        WHERE cerveza ILIKE %s {filtro}
    """, params)
    total = cur.fetchone() or {}

    params_clientes = [cerveza]
    filtro_clientes = _filtro_fecha(desde, hasta, params_clientes)
    cur.execute(f"""
        SELECT razon_social,
               SUM(ingreso_neto_atribuido) AS ingreso,
               SUM(unidades)               AS unidades
        FROM v_ingreso_producto
        WHERE cerveza ILIKE %s {filtro_clientes}
        GROUP BY razon_social
        ORDER BY ingreso DESC
        LIMIT %s
    """, params_clientes + [limite_clientes])

    clientes = [
        {"cliente": f["razon_social"],
         "ingreso": float(f["ingreso"] or 0),
         "unidades": float(f["unidades"] or 0)}
        for f in cur.fetchall()
    ]

    determinista = float(total.get("determinista") or 0)
    estimado = float(total.get("estimado") or 0)

    return {
        "cerveza": cerveza,
        "ingreso": float(total.get("ingreso") or 0),
        "unidades": float(total.get("unidades") or 0),
        "n_documentos": int(total.get("n_documentos") or 0),
        "clientes": clientes,
        "desde": desde,
        "hasta": hasta,
        "alcance": f"{cerveza} ({_alcance(desde, hasta)})",
        "cobertura": _cobertura(determinista, estimado),
    }
