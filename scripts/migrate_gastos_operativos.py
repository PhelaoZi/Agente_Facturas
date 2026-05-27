#!/usr/bin/env python3
"""
migrate_gastos_operativos.py — Zigurat ERP
Crea tabla gastos_operativos para facturas de compra que no son
insumos de producción (peajes, servicios, arrendamientos, etc.).
Idempotente.
"""
import os, sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2. Instala con: pip install psycopg2-binary")
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

SQL = """
CREATE TABLE IF NOT EXISTS gastos_operativos (
    id                   SERIAL PRIMARY KEY,
    folio                TEXT,
    tipo_documento       TEXT,
    fecha_emision        DATE,
    rut_emisor           TEXT,
    razon_social_emisor  TEXT,
    descripcion          TEXT,
    monto_neto           INTEGER,
    monto_total          INTEGER,
    categoria            TEXT DEFAULT 'otros',
    fecha_procesado      TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_gastos_folio_rut
    ON gastos_operativos (folio, rut_emisor);
"""


def main():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            cur = conn.cursor()
            cur.execute(SQL)
        print("OK — tabla gastos_operativos lista (idempotente).")
    except psycopg2.Error as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
