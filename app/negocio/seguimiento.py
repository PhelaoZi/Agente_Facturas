"""Capa determinista del mini-CRM de seguimiento comercial.

Espejo de app/negocio/gastos.py: funciones puras (validar*) que normalizan y
validan antes de cualquier escritura, y funciones de BD (agregar/marcar) que
reciben un cursor (el commit lo maneja quien llama). Alimenta la lista de
seguimiento que el "gerente comercial" propone y el usuario confirma.
"""
from datetime import datetime, date

PRIORIDADES = {"alta", "media"}
ESTADOS_MARCAR = {"contactado", "descartado"}


def _norm_fecha_opt(f):
    """Normaliza una fecha opcional a 'YYYY-MM-DD' o None. ValueError si no parsea."""
    if not f:
        return None
    try:
        return datetime.strptime(str(f).strip(), "%Y-%m-%d").date().isoformat()
    except (ValueError, TypeError):
        raise ValueError(f"Fecha inválida: {f!r}. Formato esperado: YYYY-MM-DD.")


def _validar_id(params):
    raw = params.get("id")
    try:
        id_ = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Id de seguimiento inválido: {raw!r}.")
    if id_ <= 0:
        raise ValueError(f"Id de seguimiento inválido: {id_}.")
    return id_


def validar_agregar(params):
    """Valida y normaliza un alta de seguimiento. ValueError si falta lo obligatorio."""
    rut = (params.get("rut_cliente") or "").strip()
    if not rut:
        raise ValueError("Falta el RUT del cliente para el seguimiento.")
    motivo = (params.get("motivo") or "").strip()
    if not motivo:
        raise ValueError("El motivo del seguimiento no puede estar vacío.")
    prioridad = (params.get("prioridad") or "media").strip().lower()
    if prioridad not in PRIORIDADES:
        raise ValueError(f"Prioridad inválida: {prioridad!r}. Usa 'alta' o 'media'.")
    return {
        "rut_cliente": rut,
        "motivo": motivo,
        "prioridad": prioridad,
        "senales": (params.get("senales") or "").strip() or None,
        "fecha_objetivo": _norm_fecha_opt(params.get("fecha_objetivo")),
        "notas": (params.get("notas") or "").strip() or None,
    }


def hay_pendiente(cur, rut_cliente):
    """True si el cliente ya tiene un seguimiento en estado 'pendiente'."""
    cur.execute(
        "SELECT id FROM seguimiento_comercial "
        "WHERE rut_cliente = %s AND estado = 'pendiente' LIMIT 1",
        (rut_cliente,),
    )
    return cur.fetchone() is not None


def agregar(cur, rut_cliente, motivo, prioridad, senales, fecha_objetivo, notas):
    """Inserta un seguimiento y devuelve {id, mensaje}. Guard: no duplica
    pendientes del mismo cliente (ValueError si ya hay uno)."""
    if hay_pendiente(cur, rut_cliente):
        raise ValueError(f"El cliente {rut_cliente} ya tiene un seguimiento pendiente.")
    cur.execute(
        """
        INSERT INTO seguimiento_comercial
            (rut_cliente, motivo, prioridad, senales, fecha_objetivo, notas)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (rut_cliente, motivo, prioridad, senales, fecha_objetivo, notas),
    )
    new_id = cur.fetchone()["id"]
    return {"id": new_id, "mensaje": f"Seguimiento creado (id {new_id}): {motivo}"}


def validar_marcar(params):
    """Valida marcar: id válido + estado ∈ {contactado, descartado}; fecha por
    defecto hoy."""
    id_ = _validar_id(params)
    estado = (params.get("estado") or "").strip().lower()
    if estado not in ESTADOS_MARCAR:
        raise ValueError(f"Estado inválido: {estado!r}. Usa 'contactado' o 'descartado'.")
    fecha = params.get("fecha_contacto") or params.get("fecha")
    fecha_contacto = _norm_fecha_opt(fecha) if fecha else date.today().isoformat()
    return {"id": id_, "estado": estado, "fecha_contacto": fecha_contacto}


def marcar(cur, id, estado, fecha_contacto):
    """Marca un seguimiento como contactado/descartado. ValueError si no existe."""
    cur.execute(
        """
        UPDATE seguimiento_comercial SET estado = %s, fecha_contacto = %s
        WHERE id = %s RETURNING rut_cliente, motivo
        """,
        (estado, fecha_contacto, id),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"El seguimiento {id} ya no existe.")
    return {"id": id, "mensaje": f"Seguimiento marcado como {estado}: {row['motivo']}"}


def obtener(cur, id):
    """Devuelve el seguimiento por id (con razón social) como dict, o None."""
    cur.execute(
        """
        SELECT s.id, s.rut_cliente, c.razon_social, s.motivo, s.prioridad,
               s.estado, s.senales, s.fecha_creacion, s.fecha_objetivo,
               s.fecha_contacto, s.notas
        FROM seguimiento_comercial s
        LEFT JOIN clientes c ON c.rut_cliente = s.rut_cliente
        WHERE s.id = %s
        """,
        (id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def listar(cur, estado="pendiente"):
    """Lista seguimientos del estado dado (None = todos), alta primero."""
    cur.execute(
        """
        SELECT s.id, s.rut_cliente, c.razon_social, s.motivo, s.prioridad,
               s.estado, s.senales, s.fecha_creacion, s.fecha_objetivo,
               s.fecha_contacto
        FROM seguimiento_comercial s
        LEFT JOIN clientes c ON c.rut_cliente = s.rut_cliente
        WHERE (%s IS NULL OR s.estado = %s)
        ORDER BY CASE s.prioridad WHEN 'alta' THEN 0 ELSE 1 END, s.fecha_creacion
        """,
        (estado, estado),
    )
    return [dict(r) for r in cur.fetchall()]
