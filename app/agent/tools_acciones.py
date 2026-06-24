"""Servidor MCP in-process 'acciones': herramientas que PROPONEN una escritura
sin ejecutarla. El agente nunca escribe en la BD; solo publica una tarjeta de
confirmación (Artifact tipo 'accion'). La escritura real la hace el endpoint
determinista del dashboard al apretar Confirmar.

Mismo patrón que app/agent/publish_tools.py (build_lienzo_server).
"""
import psycopg2
from psycopg2.extras import RealDictCursor

from app.canvas.artifacts import Artifact, Collector
from app.config import DB_URL
from app.negocio import gastos
from app.negocio.gastos import validar_gasto


def _pesos(n):
    try:
        return "$" + f"{int(round(float(n))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "$0"


def _fecha_dmy(fecha):
    """Formatea 'YYYY-MM-DD' como 'DD/MM/YYYY'; si no parsea, devuelve el original."""
    from datetime import datetime
    try:
        return datetime.strptime(str(fecha), "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return str(fecha or "")


def _resumen_gasto(params: dict) -> str:
    desc = params.get("descripcion", "")
    extra = f" · {params['proveedor']}" if params.get("proveedor") else ""
    return f"Gasto: {desc} · {_pesos(params.get('monto'))} · vence {_fecha_dmy(params.get('fecha', ''))}{extra}"


def accion_gasto_artifact(params: dict) -> Artifact:
    """Construye el artefacto de acción 'registrar_gasto' a partir de params ya validados."""
    return Artifact(
        tipo="accion",
        titulo="Confirmar gasto",
        payload={
            "tipo_accion": "registrar_gasto",
            "params": params,
            "resumen": _resumen_gasto(params),
        },
    )


def _obtener_gasto(id):
    """Lee un gasto por id con su propia conexión de solo lectura."""
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            return gastos.obtener_gasto(cur, id)
    finally:
        conn.close()


def borrar_gasto_artifact(g) -> Artifact:
    resumen = (f"Borrar: {g['descripcion']} · {_pesos(g['monto'])} · "
               f"vence {_fecha_dmy(g['fecha_vencimiento'])}")
    return Artifact(tipo="accion", titulo="Confirmar borrado",
        payload={"tipo_accion": "borrar_gasto", "params": {"id": g["id"]}, "resumen": resumen})


def marcar_pagado_artifact(g, fecha_pago) -> Artifact:
    resumen = (f"Marcar pagado: {g['descripcion']} · {_pesos(g['monto'])} · "
               f"el {_fecha_dmy(fecha_pago)}")
    return Artifact(tipo="accion", titulo="Confirmar pago de gasto",
        payload={"tipo_accion": "marcar_gasto_pagado",
                 "params": {"id": g["id"], "fecha_pago": fecha_pago}, "resumen": resumen})


def editar_gasto_artifact(g, params, cambios) -> Artifact:
    partes = []
    for col, nuevo in cambios.items():
        viejo = g.get(col)
        if col == "monto":
            partes.append(f"monto {_pesos(viejo)} → {_pesos(nuevo)}")
        elif col == "fecha_vencimiento":
            partes.append(f"vence {_fecha_dmy(viejo)} → {_fecha_dmy(nuevo)}")
        else:
            partes.append(f"{col}: {viejo} → {nuevo}")
    resumen = f"Editar {g['descripcion']}: " + ", ".join(partes)
    return Artifact(tipo="accion", titulo="Confirmar edición",
        payload={"tipo_accion": "editar_gasto", "params": params, "resumen": resumen})


def build_acciones_server(collector: Collector):
    """Construye el servidor MCP 'acciones' ligado a un collector.

    Devuelve (server, lista_de_nombres_de_tools).
    """
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool(
        "proponer_gasto",
        "Propone registrar un gasto (cuenta por pagar) para que el usuario lo "
        "confirme con un botón. NO escribe en la base de datos: solo publica una "
        "tarjeta de confirmación. Úsala cuando el usuario pida anotar/registrar un gasto.",
        {"descripcion": str, "monto": str, "fecha": str, "proveedor": str, "categoria": str},
    )
    async def proponer_gasto(args):
        try:
            limpio = validar_gasto(
                args.get("descripcion"), args.get("monto"), args.get("fecha"),
                args.get("proveedor"), args.get("categoria"))
        except ValueError as e:
            return {"content": [{"type": "text",
                    "text": f"No puedo proponer el gasto: {e} Pídele al usuario el dato que falta."}]}
        collector.add(accion_gasto_artifact(limpio))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — {_resumen_gasto(limpio)}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar para que se registre. "
                        "NO afirmes que el gasto ya quedó registrado."}]}

    @tool("proponer_borrar_gasto",
          "Propone BORRAR un gasto (cuenta por pagar) por su id, para que el usuario "
          "confirme. NO borra: publica una tarjeta. Usa listar_gastos primero para el id.",
          {"id": int})
    async def proponer_borrar_gasto(args):
        g = _obtener_gasto(args.get("id"))
        if not g:
            return {"content": [{"type": "text",
                    "text": f"No encontré un gasto con id {args.get('id')}. Usa listar_gastos para ver los ids."}]}
        collector.add(borrar_gasto_artifact(g))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — Borrar {g['descripcion']}. Quedó como tarjeta; "
                        "el usuario debe apretar Confirmar. NO afirmes que ya se borró."}]}

    @tool("proponer_marcar_gasto_pagado",
          "Propone marcar un gasto como PAGADO por su id (fecha opcional, por defecto hoy). "
          "NO escribe: publica una tarjeta. Usa listar_gastos primero.",
          {"id": int, "fecha": str})
    async def proponer_marcar_gasto_pagado(args):
        g = _obtener_gasto(args.get("id"))
        if not g:
            return {"content": [{"type": "text",
                    "text": f"No encontré un gasto con id {args.get('id')}. Usa listar_gastos para ver los ids."}]}
        from datetime import date
        fecha = (args.get("fecha") or "").strip() or date.today().isoformat()
        collector.add(marcar_pagado_artifact(g, fecha))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — Marcar pagado {g['descripcion']}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar. NO afirmes que ya se pagó."}]}

    @tool("proponer_editar_gasto",
          "Propone EDITAR campos de un gasto por su id (descripcion/monto/fecha/proveedor/categoria). "
          "NO escribe: publica una tarjeta. Usa listar_gastos primero. Pasa solo los campos a cambiar.",
          {"id": int, "descripcion": str, "monto": str, "fecha": str, "proveedor": str, "categoria": str})
    async def proponer_editar_gasto(args):
        g = _obtener_gasto(args.get("id"))
        if not g:
            return {"content": [{"type": "text",
                    "text": f"No encontré un gasto con id {args.get('id')}. Usa listar_gastos para ver los ids."}]}
        params = {"id": g["id"]}
        for campo in ("descripcion", "monto", "fecha", "proveedor", "categoria"):
            v = args.get(campo)
            if v is not None and str(v).strip() != "":
                params[campo] = v
        try:
            clean = gastos.validar_editar(params)
        except ValueError as e:
            return {"content": [{"type": "text", "text": f"No puedo proponer la edición: {e}"}]}
        collector.add(editar_gasto_artifact(g, params, clean["cambios"]))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — Editar {g['descripcion']}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar. NO afirmes que ya se editó."}]}

    server = create_sdk_mcp_server(name="acciones", version="1.0.0", tools=[
        proponer_gasto, proponer_borrar_gasto, proponer_marcar_gasto_pagado, proponer_editar_gasto,
    ])
    tool_names = [
        "mcp__acciones__proponer_gasto",
        "mcp__acciones__proponer_borrar_gasto",
        "mcp__acciones__proponer_marcar_gasto_pagado",
        "mcp__acciones__proponer_editar_gasto",
    ]
    return server, tool_names
