#!/usr/bin/env python3
"""
migrate_flujo_caja.py - Zigurat ERP
Migración para el módulo de flujo de caja y conciliación bancaria.
Idempotente: se puede ejecutar múltiples veces sin efectos secundarios.

Uso:
    python scripts/migrate_flujo_caja.py
"""

import os
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta la librería psycopg2.")
    print("Instala con: pip install psycopg2-binary")
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

MIGRATIONS = [
    # 1. Columna codigo_transferencia en movimientos_banco para deduplicar
    """
    ALTER TABLE movimientos_banco
        ADD COLUMN IF NOT EXISTS codigo_transferencia VARCHAR(30)
    """,
    # 2. Índice único parcial: solo aplica a filas donde codigo no es NULL
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_movimientos_codigo_transferencia
        ON movimientos_banco (codigo_transferencia)
        WHERE codigo_transferencia IS NOT NULL
    """,
    # 3. Nueva tabla cuentas_por_pagar
    """
    CREATE TABLE IF NOT EXISTS cuentas_por_pagar (
        id                SERIAL PRIMARY KEY,
        descripcion       VARCHAR(255) NOT NULL,
        proveedor         VARCHAR(255),
        monto             NUMERIC NOT NULL,
        fecha_vencimiento DATE NOT NULL,
        recurrente        BOOLEAN DEFAULT FALSE,
        periodicidad      VARCHAR(20),
        pagado            BOOLEAN DEFAULT FALSE,
        fecha_pago        DATE,
        categoria         VARCHAR(50),
        created_at        TIMESTAMPTZ DEFAULT NOW()
    )
    """,
]


def main():
    print("=" * 60)
    print("ZIGURAT ERP — Migración Flujo de Caja")
    print("=" * 60)
    print()

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    print(f"✓ Conectado a: {DB_CONFIG['dbname']}")
    print()

    try:
        with conn:
            cur = conn.cursor()
            for i, sql in enumerate(MIGRATIONS, 1):
                cur.execute(sql)
                print(f"  ✓ Migración {i}/{len(MIGRATIONS)} aplicada")

        print()
        print("✅ Migración completada exitosamente")
        print()
        print("Tablas/columnas creadas o ya existían:")
        print("  - movimientos_banco.codigo_transferencia (VARCHAR 30)")
        print("  - movimientos_banco: índice único en codigo_transferencia")
        print("  - cuentas_por_pagar (nueva tabla)")
    except psycopg2.Error as e:
        print(f"\nERROR en migración: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
