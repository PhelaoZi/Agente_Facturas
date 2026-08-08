"""Herramientas MCP in-process que el agente usa para publicar artefactos."""
from app.canvas.artifacts import Artifact, Collector

# Los parametros que son listas van declarados con JSON Schema completo, NO con
# el atajo `{"x": list}` del decorador @tool: ese atajo emite {"type": "array"}
# sin `items`, y Google AI Studio (Gemini) rechaza la peticion entera con
# HTTP 400 INVALID_ARGUMENT en vez de ignorar el campo como hacen Anthropic,
# OpenAI y GLM. Decir que contiene cada lista tambien mejora los argumentos que
# arma el modelo. Ver tests/test_publish_tools.py::test_todo_array_declara_items.
SCHEMA_GRAFICO = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "chart_type": {"type": "string", "description": "bar, line o pie"},
        "x": {"type": "array", "items": {"type": "string"},
              "description": "Etiquetas del eje X"},
        "y": {"type": "array", "items": {"type": "number"},
              "description": "Valores numericos, uno por etiqueta de x"},
    },
    "required": ["titulo", "chart_type", "x", "y"],
}

SCHEMA_TABLA = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "columnas": {"type": "array", "items": {"type": "string"},
                     "description": "Encabezados de las columnas"},
        "filas": {"type": "array",
                  "items": {"type": "array", "items": {"type": "string"}},
                  "description": "Una lista por fila, con un valor por columna"},
    },
    "required": ["titulo", "columnas", "filas"],
}


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


def build_lienzo_server(collector: Collector, resultados=None):
    """Construye el servidor MCP in-process 'lienzo' ligado a un collector.

    `resultados` (ResultadosSQL) habilita `publicar_consulta`: publicar por
    REFERENCIA lo que devolvió un SELECT, sin que el modelo reescriba las filas.

    Devuelve (server, lista_de_nombres_de_tools).
    """
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool("publicar_kpi", "Publica un indicador (KPI) en el lienzo.",
          {"etiqueta": str, "valor": str, "delta": str})
    async def publicar_kpi(args):
        collector.add(kpi_artifact(args))
        return {"content": [{"type": "text", "text": f"KPI '{args['etiqueta']}' publicado."}]}

    @tool("publicar_grafico", "Publica un gráfico (chart_type: bar|line|pie) en el lienzo.",
          SCHEMA_GRAFICO)
    async def publicar_grafico(args):
        collector.add(grafico_artifact(args))
        return {"content": [{"type": "text", "text": f"Gráfico '{args['titulo']}' publicado."}]}

    @tool("publicar_tabla", "Publica una tabla (columnas + filas) en el lienzo.",
          SCHEMA_TABLA)
    async def publicar_tabla(args):
        collector.add(tabla_artifact(args))
        return {"content": [{"type": "text", "text": f"Tabla '{args['titulo']}' publicada."}]}

    @tool("publicar_informe", "Publica un informe de texto (markdown) en el lienzo.",
          {"titulo": str, "markdown": str})
    async def publicar_informe(args):
        collector.add(informe_artifact(args))
        return {"content": [{"type": "text", "text": f"Informe '{args['titulo']}' publicado."}]}

    @tool("publicar_consulta",
          "Publica en el lienzo el resultado COMPLETO de un SELECT que ya "
          "ejecutaste, usando la referencia (ref) que te devolvió. Úsala en vez "
          "de publicar_tabla cuando las filas vengan de una consulta: no tienes "
          "que reescribirlas y salen exactas.",
          {"ref": str, "titulo": str})
    async def publicar_consulta(args):
        guardado = resultados.obtener(args["ref"]) if resultados else None
        if not guardado:
            # Nunca inventar una tabla vacia: el modelo debe poder corregir.
            return {"content": [{"type": "text", "text":
                f"No existe el resultado '{args['ref']}'. Vuelve a ejecutar la "
                f"consulta y usa la referencia que te devuelva."}],
                "is_error": True}
        collector.add(tabla_artifact({"titulo": args["titulo"],
                                      "columnas": guardado["columnas"],
                                      "filas": guardado["filas"]}))
        return {"content": [{"type": "text", "text":
            f"Tabla '{args['titulo']}' publicada con {len(guardado['filas'])} "
            f"filas. NO repitas las filas en el chat: resume en prosa."}]}

    tools = [publicar_kpi, publicar_grafico, publicar_tabla, publicar_informe]
    tool_names = [
        "mcp__lienzo__publicar_kpi",
        "mcp__lienzo__publicar_grafico",
        "mcp__lienzo__publicar_tabla",
        "mcp__lienzo__publicar_informe",
    ]
    if resultados is not None:
        tools.append(publicar_consulta)
        tool_names.append("mcp__lienzo__publicar_consulta")

    server = create_sdk_mcp_server(name="lienzo", version="1.0.0", tools=tools)
    return server, tool_names
