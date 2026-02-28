#!/usr/bin/env python3
"""
agregar_gasto.py - Zigurat ERP
Agrega una cuenta por pagar a la base de datos.

Uso:
    python agregar_gasto.py "descripcion" monto YYYY-MM-DD [proveedor] [categoria]

Ejemplo:
    python agregar_gasto.py "Arriendo bodega marzo" 850000 2026-03-05 "Propietario SA" arriendo
"""

import os
import sys
from pathlib import Path
from datetime import datetime

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2.")
    sys.exit(1)


def _load_env():
    # Buscar .env subiendo desde la ubicacion del script hasta encontrarlo
    p = Path(__file__).resolve()
    for _ in range(6):
        candidate = p.parent / ".env"
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return
        p = p.parent

_load_env()

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}


def main():
    if len(sys.argv) < 4:
        print("Uso: python agregar_gasto.py \"descripcion\" monto YYYY-MM-DD [proveedor] [categoria]")
        print("Ejemplo: python agregar_gasto.py \"Arriendo bodega\" 850000 2026-03-05 \"Prop SA\" arriendo")
        sys.exit(1)

    descripcion  = sys.argv[1]
    monto_raw    = sys.argv[2].replace('.', '').replace(',', '.')
    fecha_raw    = sys.argv[3]
    proveedor    = sys.argv[4] if len(sys.argv) > 4 else None
    categoria    = sys.argv[5] if len(sys.argv) > 5 else None

    try:
        monto = float(monto_raw)
    except ValueError:
        print(f"ERROR: Monto invalido: {sys.argv[2]}")
        sys.exit(1)

    try:
        fecha = datetime.strptime(fecha_raw, '%Y-%m-%d').date()
    except ValueError:
        print(f"ERROR: Fecha invalida: {fecha_raw}. Formato esperado: YYYY-MM-DD")
        sys.exit(1)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    with conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO cuentas_por_pagar
                (descripcion, proveedor, monto, fecha_vencimiento, categoria)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (descripcion, proveedor, monto, fecha, categoria))
        new_id = cur.fetchone()[0]

    conn.close()

    monto_fmt = "$" + "{:,.0f}".format(monto).replace(",", ".")
    print(f"Gasto registrado (id={new_id})")
    print(f"   Descripcion:  {descripcion}")
    print(f"   Monto:        {monto_fmt}")
    print(f"   Vencimiento:  {fecha.strftime('%d/%m/%Y')}")
    if proveedor:
        print(f"   Proveedor:    {proveedor}")
    if categoria:
        print(f"   Categoria:    {categoria}")


if __name__ == "__main__":
    main()
