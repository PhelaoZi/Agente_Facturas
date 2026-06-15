#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backup_db.py - Zigurat ERP
Backup diario verificado de la BD dte_facturas_chile a OneDrive.

Flujo: pg_dump -Fc -> archivo .part -> verificación con pg_restore --list ->
renombrar a .dump -> retención (60 días + primer dump de cada mes) ->
_estado.json + logs/backup_db.log.

Uso:
    python scripts/backup_db.py

Configuración (.env, todas opcionales salvo DB_PASSWORD):
    DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD  - conexión
    BACKUP_DIR    - carpeta destino (default: C:\\Users\\cdela\\OneDrive\\Backups\\zigurat-db)
    PG_DUMP_PATH  - ruta a pg_dump.exe (default: autodetectar en Program Files)

RESTAURACIÓN COMPLETA (disco nuevo / BD corrupta), en PowerShell:
    $env:PGPASSWORD = "<DB_PASSWORD del .env>"
    & "C:\\Program Files\\PostgreSQL\\16\\bin\\createdb.exe" -h localhost -U postgres dte_facturas_chile
    & "C:\\Program Files\\PostgreSQL\\16\\bin\\pg_restore.exe" --clean --if-exists `
        -h localhost -U postgres -d dte_facturas_chile "<ruta al .dump>"

Restauración selectiva de una tabla: agregar  -t ventas  al pg_restore.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / "logs" / "backup_db.log"
DEFAULT_BACKUP_DIR = Path(r"C:\Users\cdela\OneDrive\Backups\zigurat-db")
BASE_POSTGRES = Path(r"C:\Program Files\PostgreSQL")
RETENCION_DIAS = 60
TIMEOUT_SEGUNDOS = 300
PATRON_DUMP = re.compile(r"^zigurat_dte_(\d{4}-\d{2}-\d{2})_\d{4}\.dump$")


# --- Carga de variables de entorno desde .env (patrón del proyecto) ----------

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


# --- Nombres de archivo -------------------------------------------------------

def nombre_dump(momento: datetime) -> str:
    """Nombre del archivo de backup para un instante dado."""
    return f"zigurat_dte_{momento:%Y-%m-%d_%H%M}.dump"


def fecha_de_nombre(nombre: str) -> date | None:
    """Extrae la fecha del nombre de un dump; None si el nombre no es nuestro.

    Se parsea del nombre y no del mtime porque OneDrive puede alterar mtimes.
    """
    m = PATRON_DUMP.match(nombre)
    return date.fromisoformat(m.group(1)) if m else None


# --- Retención -----------------------------------------------------------------

def archivos_a_borrar(nombres: list[str], hoy: date) -> list[str]:
    """Aplica la política de retención sobre una lista de nombres de archivo.

    Se borra un dump si tiene más de RETENCION_DIAS y NO es el más antiguo de
    su mes calendario (el primero de cada mes se conserva para siempre).
    Nombres que no calzan con PATRON_DUMP nunca se borran (defensivo).
    """
    validos = [(n, f) for n in nombres if (f := fecha_de_nombre(n)) is not None]

    # El más antiguo de cada mes. En este formato de nombre, orden
    # lexicográfico == orden cronológico (incluida la hora HHMM).
    primero_del_mes: dict[tuple[int, int], str] = {}
    for nombre, fecha in sorted(validos):
        primero_del_mes.setdefault((fecha.year, fecha.month), nombre)
    conservar = set(primero_del_mes.values())

    return [
        nombre
        for nombre, fecha in validos
        if (hoy - fecha).days > RETENCION_DIAS and nombre not in conservar
    ]


# --- Localización de binarios de PostgreSQL --------------------------------------

def localizar_pg_dump(base: Path = BASE_POSTGRES) -> Path:
    """Encuentra pg_dump.exe. Orden: PG_DUMP_PATH del .env > versión más alta
    instalada en Program Files > PATH del sistema.

    Si PG_DUMP_PATH está definido pero no existe, es un error explícito:
    una configuración rota no debe degradar silenciosamente a otra cosa.
    """
    configurado = os.environ.get("PG_DUMP_PATH")
    if configurado:
        ruta = Path(configurado)
        if ruta.exists():
            return ruta
        raise FileNotFoundError(
            f"PG_DUMP_PATH apunta a un archivo inexistente: {configurado}"
        )

    candidatos = sorted(
        base.glob("*/bin/pg_dump.exe"),
        key=lambda p: int(p.parent.parent.name)
        if p.parent.parent.name.isdigit()
        else -1,
    )
    if candidatos:
        return candidatos[-1]

    en_path = shutil.which("pg_dump")
    if en_path:
        return Path(en_path)

    raise FileNotFoundError(
        "No se encontró pg_dump.exe. Define PG_DUMP_PATH en el .env o "
        "instala PostgreSQL en C:\\Program Files\\PostgreSQL."
    )
