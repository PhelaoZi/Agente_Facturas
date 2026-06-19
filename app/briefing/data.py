"""Capa de datos de solo lectura para el brief diario.

Cada función recibe un cursor (RealDictCursor) y devuelve estructuras Python
simples y ya agregadas, listas para renderizar. Reglas canónicas del proyecto:
- Monto real = COALESCE(monto_total_ajustado, monto_total)
- Excluir Notas de Crédito: tipo_documento != 61
- Estado de cobro: fecha_pago IS NULL = pendiente
- Excluir clientes 'incobrable' de los totales de deuda
"""


def resumen_cobranza(cur):
    """Deuda total pendiente y su desglose por antigüedad (aging buckets)."""
    cur.execute("""
        SELECT (CURRENT_DATE - v.fecha) AS dias,
               COALESCE(v.monto_total_ajustado, v.monto_total) AS total
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE v.tipo_documento != 61
          AND v.fecha_pago IS NULL
          AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
          AND COALESCE(c.estado, '') <> 'incobrable'
    """)
    buckets = {"al_dia": 0, "d1_30": 0, "d31_60": 0, "d60_mas": 0}
    total = 0
    filas = cur.fetchall()
    for f in filas:
        dias = int(f["dias"])
        monto = float(f["total"])
        total += monto
        if dias <= 0:
            buckets["al_dia"] += monto
        elif dias <= 30:
            buckets["d1_30"] += monto
        elif dias <= 60:
            buckets["d31_60"] += monto
        else:
            buckets["d60_mas"] += monto
    return {"total": total, "n_facturas": len(filas), "buckets": buckets}
