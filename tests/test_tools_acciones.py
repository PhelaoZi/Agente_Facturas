# tests/test_tools_acciones.py
from app.agent import tools_acciones
from app.canvas.artifacts import Collector


def test_accion_gasto_artifact_arma_payload():
    params = {"descripcion": "Luz", "monto": 185000.0, "fecha": "2026-06-30",
              "proveedor": None, "categoria": "servicios"}
    art = tools_acciones.accion_gasto_artifact(params)
    assert art.tipo == "accion"
    assert art.titulo == "Confirmar gasto"
    assert art.payload["tipo_accion"] == "registrar_gasto"
    assert art.payload["params"] == params
    assert "185.000" in art.payload["resumen"]
    assert "Luz" in art.payload["resumen"]
    assert "30/06/2026" in art.payload["resumen"]
    assert "None" not in art.payload["resumen"]


def test_build_acciones_server_lista_un_tool():
    server, tool_names = tools_acciones.build_acciones_server(Collector())
    assert tool_names == ["mcp__acciones__proponer_gasto"]
