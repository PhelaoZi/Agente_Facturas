# tests/test_tools_base.py
"""Decorador y registro propios de tools (reemplazo del SDK de Anthropic)."""
import asyncio

from app.agent.tools_base import Registro, tool


def _saludar():
    @tool("saludar", "Saluda a alguien.", {"nombre": str, "apodo": str},
          opcionales=("apodo",))
    async def saludar(args):
        quien = args.get("apodo") or args["nombre"]
        return {"content": [{"type": "text", "text": f"Hola {quien}"}]}
    return saludar


def test_el_schema_lleva_el_nombre_con_prefijo_del_registro():
    """El modelo llama a las tools por `mcp__servidor__tool`; el prefijo lo pone
    el registro, no cada tool."""
    reg = Registro("prueba", [_saludar()])

    schemas = reg.schemas_openai()

    assert len(schemas) == 1
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "mcp__prueba__saludar"
    assert schemas[0]["function"]["description"] == "Saluda a alguien."


def test_el_atajo_de_tipos_se_traduce_a_json_schema():
    reg = Registro("prueba", [_saludar()])

    params = reg.schemas_openai()[0]["function"]["parameters"]

    assert params["type"] == "object"
    assert params["properties"] == {"nombre": {"type": "string"},
                                    "apodo": {"type": "string"}}


def test_los_opcionales_quedan_fuera_de_required():
    """El motivo de todo este modulo: el atajo del SDK marcaba TODO obligatorio,
    asi que el modelo tenia que inventar filtros que el codigo ya trataba como
    opcionales."""
    reg = Registro("prueba", [_saludar()])

    params = reg.schemas_openai()[0]["function"]["parameters"]

    assert params["required"] == ["nombre"]


def test_traduce_los_cuatro_tipos_que_usa_el_proyecto():
    @tool("tipos", "d", {"s": str, "i": int, "f": float, "b": bool})
    async def tipos(args):
        return {"content": []}

    props = Registro("p", [tipos]).schemas_openai()[0]["function"]["parameters"]["properties"]

    assert props == {"s": {"type": "string"}, "i": {"type": "integer"},
                     "f": {"type": "number"}, "b": {"type": "boolean"}}


def test_un_json_schema_explicito_se_usa_tal_cual():
    """Las listas se declaran a mano con `items` (Google rechaza la peticion
    entera si falta). Ese schema no se toca."""
    schema = {"type": "object",
              "properties": {"x": {"type": "array", "items": {"type": "string"}}},
              "required": ["x"]}

    @tool("grafico", "d", schema)
    async def grafico(args):
        return {"content": []}

    params = Registro("p", [grafico]).schemas_openai()[0]["function"]["parameters"]

    assert params == schema


def test_ejecutar_llama_al_handler_y_devuelve_su_texto():
    reg = Registro("prueba", [_saludar()])

    texto = asyncio.run(reg.ejecutar("mcp__prueba__saludar", {"nombre": "Christian"}))

    assert texto == "Hola Christian"


def test_ejecutar_junta_los_bloques_de_texto_de_la_respuesta():
    @tool("varios", "d", {})
    async def varios(args):
        return {"content": [{"type": "text", "text": "uno"},
                            {"type": "text", "text": "dos"}]}

    texto = asyncio.run(Registro("p", [varios]).ejecutar("mcp__p__varios", {}))

    assert texto == "uno\ndos"


def test_ejecutar_avisa_cuando_una_tool_no_devuelve_texto():
    """Las tools del lienzo dibujan y no informan nada; el modelo igual necesita
    saber que la llamada salio bien."""
    @tool("muda", "d", {})
    async def muda(args):
        return {"content": []}

    texto = asyncio.run(Registro("p", [muda]).ejecutar("mcp__p__muda", {}))

    assert texto == "Ejecutada con éxito."


def test_una_tool_que_falla_no_voltea_el_turno():
    """Un error de BD tiene que volver como resultado legible, no como excepcion:
    si aborta el turno, el usuario pierde todo lo que el agente ya reunio."""
    @tool("rota", "d", {})
    async def rota(args):
        raise RuntimeError("Postgres caído")

    texto = asyncio.run(Registro("p", [rota]).ejecutar("mcp__p__rota", {}))

    assert "Postgres caído" in texto
    assert "rota" in texto


def test_una_tool_desconocida_devuelve_error_y_no_excepcion():
    """El nombre lo elige el modelo: puede inventarlo."""
    reg = Registro("prueba", [_saludar()])

    texto = asyncio.run(reg.ejecutar("mcp__prueba__inventada", {}))

    assert "inventada" in texto
    assert "desconocida" in texto.lower()


def test_nombres_devuelve_los_nombres_completos():
    reg = Registro("prueba", [_saludar()])

    assert reg.nombres() == ["mcp__prueba__saludar"]
