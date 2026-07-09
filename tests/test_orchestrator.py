# tests/test_orchestrator.py
import types
from app.agent import orchestrator
from app.canvas.artifacts import Collector


def test_extract_text_extrae_ultimo_mensaje_con_texto():
    msgs = [
        types.SimpleNamespace(content=[
            types.SimpleNamespace(text="Ignorar este primer mensaje"),
        ]),
        types.SimpleNamespace(content=[
            types.SimpleNamespace(text="Hola"),
            types.SimpleNamespace(text="mundo"),
        ]),
        types.SimpleNamespace(content=""),  # Vacío, se ignora
        types.SimpleNamespace(otra_cosa=1),  # Sin content, se ignora
    ]
    assert orchestrator._extract_text(msgs) == "Hola\nmundo"


def test_postgres_server_usa_npx_y_server_postgres():
    s = orchestrator._postgres_server()
    assert s["command"] == "npx"
    assert any("server-postgres" in a for a in s["args"])


def test_build_options_incluye_tools_permitidos():
    options = orchestrator._build_options(Collector())
    assert "mcp__postgres__query" in options.allowed_tools
    assert "mcp__lienzo__publicar_kpi" in options.allowed_tools


def test_run_agrega_texto_de_la_respuesta(monkeypatch):
    async def fake_query(prompt, options):
        yield types.SimpleNamespace(
            content=[types.SimpleNamespace(text="Respuesta del agente")],
            session_id="ses-1",
        )

    monkeypatch.setattr(orchestrator, "query", fake_query)
    texto, session_id = orchestrator.run("¿ventas?", Collector())
    assert texto == "Respuesta del agente"
    assert session_id == "ses-1"


def test_build_options_incluye_tools_de_negocio():
    options = orchestrator._build_options(Collector())
    assert "mcp__negocio__deuda_total" in options.allowed_tools
    assert "mcp__negocio__flujo_caja" in options.allowed_tools
    # No se rompe lo anterior:
    assert "mcp__postgres__query" in options.allowed_tools
    assert "mcp__lienzo__publicar_kpi" in options.allowed_tools


def test_build_options_incluye_tool_de_accion():
    options = orchestrator._build_options(Collector())
    assert "mcp__acciones__proponer_gasto" in options.allowed_tools
    assert "acciones" in options.mcp_servers
    # No rompe lo anterior:
    assert "mcp__negocio__deuda_total" in options.allowed_tools
    assert "mcp__lienzo__publicar_kpi" in options.allowed_tools
    assert "mcp__postgres__query" in options.allowed_tools


def test_build_options_no_cambia_permission_mode():
    options = orchestrator._build_options(Collector())
    assert options.permission_mode == "bypassPermissions"


def test_build_options_bloquea_tools_builtin():
    # Con bypassPermissions las tools built-in del CLI (Bash, Write, ...) quedan
    # auto-aprobadas: hay que vetarlas explícitamente para que el invariante
    # "el agente nunca escribe" sea capacidad real y no solo prompt.
    options = orchestrator._build_options(Collector())
    for tool in ["Bash", "Write", "Edit", "NotebookEdit",
                 "Read", "Glob", "Grep", "WebFetch", "WebSearch", "Task"]:
        assert tool in options.disallowed_tools, f"falta vetar {tool}"
