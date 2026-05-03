#!/usr/bin/env python3
"""
costo_sku.py - Zigurat ERP
Consulta vista_costo_sku y muestra una tabla legible de costos unitarios.

Uso:
    python costo_sku.py
    python costo_sku.py --sku CREAM-330-C12
    python costo_sku.py --receta "Cream Ale"
"""

import argparse
import os
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2.")
    sys.exit(1)


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


def fmt_clp(v):
    if v is None:
        return "    --"
    return "$" + "{:,.0f}".format(v).replace(",", ".")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sku", help="Filtrar por código de SKU")
    ap.add_argument("--receta", help="Filtrar por nombre de cerveza")
    args = ap.parse_args()

    where = []
    params = []
    if args.sku:
        where.append("codigo = %s")
        params.append(args.sku)
    if args.receta:
        where.append("nombre_cerveza ILIKE %s")
        params.append(f"%{args.receta}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        SELECT codigo, nombre_cerveza, formato,
               costo_liquido_unitario, costo_envasado_unitario, costo_total_unitario
        FROM vista_costo_sku
        {where_sql}
        ORDER BY nombre_cerveza, formato, codigo
    """

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    with conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()

        # SKUs sospechosos: botella sin envasado
        cur.execute("""
            SELECT s.codigo
            FROM sku s
            JOIN formatos f ON f.id = s.formato_id
            LEFT JOIN sku_envasado se ON se.sku_id = s.id
            WHERE s.activo AND f.capacidad_ml < 1000 AND se.id IS NULL
        """)
        botellas_sin_envase = {r[0] for r in cur.fetchall()}
    conn.close()

    if not rows:
        print("Sin SKUs cargados (o sin coincidencias).")
        return

    print(f"{'SKU':<18} {'CERVEZA':<25} {'FORMATO':<20} {'LIQUIDO':>10} {'ENVASE':>10} {'TOTAL':>10}")
    print("-" * 100)
    for r in rows:
        codigo, cerveza, formato, liquido, envase, total = r
        flag = ""
        if total is None or total < 0:
            flag = " [!]"
        elif codigo in botellas_sin_envase:
            flag = " [sin envasado]"
        print(f"{codigo:<18} {cerveza:<25} {formato:<20} {fmt_clp(liquido):>10} {fmt_clp(envase):>10} {fmt_clp(total):>10}{flag}")

    print()
    print(f"Total: {len(rows)} SKU(s)")


if __name__ == "__main__":
    main()
