#!/usr/bin/env python3
"""
migrate_seguimiento_comercial.py — Zigurat ERP
Crea la tabla seguimiento_comercial: mini-CRM de la lista de seguimiento
comercial que alimenta el "gerente comercial" del chat. Idempotente.
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
CREATE TABLE IF NOT EXISTS seguimiento_comercial (
    id              SERIAL PRIMARY KEY,
    rut_cliente     TEXT NOT NULL,
    motivo          TEXT NOT NULL,
    prioridad       TEXT NOT NULL DEFAULT 'media',
    estado          TEXT NOT NULL DEFAULT 'pendiente',
    senales         TEXT,
    fecha_creacion  DATE NOT NULL DEFAULT CURRENT_DATE,
    fecha_objetivo  DATE,
    fecha_contacto  DATE,
    notas           TEXT
);

CREATE INDEX IF NOT EXISTS ix_seguimiento_estado
    ON seguimiento_comercial (estado);
"""


def main():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(SQL)
        print("OK — tabla seguimiento_comercial lista (idempotente).")
    except psycopg2.Error as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
