"""Servidor MCP in-process 'acciones': herramientas que PROPONEN una escritura
sin ejecutarla. El agente nunca escribe en la BD; solo publica una tarjeta de
confirmación (Artifact tipo 'accion'). La escritura real la hace el endpoint
determinista del dashboard al apretar Confirmar.

Mismo patrón que app/agent/publish_tools.py (build_lienzo_server).
"""
from app.canvas.artifacts import Artifact, Collector
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

    server = create_sdk_mcp_server(name="acciones", version="1.0.0", tools=[proponer_gasto])
    tool_names = ["mcp__acciones__proponer_gasto"]
    return server, tool_names
