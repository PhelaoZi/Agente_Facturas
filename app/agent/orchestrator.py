"""Ejecuta el Claude Agent SDK como orquestador sobre Postgres + lienzo."""
import asyncio
import shutil
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from claude_agent_sdk import ClaudeAgentOptions, query

from app.agent.publish_tools import build_lienzo_server
from app.agent.system_prompt import SYSTEM_PROMPT
from app.agent.tools_negocio import build_negocio_server
from app.agent.tools_acciones import build_acciones_server
from app.canvas.artifacts import Collector
from app.config import DB_URL, PROJECT_ROOT

MAX_TURNS = 20


def _postgres_server() -> dict:
    """Servidor MCP de Postgres (solo lectura) igual al de .mcp.json."""
    return {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres", DB_URL],
    }


def _extract_text(messages) -> str:
    """Extrae el texto del último mensaje con contenido del agente (evita mostrar razonamiento intermedio)."""
    for m in reversed(messages):
        content = getattr(m, "content", None)
        if isinstance(content, list):
            partes = []
            for b in content:
                t = getattr(b, "text", None)
                if isinstance(t, str):
                    partes.append(t)
            if partes:
                return "\n".join(partes).strip()
        elif isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _build_options(collector: Collector) -> ClaudeAgentOptions:
    lienzo_server, lienzo_tools = build_lienzo_server(collector)
    negocio_server, negocio_tools = build_negocio_server()
    acciones_server, acciones_tools = build_acciones_server(collector)
    claude_path = shutil.which("claude")
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        cwd=str(PROJECT_ROOT),
        # Modelo fijo: sin esto el chat usa el modelo por defecto del CLI (puede
        # ser el más caro). Sonnet sobra para consultas de negocio con tools.
        model="sonnet",
        # Aislamiento del agente: no cargar CLAUDE.md ni settings del filesystem
        # (el SYSTEM_PROMPT ya contiene las reglas canónicas) y usar SOLO los
        # servidores MCP definidos aquí (ignora .mcp.json del repo).
        setting_sources=[],
        strict_mcp_config=True,
        mcp_servers={
            "lienzo": lienzo_server,
            "negocio": negocio_server,
            "acciones": acciones_server,
            "postgres": _postgres_server(),
        },
        allowed_tools=lienzo_tools + negocio_tools + acciones_tools + ["mcp__postgres__query"],
        permission_mode="bypassPermissions",
        max_turns=MAX_TURNS,
        cli_path=claude_path,
    )

async def _run(pregunta: str, collector: Collector) -> str:
    options = _build_options(collector)
    mensajes = []
    async for message in query(prompt=pregunta, options=options):
        mensajes.append(message)
    return _extract_text(mensajes)


def run(pregunta: str, collector: Collector) -> str:
    """Punto de entrada síncrono para Streamlit. Llena `collector` con artefactos."""
    return asyncio.run(_run(pregunta, collector))
