# tests/test_publish_tools.py
from app.agent import publish_tools
from app.canvas.artifacts import Collector


def test_kpi_artifact_builder():
    art = publish_tools.kpi_artifact({"etiqueta": "Ventas", "valor": "$1M", "delta": "+5%"})
    assert art.tipo == "kpi"
    assert art.titulo == "Ventas"
    assert art.payload["valor"] == "$1M"


def test_grafico_artifact_builder():
    art = publish_tools.grafico_artifact(
        {"titulo": "G", "chart_type": "bar", "x": ["a"], "y": [1]}
    )
    assert art.tipo == "grafico"
    assert art.payload["chart_type"] == "bar"


def test_tabla_artifact_builder():
    art = publish_tools.tabla_artifact({"titulo": "T", "columnas": ["c"], "filas": [[1]]})
    assert art.tipo == "tabla"
    assert art.payload["columnas"] == ["c"]


def test_informe_artifact_builder():
    art = publish_tools.informe_artifact({"titulo": "I", "markdown": "x"})
    assert art.tipo == "informe"
    assert art.payload["markdown"] == "x"


def test_build_lienzo_server_lista_cuatro_tools():
    server, tool_names = publish_tools.build_lienzo_server(Collector())
    assert len(tool_names) == 4
    assert "mcp__lienzo__publicar_kpi" in tool_names
    assert "mcp__lienzo__publicar_grafico" in tool_names
    assert "mcp__lienzo__publicar_tabla" in tool_names
    assert "mcp__lienzo__publicar_informe" in tool_names
