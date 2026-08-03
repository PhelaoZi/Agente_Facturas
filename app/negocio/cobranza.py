"""Capa determinista de cobranza: cobrar facturas y castigar deuda incobrable.

Dos escrituras, sobre dos fuentes de verdad distintas:

- `ventas.fecha_pago` — estado de cobro de una factura (NULL = pendiente).
- `clientes.estado` — 'activo' o 'incobrable'. Castigar la deuda de un cliente
  que quebró la saca del "por cobrar" en el dashboard, el brief, la wiki y la
  nube, y la deja en un KPI aparte. Es una decisión de cobranza, por eso vive
  aquí y no en `clientes.py`, que se mantiene de solo lectura.

**Castigar NO es cobrar.** Un incobrable jamás debe resolverse marcando la
factura como pagada: eso inventa plata que nunca entró, infla la cobranza
histórica y ensucia el promedio de días de pago con que se proyecta el flujo de
caja. Por eso `fecha_pago` queda intacta en NULL.

`ventas.py` se mantiene de solo lectura; por eso las escrituras viven en este
módulo aparte. Misma interfaz que gastos/seguimiento: las funciones `validar_*`
son puras (gatekeeper) y las de escritura reciben un cursor (la conexión y el
commit los maneja quien llama).
"""
import re
from datetime import datetime, date

# RUT chileno tal como se guarda en la BD: 7-8 dígitos, guión, dígito verificador.
RE_RUT = re.compile(r"^\d{7,8}-[\dK]$")

# Cliente + su deuda pendiente. El LEFT JOIN mantiene al cliente sin facturas
# impagas (deuda 0); el FILTER aplica las reglas canónicas: sin NC, montos
# ajustados y solo lo que sigue con fecha_pago IS NULL.
_PENDIENTE = ("v.tipo_documento != 61 AND v.fecha_pago IS NULL "
              "AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0")
_SQL_CLIENTE_DEUDA = f"""
    SELECT c.rut_cliente, c.razon_social, c.estado,
           COALESCE(SUM(COALESCE(v.monto_total_ajustado, v.monto_total))
                    FILTER (WHERE {_PENDIENTE}), 0) AS deuda,
           COUNT(*) FILTER (WHERE {_PENDIENTE}) AS n_facturas
    FROM clientes c
    LEFT JOIN ventas v ON v.rut_cliente = c.rut_cliente
    WHERE {{filtro}}
    GROUP BY c.rut_cliente, c.razon_social, c.estado
    ORDER BY c.razon_social
"""


def _pesos(n):
    """Formatea un monto como '$188.750' (separador de miles chileno)."""
    try:
        return "$" + f"{int(round(float(n))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "$0"


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
    fecha_pago = _parsear_fecha_pago(raw) if raw else date.today()
    return {"folio": folio, "fecha_pago": fecha_pago.isoformat()}


