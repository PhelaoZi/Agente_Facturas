"""Ejecuta el Claude Agent SDK como orquestador sobre Postgres + lienzo."""
import asyncio
import shutil
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from claude_agent_sdk import ClaudeAgentOptions, query

from app.agent import memoria
from app.agent.publish_tools import build_lienzo_server
from app.agent.system_prompt import SYSTEM_PROMPT
from app.agent.tools_negocio import build_negocio_server
from app.agent.tools_acciones import build_acciones_server
from app.canvas.artifacts import Collector
from app.config import DB_URL, PROJECT_ROOT

MAX_TURNS = 20

# Tools built-in del CLI vetadas. Con permission_mode="bypassPermissions" todo
# lo no vetado queda auto-aprobado: sin esta lista el agente podría ejecutar
# Bash (y escribir en la BD vía psql), editar archivos o leer el .env, y el
# invariante "el agente nunca escribe" dependería solo del system prompt.
# El agente del chat solo necesita sus tools MCP (negocio/lienzo/acciones/
# memoria/postgres).
DISALLOWED_TOOLS = [
    "Bash", "Write", "Edit", "MultiEdit", "NotebookEdit",
    "Read", "Glob", "Grep", "WebFetch", "WebSearch", "Task",
]


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


def _extract_session_id(messages) -> str | None:
    """Obtiene el session_id que asignó el CLI (para reanudar la conversación)."""
    for m in messages:
        sid = getattr(m, "session_id", None)
        if isinstance(sid, str) and sid:
            return sid
        data = getattr(m, "data", None)
        if isinstance(data, dict) and data.get("session_id"):
            return data["session_id"]
    return None


def _build_options(collector: Collector, session_id: str | None = None) -> ClaudeAgentOptions:
    lienzo_server, lienzo_tools = build_lienzo_server(collector)
    negocio_server, negocio_tools = build_negocio_server()
    acciones_server, acciones_tools = build_acciones_server(collector)
    memoria_server, memoria_tools = memoria.build_memoria_server()
    claude_path = shutil.which("claude")
    # Memoria de largo plazo: el índice compacto viaja en cada consulta; el
    # detalle de cada nota se lee bajo demanda con mcp__memoria__leer_nota.
    indice = memoria.leer_indice()
    system_prompt = SYSTEM_PROMPT
    if indice:
        system_prompt += "\n\nMEMORIA DEL NEGOCIO (aprendida en sesiones anteriores):\n" + indice
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=str(PROJECT_ROOT),
        # Modelo fijo: sin esto el chat usa el modelo por defecto del CLI (puede
        # ser el más caro). Sonnet sobra para consultas de negocio con tools.
        model="sonnet",
        # Aislamiento del agente: no cargar CLAUDE.md ni settings del filesystem
        # (el SYSTEM_PROMPT ya contiene las reglas canónicas) y usar SOLO los
        # servidores MCP definidos aquí (ignora .mcp.json del repo).
        setting_sources=[],
        strict_mcp_config=True,
        # Memoria de conversación: si hay una sesión previa, se reanuda para que
        # el agente recuerde las preguntas anteriores ("¿y el mes pasado?").
        resume=session_id,
        mcp_servers={
            "lienzo": lienzo_server,
            "negocio": negocio_server,
            "acciones": acciones_server,
            "memoria": memoria_server,
            "postgres": _postgres_server(),
        },
        allowed_tools=lienzo_tools + negocio_tools + acciones_tools + memoria_tools
                      + ["mcp__postgres__query"],
        disallowed_tools=DISALLOWED_TOOLS,
        permission_mode="bypassPermissions",
        max_turns=MAX_TURNS,
        cli_path=claude_path,
    )

async def _run(pregunta: str, collector: Collector,
               session_id: str | None = None) -> tuple[str, str | None]:
    options = _build_options(collector, session_id)
    mensajes = []
    async for message in query(prompt=pregunta, options=options):
        mensajes.append(message)
    return _extract_text(mensajes), _extract_session_id(mensajes) or session_id


def run(pregunta: str, collector: Collector,
        session_id: str | None = None) -> tuple[str, str | None]:
    """Punto de entrada síncrono. Llena `collector` con artefactos.

    Devuelve (texto, session_id). Pasar el session_id devuelto en la siguiente
    llamada reanuda la conversación (memoria de chat); None inicia una nueva.
    """
    return asyncio.run(_run(pregunta, collector, session_id))
