"""Capa determinista de gastos (cuentas por pagar).

`validar_gasto` es una función pura (gatekeeper) que normaliza y valida los
datos antes de cualquier escritura. `registrar_gasto` ejecuta el INSERT con un
cursor que recibe (la conexión y el commit los maneja quien llama). Replica el
SQL y la normalización de monto de la skill agregar-gasto.
"""
from datetime import datetime, date


def _normalizar_monto(monto):
    """Convierte un monto en float. Acepta número o string en formato chileno
    ('185.000' = miles con punto, coma decimal). Devuelve None si no es válido."""
    if monto is None:
        return None
    if isinstance(monto, (int, float)):
        return float(monto)
    s = str(monto).strip()
    if not s:
        return None
    # Asume montos enteros o con punto de miles ('185.000'); no soporta
    # decimales con coma en strings con miles ('1.234,56').
    s = s.replace("$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def validar_gasto(descripcion, monto, fecha, proveedor=None, categoria=None):
    """Valida y normaliza los datos de un gasto. Lanza ValueError si algo falla."""
    desc = (descripcion or "").strip()
    if not desc:
        raise ValueError("La descripción del gasto no puede estar vacía.")

    monto_limpio = _normalizar_monto(monto)
    if monto_limpio is None or monto_limpio <= 0:
        raise ValueError(f"Monto inválido: {monto!r}. Debe ser un número mayor que 0.")

    try:
        fecha_d = datetime.strptime(str(fecha).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ValueError(f"Fecha inválida: {fecha!r}. Formato esperado: YYYY-MM-DD.")

    return {
        "descripcion": desc,
        "monto": monto_limpio,
        "fecha": fecha_d.isoformat(),
        "proveedor": (proveedor or "").strip() or None,
        "categoria": (categoria or "").strip() or None,
    }


def registrar_gasto(cur, descripcion, monto, fecha, proveedor, categoria):
    """Inserta el gasto en cuentas_por_pagar y devuelve el id nuevo.

    Recibe un cursor (RealDictCursor); el commit lo hace quien llama.
    Mismo SQL que la skill agregar-gasto.
    """
    cur.execute(
        """
        INSERT INTO cuentas_por_pagar
            (descripcion, proveedor, monto, fecha_vencimiento, categoria)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (descripcion, proveedor, monto, fecha, categoria),
    )
    return cur.fetchone()["id"]


def obtener_gasto(cur, id):
    """Devuelve el gasto por id como dict, o None si no existe."""
    cur.execute(
        """
        SELECT id, descripcion, monto, fecha_vencimiento, proveedor, categoria, pagado
        FROM cuentas_por_pagar WHERE id = %s
        """,
        (id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def listar(cur, filtro=None, incluir_pagados=False):
    """Lista gastos ordenados por vencimiento. `filtro` hace ILIKE sobre la
    descripción; por defecto excluye los ya pagados."""
    cond = []
    params = []
    if not incluir_pagados:
        cond.append("(pagado = FALSE OR pagado IS NULL)")
    if filtro:
        cond.append("descripcion ILIKE %s")
        params.append(f"%{filtro}%")
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    cur.execute(
        f"""
        SELECT id, descripcion, monto, fecha_vencimiento, proveedor, categoria, pagado
        FROM cuentas_por_pagar
        {where}
        ORDER BY fecha_vencimiento ASC NULLS LAST, id
        """,
        tuple(params),
    )
    return [dict(r) for r in cur.fetchall()]


def _validar_id(params):
    """Extrae y valida un id de gasto (entero > 0). Lanza ValueError si no sirve."""
    raw = params.get("id")
    try:
        id_ = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Id de gasto inválido: {raw!r}.")
    if id_ <= 0:
        raise ValueError(f"Id de gasto inválido: {id_}.")
    return id_


def validar_borrar(params):
    """Valida los params de borrar: requiere un id entero > 0."""
    return {"id": _validar_id(params)}


def borrar_gasto(cur, id):
    """Borra el gasto por id. Devuelve {id, descripcion, mensaje}.
    Lanza ValueError si el gasto no existe."""
    cur.execute(
        "DELETE FROM cuentas_por_pagar WHERE id = %s RETURNING descripcion",
        (id,),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"El gasto {id} ya no existe.")
    desc = row["descripcion"]
    return {"id": id, "descripcion": desc, "mensaje": f"Gasto borrado: {desc}"}


def validar_marcar_pagado(params):
    """Valida marcar-pagado: id válido; fecha_pago por defecto hoy, si viene
    debe ser YYYY-MM-DD."""
    id_ = _validar_id(params)
    fecha = params.get("fecha_pago") or params.get("fecha")
    if not fecha:
        fecha_pago = date.today().isoformat()
    else:
        try:
            fecha_pago = datetime.strptime(str(fecha).strip(), "%Y-%m-%d").date().isoformat()
        except (ValueError, TypeError):
            raise ValueError(f"Fecha inválida: {fecha!r}. Formato esperado: YYYY-MM-DD.")
    return {"id": id_, "fecha_pago": fecha_pago}


def marcar_gasto_pagado(cur, id, fecha_pago):
    """Marca el gasto como pagado en la fecha dada. Lanza ValueError si no existe."""
    cur.execute(
        """
        UPDATE cuentas_por_pagar SET pagado = TRUE, fecha_pago = %s
        WHERE id = %s RETURNING descripcion
        """,
        (fecha_pago, id),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"El gasto {id} ya no existe.")
    desc = row["descripcion"]
    return {"id": id, "descripcion": desc, "mensaje": f"Gasto marcado como pagado: {desc}"}
