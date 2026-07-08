"""Capa determinista de cobranza: marcar facturas de venta como pagadas.

Escritura sobre `ventas.fecha_pago`, la fuente de verdad única del estado de
cobro (NULL = pendiente). `ventas.py` se mantiene de solo lectura; por eso la
escritura vive en este módulo aparte. Misma interfaz que gastos/seguimiento:
`validar_marcar_pagada` es pura (gatekeeper) y `marcar_factura_pagada` recibe
un cursor (la conexión y el commit los maneja quien llama).
"""
from datetime import datetime, date


def _validar_folio(params):
    """Extrae y valida un folio de factura (entero > 0). Lanza ValueError."""
    raw = params.get("folio")
    try:
        folio = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Folio inválido: {raw!r}.")
    if folio <= 0:
        raise ValueError(f"Folio inválido: {folio}.")
    return folio


def validar_marcar_pagada(params):
    """Valida marcar-factura-pagada: folio válido; fecha_pago por defecto hoy.
    Rechaza fechas futuras (un pago no puede ocurrir mañana). Se permite fecha
    anterior a la factura (los prepagos existen)."""
    folio = _validar_folio(params)
    raw = params.get("fecha_pago") or params.get("fecha")
    if not raw:
        fecha_pago = date.today()
    else:
        try:
            fecha_pago = datetime.strptime(str(raw).strip(), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            raise ValueError(f"Fecha inválida: {raw!r}. Formato esperado: YYYY-MM-DD.")
    if fecha_pago > date.today():
        raise ValueError(f"La fecha de pago no puede ser futura: {fecha_pago.isoformat()}.")
    return {"folio": folio, "fecha_pago": fecha_pago.isoformat()}


def obtener_factura(cur, folio):
    """Devuelve la factura (nunca una NC) por folio como dict, o None.
    Incluye el total real (montos ajustados por NC) y el estado de pago."""
    cur.execute(
        """
        SELECT v.folio, v.fecha, v.fecha_pago, v.rut_cliente, c.razon_social,
               COALESCE(v.monto_total_ajustado, v.monto_total) AS total
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE v.folio = %s AND v.tipo_documento != 61
        """,
        (folio,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def marcar_factura_pagada(cur, folio, fecha_pago):
    """Marca la factura como pagada en la fecha dada. Lanza ValueError si la
    factura no existe o si ya tiene fecha_pago (no pisar un pago ya registrado,
    p. ej. por conciliación bancaria)."""
    f = obtener_factura(cur, folio)
    if not f:
        raise ValueError(f"No existe una factura con folio {folio}.")
    if f["fecha_pago"] is not None:
        raise ValueError(
            f"La factura {folio} ya está pagada desde el {f['fecha_pago']}. "
            "No se puede volver a marcar.")
    cur.execute(
        "UPDATE ventas SET fecha_pago = %s WHERE folio = %s AND tipo_documento != 61",
        (fecha_pago, folio),
    )
    return {
        "folio": folio,
        "cliente": f["razon_social"],
        "total": float(f["total"]),
        "mensaje": f"Factura {folio} de {f['razon_social']} marcada como pagada el {fecha_pago}.",
    }
