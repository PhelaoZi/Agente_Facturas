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


# ── Publicar por referencia ───────────────────────────────────────────────────
# Las filas de un SELECT no pasan por el modelo: se guardan en ResultadosSQL y
# el modelo publica la que quiera mostrar con su ref. Asi no las transporta (no
# gasta contexto ni las puede transcribir mal) pero SIGUE decidiendo que se ve.

import asyncio

from app.agent.orchestrator import ResultadosSQL


def _llamar_lienzo(registro, nombre, args):
    return asyncio.run(registro.ejecutar(f"mcp__lienzo__{nombre}", args))


def test_publicar_consulta_saca_las_filas_del_almacen():
    col = Collector()
    resultados = ResultadosSQL()
    ref = resultados.guardar(["folio", "cliente"],
                             [[str(4700 + i), f"CLIENTE {i}"] for i in range(55)])

    cfg, names = publish_tools.build_lienzo_server(col, resultados)
    assert "mcp__lienzo__publicar_consulta" in names

    _llamar_lienzo(cfg, "publicar_consulta", {"ref": ref, "titulo": "Por cobrar"})

    assert len(col.items) == 1
    art = col.items[0]
    assert art.tipo == "tabla" and art.titulo == "Por cobrar"
    assert len(art.payload["filas"]) == 55
    assert art.payload["columnas"] == ["folio", "cliente"]


def test_publicar_consulta_con_ref_desconocida_no_publica_nada():
    """Nunca inventar una tabla vacia: el modelo tiene que poder corregir."""
    col = Collector()
    cfg, _ = publish_tools.build_lienzo_server(col, ResultadosSQL())

    res = _llamar_lienzo(cfg, "publicar_consulta", {"ref": "q9", "titulo": "X"})

    assert col.items == []
    assert "q9" in res and "No existe" in res


def test_sin_almacen_la_tool_de_referencia_ni_se_ofrece():
    """El dashboard construye el lienzo sin resultados en otros contextos: no
    tiene sentido ofrecerle al modelo una tool que no puede funcionar."""
    _cfg, names = publish_tools.build_lienzo_server(Collector())
    assert "mcp__lienzo__publicar_consulta" not in names
