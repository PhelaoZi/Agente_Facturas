"""Tests del decorador _tool_seguro: un error de BD no debe abortar el turno
del agente, sino devolver un resultado de tool con is_error=True."""
import asyncio

import psycopg2
import pytest

from app.agent.tools_negocio import _tool_seguro


def test_error_de_bd_se_convierte_en_is_error():
    @_tool_seguro
    async def tool_que_falla(args):
        raise psycopg2.OperationalError("connection refused")

    resultado = asyncio.run(tool_que_falla({}))
    assert resultado["is_error"] is True
    assert "base de datos" in resultado["content"][0]["text"]


def test_resultado_normal_pasa_intacto():
    @_tool_seguro
    async def tool_ok(args):
        return {"content": [{"type": "text", "text": "todo bien"}]}

    resultado = asyncio.run(tool_ok({}))
    assert "is_error" not in resultado
    assert resultado["content"][0]["text"] == "todo bien"


def test_errores_no_bd_siguen_propagandose():
    # Solo los errores de psycopg2 se transforman; un bug de programación
    # debe seguir siendo visible como excepción.
    @_tool_seguro
    async def tool_con_bug(args):
        raise KeyError("columna_inexistente")

    with pytest.raises(KeyError):
        asyncio.run(tool_con_bug({}))
