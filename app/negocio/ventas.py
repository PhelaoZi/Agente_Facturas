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


def por_cliente(cur, nombre):
    """Documentos de un cliente. Separa facturas de NC; el total real suma
    solo facturas (las NC ya están descontadas en los montos ajustados)."""
    cur.execute("""
        SELECT v.folio, v.tipo_documento, v.fecha,
               CASE WHEN v.tipo_documento = 61 THEN v.monto_total
                    ELSE COALESCE(v.monto_total_ajustado, v.monto_total)
               END AS monto
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE c.razon_social ILIKE %s
        ORDER BY v.fecha DESC
    """, (f"%{nombre}%",))
    filas = cur.fetchall()
    facturas = [r for r in filas if int(r["tipo_documento"]) != 61]
    return {
        "nombre_consultado": nombre,
        "n_facturas": len(facturas),
        "n_notas_credito": len(filas) - len(facturas),
        "total_real": sum(float(r["monto"]) for r in facturas),
        "documentos": [
            {"folio": r["folio"], "tipo": int(r["tipo_documento"]),
             "fecha": r["fecha"], "monto": float(r["monto"])}
            for r in filas
        ],
    }


def por_producto(cur, nombre):
    """Líneas de detalle que coinciden con un producto (excluye NC)."""
    cur.execute("""
        SELECT p.folio, v.fecha, c.razon_social, p.nombre_producto,
               p.cantidad, p.precio_unitario
        FROM productos p
        JOIN ventas v ON v.folio = p.folio
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE p.nombre_producto ILIKE %s AND v.tipo_documento != 61
        ORDER BY v.fecha DESC
    """, (f"%{nombre}%",))
    return [
        {"folio": r["folio"], "fecha": r["fecha"], "cliente": r["razon_social"],
         "producto": r["nombre_producto"], "cantidad": r["cantidad"],
         "precio_unitario": (float(r["precio_unitario"])
                             if r["precio_unitario"] is not None else None)}
        for r in cur.fetchall()
    ]
