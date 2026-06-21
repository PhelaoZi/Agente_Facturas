"""Consultas de ventas de solo lectura. Reutiliza el SQL probado de
.claude/skills/consultar-ventas/scripts/query_ventas.py, devolviendo datos
estructurados en vez de imprimir. Regla canónica: monto real =
COALESCE(monto_total_ajustado, monto_total); las NC (tipo 61) se excluyen.
"""


def total(cur, desde=None, hasta=None):
    """Total vendido (neto de NC). Global, o por rango de fechas si se pasan ambas."""
    if desde and hasta:
        cur.execute("""
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(COALESCE(v.monto_total_ajustado, v.monto_total)), 0) AS total
            FROM ventas v
            WHERE v.tipo_documento != 61 AND v.fecha BETWEEN %s AND %s
        """, (desde, hasta))
    else:
        cur.execute("""
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(COALESCE(v.monto_total_ajustado, v.monto_total)), 0) AS total
            FROM ventas v
            WHERE v.tipo_documento != 61
        """)
    f = cur.fetchone()
    return {"n": int(f["n"]), "total": float(f["total"]), "desde": desde, "hasta": hasta}


def ranking(cur, limite=10):
    """Top N clientes por venta real (neto de NC)."""
    cur.execute("""
        SELECT c.razon_social, v.rut_cliente,
               SUM(COALESCE(v.monto_total_ajustado, v.monto_total)) AS total_real
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE v.tipo_documento != 61
        GROUP BY v.rut_cliente, c.razon_social
        ORDER BY total_real DESC
        LIMIT %s
    """, (limite,))
    return [
        {"cliente": f["razon_social"], "rut": f["rut_cliente"], "total": float(f["total_real"])}
        for f in cur.fetchall()
    ]
