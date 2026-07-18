#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_nube.py - Zigurat ERP
Replica de SOLO LECTURA del Postgres local hacia el proyecto InsForge
(zigurat-movil). La BD local sigue siendo la fuente de verdad.

Flujo: leer 5 tablas locales -> TRUNCATE + INSERT masivo en la nube dentro
de UNA transaccion -> registrar metadatos (ultimo_sync, saldo_banco) en
sync_meta. Con --init ademas aplica scripts/migrate_nube_views.sql y crea
las tablas (esquema copiado de la BD local con pg_dump --schema-only).

NO FATAL: siempre termina con exit code (0 ok, 1 error) y loggea; quien lo
invoque desde el pipeline debe tratar el 1 como warning, nunca abortar.

Uso:
    python scripts/sync_nube.py           # replica los datos
    python scripts/sync_nube.py --init    # primera vez: esquema + views + datos
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / "logs" / "sync_nube.log"
SQL_VIEWS = PROJECT_ROOT / "scripts" / "migrate_nube_views.sql"

# Orden de carga: padres antes que hijos (FKs). El TRUNCATE va en una sola
# sentencia con todas, asi Postgres resuelve las dependencias entre ellas.
TABLAS_ORDEN = ["clientes", "ventas", "productos", "conciliaciones",
                "cuentas_por_pagar"]
LOTE = 1000
TIMEOUT_PG_DUMP = 120


def _load_env():
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def log(mensaje):
    """Imprime y anexa a logs/sync_nube.log con timestamp."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    linea = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {mensaje}"
    print(linea)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def conectar_local():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ.get("DB_NAME", "dte_facturas_chile"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ["DB_PASSWORD"],
    )


def conectar_nube():
    url = os.environ.get("INSFORGE_DB_URL")
    if not url:
        raise RuntimeError("Falta INSFORGE_DB_URL en el .env")
    return psycopg2.connect(url)


def sql_insert(tabla, columnas):
    """SQL de insert masivo para execute_values. `tabla` y `columnas` vienen
    de TABLAS_ORDEN y de cursor.description (nunca de input externo)."""
    return f"INSERT INTO {tabla} ({', '.join(columnas)}) VALUES %s"


def leer_tabla(cur_local, tabla):
    cur_local.execute(f"SELECT * FROM {tabla}")
    columnas = [d[0] for d in cur_local.description]
    return columnas, cur_local.fetchall()


def replicar_tabla(cur_nube, tabla, columnas, filas):
    execute_values(cur_nube, sql_insert(tabla, columnas), filas, page_size=LOTE)


def obtener_saldo_banco(cur_local):
    """Ultimo saldo_diario de movimientos_banco (espejo de app/negocio/flujo.py).
    La tabla NO se replica; solo viaja este valor, que el flujo de la nube usa
    como saldo inicial."""
    cur_local.execute("""
        SELECT saldo_diario, fecha FROM movimientos_banco
        WHERE saldo_diario IS NOT NULL ORDER BY fecha DESC LIMIT 1
    """)
    fila = cur_local.fetchone()
    if fila:
        return float(fila[0]), fila[1]
    return None, None


def guardar_meta(cur_nube, clave, valor):
    cur_nube.execute("""
        INSERT INTO sync_meta (clave, valor, actualizado)
        VALUES (%s, %s, now())
        ON CONFLICT (clave) DO UPDATE
        SET valor = EXCLUDED.valor, actualizado = now()
    """, (clave, json.dumps(valor, default=str)))


def sync(conn_local, conn_nube, ahora=None):
    """Replica todas las tablas en UNA transaccion en la nube.
    Devuelve {tabla: filas_copiadas}."""
    ahora = ahora or datetime.now()
    total = {}
    with conn_local.cursor() as cur_local:
        with conn_nube:  # commit al salir sin excepcion; rollback si falla
            with conn_nube.cursor() as cur_nube:
                cur_nube.execute(f"TRUNCATE {', '.join(TABLAS_ORDEN)}")
                for tabla in TABLAS_ORDEN:
                    columnas, filas = leer_tabla(cur_local, tabla)
                    if filas:
                        replicar_tabla(cur_nube, tabla, columnas, filas)
                    total[tabla] = len(filas)
                saldo, fecha_saldo = obtener_saldo_banco(cur_local)
                guardar_meta(cur_nube, "saldo_banco",
                             {"saldo": saldo, "fecha": fecha_saldo})
                guardar_meta(cur_nube, "ultimo_sync",
                             {"momento": ahora.isoformat(timespec="seconds"),
                              "filas": total})
    return total


def aplicar_esquema(conn_nube):
    """--init: copia el esquema de las 5 tablas desde la BD local (pg_dump
    --schema-only) y aplica las views. Idempotente en las views; si una tabla
    ya existe en la nube, pg_dump/psql reportara el error y seguimos."""
    from backup_db import localizar_pg_dump  # reutiliza la autodeteccion
    pg_dump = localizar_pg_dump()
    env = {**os.environ, "PGPASSWORD": os.environ.get("DB_PASSWORD", "")}
    cmd = [str(pg_dump), "--schema-only", "--no-owner", "--no-privileges",
           "-h", os.environ.get("DB_HOST", "localhost"),
           "-p", os.environ.get("DB_PORT", "5432"),
           "-U", os.environ.get("DB_USER", "postgres"),
           "-d", os.environ.get("DB_NAME", "dte_facturas_chile")]
    for tabla in TABLAS_ORDEN:
        cmd += ["-t", tabla]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True,
                       errors="replace", timeout=TIMEOUT_PG_DUMP)
    if r.returncode != 0:
        raise RuntimeError(f"pg_dump --schema-only fallo: {r.stderr[:300]}")
    with conn_nube:
        with conn_nube.cursor() as cur:
            cur.execute(r.stdout)
            cur.execute(SQL_VIEWS.read_text(encoding="utf-8"))
    log("Esquema y views aplicados en la nube (--init)")


def main(argv=None):
    """argv inyectable para los tests (pytest contamina sys.argv)."""
    inicio = time.monotonic()
    parser = argparse.ArgumentParser(description="Replica local -> InsForge")
    parser.add_argument("--init", action="store_true",
                        help="primera vez: crea esquema y views antes de sincronizar")
    args = parser.parse_args(argv)
    try:
        conn_local = conectar_local()
        conn_nube = conectar_nube()
        try:
            if args.init:
                aplicar_esquema(conn_nube)
            total = sync(conn_local, conn_nube)
        finally:
            conn_local.close()
            conn_nube.close()
        duracion = round(time.monotonic() - inicio, 1)
        resumen = ", ".join(f"{t}={n}" for t, n in total.items())
        log(f"Sync OK en {duracion}s: {resumen}")
        return 0
    except Exception as e:
        log(f"ERROR (no fatal para el pipeline): {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
