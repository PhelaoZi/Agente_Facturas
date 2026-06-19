"""Herramientas MCP in-process que el agente usa para publicar artefactos."""
from app.canvas.artifacts import Artifact, Collector


def kpi_artifact(args: dict) -> Artifact:
    return Artifact(
        tipo="kpi",
        titulo=args["etiqueta"],
        payload={
            "etiqueta": args["etiqueta"],
            "valor": args["valor"],
            "delta": args.get("delta", ""),
        },
    )


def grafico_artifact(args: dict) -> Artifact:
    return Artifact(
        tipo="grafico",
        titulo=args["titulo"],
        payload={
            "titulo": args["titulo"],
            "chart_type": args["chart_type"],
            "x": args["x"],
            "y": args["y"],
        },
    )


def tabla_artifact(args: dict) -> Artifact:
    return Artifact(
        tipo="tabla",
        titulo=args["titulo"],
        payload={"columnas": args["columnas"], "filas": args["filas"]},
    )


def informe_artifact(args: dict) -> Artifact:
    return Artifact(
        tipo="informe",
        titulo=args["titulo"],
        payload={"markdown": args["markdown"]},
    )


def build_lienzo_server(collector: Collector):
    """Construye el servidor MCP in-process 'lienzo' ligado a un collector.

    Devuelve (server, lista_de_nombres_de_tools).
    """
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool("publicar_kpi", "Publica un indicador (KPI) en el lienzo.",
          {"etiqueta": str, "valor": str, "delta": str})
    async def publicar_kpi(args):
        collector.add(kpi_artifact(args))
        return {"content": [{"type": "text", "text": f"KPI '{args['etiqueta']}' publicado."}]}

    @tool("publicar_grafico", "Publica un gráfico (chart_type: bar|line|pie) en el lienzo.",
          {"titulo": str, "chart_type": str, "x": list, "y": list})
    async def publicar_grafico(args):
        collector.add(grafico_artifact(args))
        return {"content": [{"type": "text", "text": f"Gráfico '{args['titulo']}' publicado."}]}

    @tool("publicar_tabla", "Publica una tabla (columnas + filas) en el lienzo.",
          {"titulo": str, "columnas": list, "filas": list})
    async def publicar_tabla(args):
        collector.add(tabla_artifact(args))
        return {"content": [{"type": "text", "text": f"Tabla '{args['titulo']}' publicada."}]}

    @tool("publicar_informe", "Publica un informe de texto (markdown) en el lienzo.",
          {"titulo": str, "markdown": str})
    async def publicar_informe(args):
        collector.add(informe_artifact(args))
        return {"content": [{"type": "text", "text": f"Informe '{args['titulo']}' publicado."}]}

    server = create_sdk_mcp_server(
        name="lienzo",
        version="1.0.0",
        tools=[publicar_kpi, publicar_grafico, publicar_tabla, publicar_informe],
    )
    tool_names = [
        "mcp__lienzo__publicar_kpi",
        "mcp__lienzo__publicar_grafico",
        "mcp__lienzo__publicar_tabla",
        "mcp__lienzo__publicar_informe",
    ]
    return server, tool_names