def _parsear_fecha_pago(raw):
    """Parsea una fecha YYYY-MM-DD y rechaza futuras. Lanza ValueError."""
    try:
        fecha = datetime.strptime(str(raw).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ValueError(f"Fecha inválida: {raw!r}. Formato esperado: YYYY-MM-DD.")
    if fecha > date.today():
        raise ValueError(f"La fecha de pago no puede ser futura: {fecha.isoformat()}.")
    return fecha


def validar_corregir_fecha_pago(params):
    """Valida corregir-fecha-pago: folio válido y fecha OBLIGATORIA (una
    corrección sin fecha explícita no tiene sentido; nada de default a hoy)."""
    folio = _validar_folio(params)
    raw = params.get("fecha_pago") or params.get("fecha")
    if not raw:
        raise ValueError("Para corregir necesitas la fecha de pago correcta (YYYY-MM-DD).")
    return {"folio": folio, "fecha_pago": _parsear_fecha_pago(raw).isoformat()}


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


def validar_rut_cliente(params):
    """Valida y normaliza el RUT del cliente (quita puntos y espacios, mayúsculas).
    Gatekeeper del endpoint: sin BD, solo formato. Lanza ValueError."""
    crudo = str(params.get("rut_cliente") or "").strip().replace(".", "").replace(" ", "")
    rut = crudo.upper()
    if not rut:
        raise ValueError("Falta el RUT del cliente.")
    if not RE_RUT.match(rut):
        raise ValueError(
            f"RUT inválido: {params.get('rut_cliente')!r}. Se espera formato 76861668-K.")
    return {"rut_cliente": rut}


def buscar_clientes(cur, texto):
    """Clientes cuyo nombre o RUT contenga `texto`, con su estado y deuda.
    Devuelve una lista: quien llama decide qué hacer si hay más de uno."""
    cur.execute(_SQL_CLIENTE_DEUDA.format(filtro="c.razon_social ILIKE %s OR c.rut_cliente ILIKE %s"),
                (f"%{texto}%", f"%{texto}%"))
    return [dict(r) for r in cur.fetchall()]


def obtener_cliente(cur, rut_cliente):
    """Devuelve el cliente con su estado y su deuda pendiente, o None.

    La deuda usa las reglas canónicas: excluye NC, montos ajustados, y solo
    facturas con `fecha_pago IS NULL`.
    """
    cur.execute(_SQL_CLIENTE_DEUDA.format(filtro="c.rut_cliente = %s"), (rut_cliente,))
    row = cur.fetchone()
    return dict(row) if row else None


def _cambiar_estado(cur, rut_cliente, nuevo, esperado, error_si_no_cumple):
    """Cambia clientes.estado tras verificar el estado actual. Lanza ValueError
    si el cliente no existe o si ya está en el estado que se pide."""
    c = obtener_cliente(cur, rut_cliente)
    if not c:
        raise ValueError(f"No existe un cliente con RUT {rut_cliente}.")
    if c["estado"] != esperado:
        raise ValueError(error_si_no_cumple.format(cliente=c["razon_social"]))
    cur.execute("UPDATE clientes SET estado = %s WHERE rut_cliente = %s",
                (nuevo, rut_cliente))
    return c


def marcar_cliente_incobrable(cur, rut_cliente):
    """Castiga la deuda de un cliente (quiebra, cierre): estado = 'incobrable'.

    NO toca `ventas.fecha_pago`: las facturas siguen impagas, que es la verdad.
    Solo dejan de contar como crédito cobrable.
    """
    c = _cambiar_estado(cur, rut_cliente, "incobrable", "activo",
                        "El cliente {cliente} ya está marcado como incobrable.")
    deuda, n = float(c["deuda"] or 0), int(c["n_facturas"] or 0)
    return {
        "rut_cliente": rut_cliente,
        "cliente": c["razon_social"],
        "deuda_castigada": deuda,
        "n_facturas": n,
        "mensaje": (f"{c['razon_social']} quedó marcado como incobrable. "
                    f"Sus {n} factura(s) impagas por {_pesos(deuda)} salen del "
                    "por cobrar; siguen registradas como no pagadas."),
    }


def reactivar_cliente(cur, rut_cliente):
    """Deshace el castigo: el cliente vuelve a 'activo' y su deuda al por cobrar."""
    c = _cambiar_estado(cur, rut_cliente, "activo", "incobrable",
                        "El cliente {cliente} no está marcado como incobrable.")
    deuda, n = float(c["deuda"] or 0), int(c["n_facturas"] or 0)
    return {
        "rut_cliente": rut_cliente,
        "cliente": c["razon_social"],
        "deuda_recuperada": deuda,
        "n_facturas": n,
        "mensaje": (f"{c['razon_social']} volvió a estado activo. Sus {n} factura(s) "
                    f"impagas por {_pesos(deuda)} vuelven al por cobrar."),
    }


def corregir_fecha_pago(cur, folio, fecha_pago):
    """Corrige la fecha_pago de una factura YA pagada (fecha mal registrada).
    Lanza ValueError si la factura no existe, no está pagada (para eso está
    marcar_factura_pagada) o ya tiene exactamente esa fecha."""
    f = obtener_factura(cur, folio)
    if not f:
        raise ValueError(f"No existe una factura con folio {folio}.")
    if f["fecha_pago"] is None:
        raise ValueError(
            f"La factura {folio} no está marcada como pagada; no hay fecha que "
            "corregir. Usa marcar_factura_pagada.")
    anterior = str(f["fecha_pago"])
    if anterior == fecha_pago:
        raise ValueError(f"La factura {folio} ya tiene fecha de pago {anterior}.")
    cur.execute(
        "UPDATE ventas SET fecha_pago = %s WHERE folio = %s AND tipo_documento != 61",
        (fecha_pago, folio),
    )
    return {
        "folio": folio,
        "cliente": f["razon_social"],
        "fecha_anterior": anterior,
        "mensaje": (f"Fecha de pago de la factura {folio} de {f['razon_social']} "
                    f"corregida: {anterior} → {fecha_pago}."),
    }
