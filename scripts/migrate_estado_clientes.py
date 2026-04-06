#!/usr/bin/env python3
"""
migrate_estado_clientes.py — Zigurat ERP
Agrega columna 'estado' a la tabla clientes (activo / incobrable).
Idempotente: se puede ejecutar múltiples veces sin error.
"""
import os
import sys
from pathlib import Path


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

try:
    import psycopg2
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


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    print("Conectado a PostgreSQL:", DB_CONFIG["dbname"])

    with conn:
        with conn.cursor() as cur:
            # Agregar columna estado si no existe
            cur.execute("""
                ALTER TABLE clientes
                ADD COLUMN IF NOT EXISTS estado VARCHAR(20) NOT NULL DEFAULT 'activo'
            """)
            print("OK: columna 'estado' en clientes (activo por defecto)")

            # Verificar estado actual
            cur.execute("SELECT estado, COUNT(*) FROM clientes GROUP BY estado ORDER BY estado")
            print("\nDistribucion actual:")
            for estado, count in cur.fetchall():
                print(f"  {estado}: {count} clientes")

    conn.close()
    print("\nMigracion completada.")


if __name__ == "__main__":
    main()
