#!/usr/bin/env python3
"""
reporte.py — Zigurat ERP
Genera el reporte semanal de ventas: totales, top clientes, top productos
y comparativo con la semana anterior.

Uso:
    python .claude/skills/reporte-semanal/scripts/reporte.py
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path


# ─── Cargar .env ──────────────────────────────────────────────────────────────
def _load_env():
    env_file = Path(__file__).parent.parent.parent.parent.parent / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: Falta psycopg2. Instala con: pip install psycopg2-binary")
    sys.exit(1)

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}


# ─── Cálculo de semanas ───────────────────────────────────────────────────────
def semana_actual():
    hoy = date.today()
    lunes = hoy - timedelta(days=hoy.weekday())
    domingo = lunes + timedelta(days=6)
    return lunes, domingo


def semana_anterior():
    lunes, _ = semana_actual()
    lunes_ant = lunes - timedelta(days=7)
    domingo_ant = lunes_ant + timedelta(days=6)
    return lunes_ant, domingo_ant


# ─── Queries ──────────────────────────────────────────────────────────────────
QUERY_TOTAL = """
    SELECT
        COALESCE(SUM(COALESCE(v.monto_total_ajustado, v.monto_total)), 0) AS total,
        COUNT(*)                                                            AS n_facturas
    FROM ventas v
    WHERE v.tipo_documento != '61'
      AND v.fecha BETWEEN %s AND %s
"""

QUERY_TOP_CLIENTES = """
    SELECT
        c.razon_social,
        SUM(COALESCE(v.monto_total_ajustado, v.monto_total)) AS total
    FROM ventas v
    JOIN clientes c ON c.rut_cliente = v.rut_cliente
    WHERE v.tipo_documento != '61'
      AND v.fecha BETWEEN %s AND %s
    GROUP BY c.razon_social
    ORDER BY total DESC
    LIMIT 5
"""

QUERY_TOP_PRODUCTOS = """
    SELECT
        p.nombre_producto,
        SUM(p.cantidad)    AS unidades,
        SUM(p.total_linea) AS total
    FROM productos p
    JOIN ventas v ON v.folio = p.folio AND v.tipo_documento = p.tipo_documento
    WHERE v.tipo_documento != '61'
      AND v.fecha BETWEEN %s AND %s
    GROUP BY p.nombre_producto
    ORDER BY total DESC
    LIMIT 5
"""


# ─── Helpers de formato ───────────────────────────────────────────────────────
def fmt_clp(valor):
    return f"${int(valor):,}".replace(",", ".")


def variacion(actual, anterior):
    if anterior == 0:
        return "N/A (sin datos semana anterior)"
    pct = ((actual - anterior) / anterior) * 100
    signo = "+" if pct >= 0 else ""
    icono = "▲" if pct >= 0 else "▼"
    return f"{icono} {signo}{pct:.1f}%"


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    lunes_act, domingo_act   = semana_actual()
    lunes_ant, domingo_ant   = semana_anterior()

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur  = conn.cursor(cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    # ── Totales ──────────────────────────────────────────────────────────────
    cur.execute(QUERY_TOTAL, (lunes_act, domingo_act))
    row_act = cur.fetchone()
    total_act  = row_act["total"]
    fact_act   = row_act["n_facturas"]

    cur.execute(QUERY_TOTAL, (lunes_ant, domingo_ant))
    row_ant    = cur.fetchone()
    total_ant  = row_ant["total"]
    fact_ant   = row_ant["n_facturas"]

    # ── Top clientes ─────────────────────────────────────────────────────────
    cur.execute(QUERY_TOP_CLIENTES, (lunes_act, domingo_act))
    top_clientes = cur.fetchall()

    # ── Top productos ─────────────────────────────────────────────────────────
    cur.execute(QUERY_TOP_PRODUCTOS, (lunes_act, domingo_act))
    top_productos = cur.fetchall()

    conn.close()

    # ── Output ────────────────────────────────────────────────────────────────
    sep = "=" * 60
    print(sep)
    print("  ZIGURAT BREWERY — Reporte Semanal de Ventas")
    print(sep)
    print(f"  Semana actual:   {lunes_act.strftime('%d/%m')} – {domingo_act.strftime('%d/%m/%Y')}")
    print(f"  Semana anterior: {lunes_ant.strftime('%d/%m')} – {domingo_ant.strftime('%d/%m/%Y')}")
    print()

    print("─" * 60)
    print("  RESUMEN")
    print("─" * 60)
    print(f"  Total vendido esta semana:   {fmt_clp(total_act)}")
    print(f"  Facturas emitidas:           {fact_act}")
    print(f"  Semana anterior:             {fmt_clp(total_ant)}  ({fact_ant} facturas)")
    print(f"  Variación:                   {variacion(total_act, total_ant)}")
    print()

    if top_clientes:
        print("─" * 60)
        print("  TOP 5 CLIENTES")
        print("─" * 60)
        for i, row in enumerate(top_clientes, 1):
            nombre = (row["razon_social"] or "")[:35]
            print(f"  {i}. {nombre:<35}  {fmt_clp(row['total'])}")
        print()

    if top_productos:
        print("─" * 60)
        print("  TOP 5 PRODUCTOS")
        print("─" * 60)
        for i, row in enumerate(top_productos, 1):
            nombre = (row["nombre_producto"] or "")[:32]
            print(f"  {i}. {nombre:<32}  {row['unidades']:.0f} u.  {fmt_clp(row['total'])}")
        print()

    print(sep)


if __name__ == "__main__":
    main()
