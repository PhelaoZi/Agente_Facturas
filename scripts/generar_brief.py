#!/usr/bin/env python3
"""Genera el brief diario de Zigurat (solo lectura) y lo guarda en briefs/.

Uso:
    python scripts/generar_brief.py
"""
import sys
from datetime import date
from pathlib import Path

from _console import force_utf8

force_utf8()

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: falta psycopg2. Instala con: pip install psycopg2-binary")
    sys.exit(1)

# Permite importar app.* al ejecutar como script suelto.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_URL, PROJECT_ROOT  # noqa: E402
from app.briefing import data, render          # noqa: E402


def _recolectar(cur):
    """Junta todas las secciones del brief en un dict."""
    return {
        "umbral_vencidas": 30,
        "umbral_reciente": 7,
        "umbral_inactivos": 60,
        "cobranza": data.resumen_cobranza(cur),
        "top_deudores": data.top_deudores(cur, limite=5),
        "vencidas": data.facturas_vencidas(cur, dias=30),
        "cobrado_reciente": data.cobrado_reciente(cur, dias=7),
        "ventas_periodo": data.ventas_periodo(cur, dias=7),
        "inactivos": data.clientes_inactivos(cur, dias=60),
    }


def main():
    try:
        conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    except psycopg2.OperationalError as e:
        print(f"ERROR: no se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            brief = _recolectar(cur)
    finally:
        conn.close()

    md = render.render_markdown(brief)

    destino_dir = PROJECT_ROOT / "briefs"
    destino_dir.mkdir(exist_ok=True)
    destino = destino_dir / f"{date.today().isoformat()}.md"
    destino.write_text(md, encoding="utf-8")

    print(md)
    print(f"\nBrief guardado en: {destino}")


if __name__ == "__main__":
    main()
