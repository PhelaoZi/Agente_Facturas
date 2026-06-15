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
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None  # fecha imposible (ej. mes 13): tratar como nombre ajeno


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


# --- Archivo de estado (consumible por el dashboard en el futuro) ----------------

def escribir_estado(
    backup_dir: Path | str,
    resultado: str,
    archivo: str | None = None,
    tamano_bytes: int | None = None,
    duracion_segundos: float | None = None,
    error: str | None = None,
) -> None:
    """Escribe _estado.json con el resultado del último intento.

    En caso de error preserva `ultimo_ok` del estado anterior, para poder
    responder "¿hace cuánto que no tengo un backup bueno?".
    """
    ruta = Path(backup_dir) / "_estado.json"
    ahora = datetime.now().isoformat(timespec="seconds")

    ultimo_ok = ahora if resultado == "ok" else None
    if resultado != "ok" and ruta.exists():
        try:
            ultimo_ok = json.loads(ruta.read_text(encoding="utf-8")).get("ultimo_ok")
        except (json.JSONDecodeError, OSError):
            ultimo_ok = None  # estado previo ilegible: no inventar fechas

    estado = {
        "ultimo_intento": ahora,
        "resultado": resultado,
        "ultimo_ok": ultimo_ok,
        "archivo": archivo,
        "tamano_bytes": tamano_bytes,
        "duracion_segundos": duracion_segundos,
        "error": error,
    }
    ruta.write_text(
        json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# --- Log -------------------------------------------------------------------------

def log(mensaje: str) -> None:
    """Imprime y anexa a logs/backup_db.log con timestamp."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    linea = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {mensaje}"
    print(linea)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


# --- Dump y verificación -----------------------------------------------------------

def ejecutar_dump(pg_dump: Path, destino_part: Path) -> None:
    """Corre pg_dump -Fc hacia el archivo .part.

    La contraseña va en PGPASSWORD del entorno del subproceso: nunca en la
    línea de comandos (sería visible en el administrador de tareas).
    """
    env = {**os.environ, "PGPASSWORD": os.environ.get("DB_PASSWORD", "")}
    cmd = [
        str(pg_dump),
        "-Fc",
        "-h", os.environ.get("DB_HOST", "localhost"),
        "-p", os.environ.get("DB_PORT", "5432"),
        "-U", os.environ.get("DB_USER", "postgres"),
        "-d", os.environ.get("DB_NAME", "dte_facturas_chile"),
        "-f", str(destino_part),
    ]
    r = subprocess.run(
        cmd, env=env, capture_output=True, text=True, errors="replace",
        timeout=TIMEOUT_SEGUNDOS,
    )
    if r.returncode != 0:
        detalle = ((r.stderr or "").strip().splitlines() or ["(sin detalle)"])[-1]
        raise RuntimeError(f"pg_dump falló (código {r.returncode}): {detalle[:300]}")


def verificar_dump(pg_dump: Path, archivo: Path) -> None:
    """Valida que el dump sea legible con pg_restore --list.

    Un backup ilegible es peor que ninguno: da falsa seguridad.
    """
    pg_restore = pg_dump.parent / "pg_restore.exe"
    if not pg_restore.exists():
        raise FileNotFoundError(f"No existe pg_restore junto a pg_dump: {pg_restore}")
    r = subprocess.run(
        [str(pg_restore), "--list", str(archivo)],
        capture_output=True, text=True, errors="replace",
        timeout=TIMEOUT_SEGUNDOS,
    )
    if r.returncode != 0:
        detalle = ((r.stderr or "").strip().splitlines() or ["(sin detalle)"])[-1]
        raise RuntimeError(f"Verificación falló, dump ilegible: {detalle[:300]}")


def aplicar_retencion(backup_dir: Path, hoy: date) -> list[str]:
    """Borra del disco los dumps que la política de retención descarta."""
    nombres = [p.name for p in Path(backup_dir).glob("*.dump")]
    borrar = archivos_a_borrar(nombres, hoy)
    borrados = []
    for nombre in borrar:
        try:
            (Path(backup_dir) / nombre).unlink()
            log(f"Retención: borrado {nombre}")
            borrados.append(nombre)
        except OSError as e:
            log(f"Retención: no se pudo borrar {nombre}: {e}")
    return borrados


# --- Main ------------------------------------------------------------------------

def main() -> int:
    inicio = time.monotonic()
    backup_dir = Path(os.environ.get("BACKUP_DIR", DEFAULT_BACKUP_DIR))
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        pg_dump = localizar_pg_dump()
        momento = datetime.now()
        nombre = nombre_dump(momento)
        destino = backup_dir / nombre
        part = backup_dir / (nombre + ".part")

        log(f"Iniciando backup -> {destino}")
        try:
            ejecutar_dump(pg_dump, part)
            verificar_dump(pg_dump, part)
        except BaseException:
            # Nunca dejar un dump corrupto o a medias en la carpeta.
            try:
                part.unlink(missing_ok=True)
            except OSError as e_limpieza:
                log(f"No se pudo borrar el .part tras fallo: {e_limpieza}")
            raise
        os.replace(part, destino)   # sobrescribe atómicamente en Windows (Path.rename falla si destino existe)

        tamano = destino.stat().st_size
        borrados = aplicar_retencion(backup_dir, momento.date())
        duracion = round(time.monotonic() - inicio, 1)
        escribir_estado(
            backup_dir, "ok",
            archivo=nombre, tamano_bytes=tamano, duracion_segundos=duracion,
        )
        log(
            f"Backup OK: {nombre} ({tamano / 1024:.0f} KB, {duracion}s, "
            f"retención borró {len(borrados)} archivo(s))"
        )
        return 0
    except Exception as e:
        duracion = round(time.monotonic() - inicio, 1)
        try:
            escribir_estado(
                backup_dir, "error", duracion_segundos=duracion, error=str(e)
            )
        except OSError as e2:
            log(f"ERROR adicional al escribir _estado.json: {e2}")
        log(f"ERROR en backup: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
