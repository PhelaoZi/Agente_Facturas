"""Memoria persistente del agente del dashboard (patrón archivo + índice).

El agente aprende ENTRE sesiones guardando notas en memoria-agente/:
- MEMORIA.md: índice compacto (una línea por nota). Se inyecta al system prompt
  en cada consulta, con tope de caracteres para no inflar los tokens.
- notas/<slug>.md: el detalle de cada aprendizaje; el agente lo lee bajo
  demanda con la tool leer_nota (divulgación progresiva).

Mismo patrón que la wiki de clientes (Karpathy) y que el memory tool oficial
de Anthropic: memoria = archivos Markdown, no RAG ni BD vectorial.
"""
import re
import unicodedata
from datetime import date
from pathlib import Path

from app.config import PROJECT_ROOT

MEMORIA_DIR = PROJECT_ROOT / "memoria-agente"
NOTAS_DIR = MEMORIA_DIR / "notas"
INDICE = MEMORIA_DIR / "MEMORIA.md"

# Topes para que la memoria nunca desborde el contexto de una consulta.
MAX_INDICE_CHARS = 6000
MAX_NOTA_CHARS = 4000

TIPOS_VALIDOS = {"negocio", "correccion", "dato-bd", "preferencia"}


def _slug(titulo: str) -> str:
    """'Barril PET sin logística' -> 'barril-pet-sin-logistica'."""
    s = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:60] or "nota"


def leer_indice() -> str:
    """Contenido del índice (truncado al tope) o '' si aún no hay memoria."""
    if not INDICE.exists():
        return ""
    texto = INDICE.read_text(encoding="utf-8").strip()
    if len(texto) > MAX_INDICE_CHARS:
        texto = texto[:MAX_INDICE_CHARS] + "\n[... índice truncado: pide depurar la memoria]"
    return texto


def guardar_nota(titulo: str, contenido: str, tipo: str = "negocio") -> str:
    """Crea o actualiza una nota y su línea en el índice. Devuelve mensaje de estado."""
    titulo = (titulo or "").strip()
    contenido = (contenido or "").strip()
    if not titulo or not contenido:
        raise ValueError("La nota necesita título y contenido.")
    if tipo not in TIPOS_VALIDOS:
        tipo = "negocio"
    contenido = contenido[:MAX_NOTA_CHARS]

    NOTAS_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(titulo)
    ruta = NOTAS_DIR / f"{slug}.md"
    hoy = date.today().isoformat()
    if ruta.exists():
        # Actualización: se conserva lo anterior y se anexa lo nuevo con fecha.
        previo = ruta.read_text(encoding="utf-8").rstrip()
        ruta.write_text(f"{previo}\n\n**Actualización {hoy}:** {contenido}\n",
                        encoding="utf-8")
        accion = "actualizada"
    else:
        ruta.write_text(f"# {titulo}\n\n- tipo: {tipo}\n- fecha: {hoy}\n\n{contenido}\n",
                        encoding="utf-8")
        accion = "guardada"

    _actualizar_indice(slug, titulo, tipo, contenido)
    return f"Nota '{titulo}' {accion} en la memoria ({slug})."


def _actualizar_indice(slug: str, titulo: str, tipo: str, contenido: str) -> None:
    """Mantiene UNA línea por nota en MEMORIA.md (gancho corto para el contexto)."""
    gancho = re.sub(r"\s+", " ", contenido)[:120]
    linea = f"- [{tipo}] {titulo} ({slug}): {gancho}"
    lineas = []
    if INDICE.exists():
        lineas = [l for l in INDICE.read_text(encoding="utf-8").splitlines()
                  if l.strip() and f"({slug})" not in l and not l.startswith("# ")]
    lineas.append(linea)
    INDICE.write_text("# Memoria del agente Zigurat\n" + "\n".join(lineas) + "\n",
                      encoding="utf-8")


def leer_nota(nombre: str) -> str | None:
    """Lee el detalle de una nota por slug (acepta el título y lo convierte)."""
    ruta = NOTAS_DIR / f"{_slug(nombre)}.md"
    if not ruta.exists():
        return None
    return ruta.read_text(encoding="utf-8")


def build_memoria_server():
    """Servidor MCP 'memoria'. Devuelve (server, lista_de_tool_names)."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    def _texto(s):
        return {"content": [{"type": "text", "text": s}]}

    @tool("guardar_nota",
          "Guarda un aprendizaje en tu memoria persistente (sobrevive entre "
          "sesiones). Úsala cuando el usuario te corrija, te enseñe una regla "
          "del negocio, o descubras algo no obvio de la BD o de un error tuyo. "
          "tipo: negocio | correccion | dato-bd | preferencia.",
          {"titulo": str, "contenido": str, "tipo": str})
    async def guardar_nota_tool(args):
        try:
            msg = guardar_nota(args.get("titulo", ""), args.get("contenido", ""),
                               args.get("tipo", "negocio"))
            return _texto(msg)
        except (ValueError, OSError) as e:
            return {"content": [{"type": "text", "text": f"No pude guardar la nota: {e}"}],
                    "is_error": True}

    @tool("leer_nota",
          "Lee el detalle completo de una nota de tu memoria (el índice del "
          "system prompt solo trae el resumen). Pásale el slug o el título.",
          {"nombre": str})
    async def leer_nota_tool(args):
        detalle = leer_nota(args.get("nombre", ""))
        if detalle is None:
            return _texto(f"No existe una nota '{args.get('nombre', '')}' en la memoria.")
        return _texto(detalle)

    server = create_sdk_mcp_server(name="memoria", version="1.0.0",
                                   tools=[guardar_nota_tool, leer_nota_tool])
    return server, ["mcp__memoria__guardar_nota", "mcp__memoria__leer_nota"]
