"""Decorador y registro de tools del agente, sin dependencias externas.

Reemplaza a `claude_agent_sdk.tool` y `create_sdk_mcp_server`. El loop del
agente es propio desde el 2026-07-20, así que del SDK solo quedaba el decorador
— y su atajo `{"x": str}` marca TODOS los parámetros como obligatorios, sin
forma de decir que uno es opcional. Eso obligaba al modelo a inventar filtros
que el código ya trataba como opcionales (ver el diseño del 2026-08-09).
"""

# Los únicos tipos que el proyecto declara con el atajo. Cualquier estructura
# (listas, objetos) va con JSON Schema explícito, porque hay que declarar
# `items`: sin eso Google rechaza la petición entera con HTTP 400.
TIPOS_JSON = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
}


class Tool:
    """Una herramienta declarada: metadatos más el handler que la ejecuta."""

    def __init__(self, nombre, descripcion, parametros, opcionales, handler):
        self.nombre = nombre
        self.descripcion = descripcion
        self.parametros = parametros
        self.opcionales = tuple(opcionales)
        self.handler = handler

    def json_schema(self):
        """El JSON Schema de los parámetros, listo para la API del modelo."""
        if _es_schema_explicito(self.parametros):
            return self.parametros
        propiedades = {n: _tipo_a_json(t) for n, t in self.parametros.items()}
        return {
            "type": "object",
            "properties": propiedades,
            "required": [n for n in propiedades if n not in self.opcionales],
        }


def _es_schema_explicito(parametros):
    """Un JSON Schema escrito a mano se usa tal cual (SCHEMA_GRAFICO y
    SCHEMA_TABLA declaran `items` en sus listas)."""
    return (isinstance(parametros.get("type"), str)
            and "properties" in parametros)


def _tipo_a_json(tipo):
    if tipo not in TIPOS_JSON:
        raise ValueError(
            f"Tipo {tipo!r} no soportado por el atajo. Declara la tool con un "
            f"JSON Schema explícito (y si es una lista, con `items`).")
    return TIPOS_JSON[tipo]


def _texto_de(resultado):
    """Junta los bloques de texto del `content` de una tool.

    La forma `{"content": [{"type": "text", ...}]}` es herencia de MCP y se
    conserva a propósito: cambiarla tocaría el cuerpo de las 33 tools y sus
    tests, y mezclaría dos cambios en uno.
    """
    partes = [b["text"] for b in (resultado or {}).get("content", [])
              if isinstance(b.get("text"), str)]
    return "\n".join(partes) if partes else "Ejecutada con éxito."


def tool(nombre, descripcion, parametros, opcionales=()):
    """Declara una tool.

    `opcionales` son los parámetros que el modelo PUEDE omitir; el resto queda
    en `required`. Se declara por nombre y no con trucos de tipos (`str | None`)
    porque "puede omitirse" y "puede ser nulo" no son lo mismo, y así se puede
    buscar con grep.
    """
    def decorador(handler):
        return Tool(nombre, descripcion, parametros, opcionales, handler)
    return decorador


class Registro:
    """Un grupo de tools bajo un prefijo (negocio, lienzo, acciones, memoria)."""

    def __init__(self, prefijo, tools):
        self.prefijo = prefijo
        self._tools = {f"mcp__{prefijo}__{t.nombre}": t for t in tools}

    def nombres(self):
        """Los nombres completos, en orden de declaración."""
        return list(self._tools)

    def tiene(self, nombre_completo):
        return nombre_completo in self._tools

    def schemas_openai(self):
        """Los dicts listos para el array `tools` de la API del modelo."""
        return [
            {
                "type": "function",
                "function": {
                    "name": nombre,
                    "description": t.descripcion,
                    "parameters": t.json_schema(),
                },
            }
            for nombre, t in self._tools.items()
        ]

    async def ejecutar(self, nombre_completo, args):
        """Ejecuta una tool y devuelve su texto.

        Nunca lanza: el nombre y los argumentos los elige el modelo, y un error
        acá abortaría el turno entero, botando todo lo que el agente ya reunió.
        El error vuelve como texto para que pueda corregirse solo.
        """
        t = self._tools.get(nombre_completo)
        if t is None:
            return f"Error: herramienta '{nombre_completo}' desconocida."
        try:
            resultado = await t.handler(args)
        except Exception as e:
            return f"Error ejecutando la herramienta '{t.nombre}': {e}"
        return _texto_de(resultado)
