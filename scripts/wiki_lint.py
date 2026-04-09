#!/usr/bin/env python3
"""
wiki_lint.py — Zigurat ERP
Audita la consistencia entre la wiki y la base de datos.

Uso:
    python scripts/wiki_lint.py
"""

import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2. Instala con: pip install psycopg2-binary")
    sys.exit(1)


# ─── Carga de variables de entorno desde .env ─────────────────────────────────
def _load_env():
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

WIKI_DIR = Path(__file__).parent.parent / "wiki"
CLIENTES_DIR = WIKI_DIR / "clientes"


def main():
    print("=" * 60)
    print("ZIGURAT ERP — Wiki Lint")
    print("=" * 60)
    print()

    if not CLIENTES_DIR.exists():
        print("ERROR: No existe wiki/clientes/. Ejecuta /wiki-init primero.")
        sys.exit(1)

    # Conectar a BD
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.set_client_encoding('UTF8')
        cur = conn.cursor()
    except psycopg2.Error as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    problemas = []

    # Importar slugify de wiki_update
    sys.path.insert(0, str(Path(__file__).parent))
    from wiki_update import slugify

    # 1. Clientes en BD sin ficha
    cur.execute("SELECT rut_cliente, razon_social FROM clientes ORDER BY razon_social")
    clientes_bd = cur.fetchall()
    fichas_existentes = {f.stem for f in CLIENTES_DIR.glob("*.md")}

    for rut, razon in clientes_bd:
        slug = slugify(razon)
        if slug not in fichas_existentes:
            problemas.append(f"  [SIN FICHA] {razon} ({rut}) — no tiene ficha en wiki/clientes/")

    # 2. Fichas sin cliente en BD (huérfanas)
    slugs_bd = {slugify(r[1]) for r in clientes_bd}
    for ficha in CLIENTES_DIR.glob("*.md"):
        if ficha.stem not in slugs_bd:
            problemas.append(f"  [HUÉRFANA] {ficha.name} — no tiene cliente en BD")

    # 3. Fichas desactualizadas (>7 días con movimientos recientes)
    hoy = date.today()
    for ficha in CLIENTES_DIR.glob("*.md"):
        contenido = ficha.read_text(encoding="utf-8")
        match_fecha = re.search(r"ultima_actualizacion:\s*(\d{4}-\d{2}-\d{2})", contenido)
        if match_fecha:
            ultima = datetime.strptime(match_fecha.group(1), "%Y-%m-%d").date()
            dias = (hoy - ultima).days
            if dias > 7:
                rut_match = re.search(r'rut:\s*"([^"]+)"', contenido)
                if rut_match:
                    rut = rut_match.group(1)
                    cur.execute(
                        "SELECT COUNT(*) FROM ventas WHERE rut_cliente = %s AND fecha > %s",
                        (rut, match_fecha.group(1))
                    )
                    count = cur.fetchone()[0]
                    if count > 0:
                        problemas.append(
                            f"  [DESACTUALIZADA] {ficha.name} — última actualización: "
                            f"{match_fecha.group(1)} ({dias} días) con {count} movimiento(s) nuevo(s)"
                        )

    conn.close()

    # Reporte
    if problemas:
        print(f"  Se encontraron {len(problemas)} problema(s):\n")
        for p in problemas:
            print(p)
        print()
        print("  Sugerencia: ejecutar 'python scripts/wiki_update.py --todos' para corregir.")
    else:
        print("  ✅ Wiki consistente — no se encontraron problemas.")

    print()
    print("=" * 60)


if __name__ == "__main__":
    main()
