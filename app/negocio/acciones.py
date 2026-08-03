"""Registro de acciones de escritura confirmadas (propose/confirm/execute).

Cada acción es un par (validar, ejecutar) de interfaz uniforme:
- validar(params: dict) -> dict limpio (lanza ValueError si algo está mal)
- ejecutar(cur, clean: dict) -> dict resultado {mensaje, id?}

El endpoint determinista usa `validar` (sin BD → 400) y `ejecutar` (con BD →
500/400). El agente nunca escribe: solo propone artefactos `accion`.
"""
from app.negocio import cobranza
from app.negocio import gastos
from app.negocio import seguimiento


def _validar_registrar(params):
    return gastos.validar_gasto(
        params.get("descripcion"), params.get("monto"), params.get("fecha"),
        params.get("proveedor"), params.get("categoria"))


def _ejecutar_registrar(cur, clean):
    new_id = gastos.registrar_gasto(cur, **clean)
    monto_fmt = "$" + f"{int(round(float(clean['monto']))):,}".replace(",", ".")
    return {"id": new_id,
            "mensaje": f"Gasto registrado (id {new_id}): {clean['descripcion']} · {monto_fmt}"}


def _ejecutar_borrar(cur, clean):
    return gastos.borrar_gasto(cur, clean["id"])


def _ejecutar_editar(cur, clean):
    return gastos.editar_gasto(cur, clean["id"], clean["cambios"])


def _ejecutar_marcar_pagado(cur, clean):
    return gastos.marcar_gasto_pagado(cur, clean["id"], clean["fecha_pago"])


def _ejecutar_agregar_seguimiento(cur, clean):
    return seguimiento.agregar(cur, **clean)


def _ejecutar_marcar_seguimiento(cur, clean):
    return seguimiento.marcar(cur, clean["id"], clean["estado"], clean["fecha_contacto"])


def _ejecutar_marcar_factura_pagada(cur, clean):
    return cobranza.marcar_factura_pagada(cur, clean["folio"], clean["fecha_pago"])


def _ejecutar_corregir_fecha_pago(cur, clean):
    return cobranza.corregir_fecha_pago(cur, clean["folio"], clean["fecha_pago"])


def _ejecutar_marcar_cliente_incobrable(cur, clean):
    return cobranza.marcar_cliente_incobrable(cur, clean["rut_cliente"])


def _ejecutar_reactivar_cliente(cur, clean):
    return cobranza.reactivar_cliente(cur, clean["rut_cliente"])


ACCIONES = {
    "registrar_gasto":     (_validar_registrar, _ejecutar_registrar),
    "borrar_gasto":        (gastos.validar_borrar, _ejecutar_borrar),
    "editar_gasto":        (gastos.validar_editar, _ejecutar_editar),
    "marcar_gasto_pagado": (gastos.validar_marcar_pagado, _ejecutar_marcar_pagado),
    "agregar_seguimiento": (seguimiento.validar_agregar, _ejecutar_agregar_seguimiento),
    "marcar_seguimiento":  (seguimiento.validar_marcar, _ejecutar_marcar_seguimiento),
    "marcar_factura_pagada": (cobranza.validar_marcar_pagada, _ejecutar_marcar_factura_pagada),
    "corregir_fecha_pago":   (cobranza.validar_corregir_fecha_pago, _ejecutar_corregir_fecha_pago),
    # Castigo de deuda incobrable: escribe clientes.estado, NUNCA ventas.fecha_pago.
    "marcar_cliente_incobrable": (cobranza.validar_rut_cliente, _ejecutar_marcar_cliente_incobrable),
    "reactivar_cliente":         (cobranza.validar_rut_cliente, _ejecutar_reactivar_cliente),
}


def validar(tipo_accion, params):
    """Valida los params de una acción. Lanza ValueError si el tipo es
    desconocido o los params no sirven."""
    if tipo_accion not in ACCIONES:
        raise ValueError(f"Acción desconocida: {tipo_accion!r}")
    return ACCIONES[tipo_accion][0](params or {})


def ejecutar(cur, tipo_accion, clean):
    """Ejecuta una acción ya validada. Lanza ValueError si el tipo es desconocido."""
    if tipo_accion not in ACCIONES:
        raise ValueError(f"Acción desconocida: {tipo_accion!r}")
    return ACCIONES[tipo_accion][1](cur, clean)
