#!/usr/bin/env python3
"""
migrate_chat_telemetria.py — Zigurat ERP
Crea la tabla chat_telemetria: una fila por llamada al modelo del chat de
negocio (tokens, caché, latencia, costo). Idempotente.

Existe porque hasta el 2026-08-09 el orquestador recibía el bloque `usage` de
cada llamada y lo descartaba: no se podía contestar cuánto cuesta el chat ni si
el caché de prefijo está pegando.
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
CREATE TABLE IF NOT EXISTS chat_telemetria (
    id                 SERIAL PRIMARY KEY,
    creado             TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Agrupa las vueltas de UNA pregunta: el costo de una tarea es la suma de
    -- sus filas. No hay tabla de turnos aparte; se agrega con SQL y no hay dos
    -- sitios que puedan quedar inconsistentes.
    turno_id           TEXT NOT NULL,
    session_id         TEXT,
    iteracion          INTEGER NOT NULL,
    modelo             TEXT NOT NULL,
    pregunta           TEXT,
    -- OpenRouter elige proveedor por llamada; un salto entre vueltas rompe el
    -- caché de prefijo. Sin este dato ese diagnóstico es imposible.
    proveedor          TEXT,
    prompt_tokens      INTEGER,
    cached_tokens      INTEGER,
    completion_tokens  INTEGER,
    reasoning_tokens   INTEGER,
    latencia_ms        INTEGER,
    finish_reason      TEXT,
    tools_llamadas     TEXT[],
    costo_usd          NUMERIC(12, 6),
    -- El `usage` tal cual lo mandó el proveedor: si mañana agrega un campo
    -- útil ya queda registrado, sin reprocesar histórico.
    usage_crudo        JSONB
);

CREATE INDEX IF NOT EXISTS idx_chat_telemetria_creado
    ON chat_telemetria (creado DESC);
CREATE INDEX IF NOT EXISTS idx_chat_telemetria_turno
    ON chat_telemetria (turno_id);
"""


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SQL)
            cur.execute("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_name = 'chat_telemetria'
            """)
            print(f"OK: chat_telemetria lista ({cur.fetchone()[0]} columnas).")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
