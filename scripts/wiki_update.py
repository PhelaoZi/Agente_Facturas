#!/usr/bin/env python3
"""
wiki_update.py - Zigurat ERP
Genera fichas Markdown por cliente en wiki/clientes/ a partir de PostgreSQL.

Uso:
    python wiki_update.py --todos              # Todos los clientes
    python wiki_update.py --ruts 12345678-9    # Uno o más RUTs separados por coma
    python wiki_update.py --cliente "NOMBRE"   # Busca por razón social (parcial)
"""

import argparse
import io
import os
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path

# Forzar salida UTF-8 en consola Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta la librería psycopg2.")
    print("Instala con: pip install psycopg2-binary")
    sys.exit(1)


# ─── Carga de variables de entorno desde .env ─────────────────────────────────
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


# ─── Configuración de conexión ────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}


# ─── Rutas del proyecto ──────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
WIKI_DIR = BASE_DIR / "wiki"
CLIENTES_DIR = WIKI_DIR / "clientes"
INDEX_PATH = WIKI_DIR / "index.md"
LOG_PATH = BASE_DIR / "logs" / "wiki_update.log"


# ─── Conexión ─────────────────────────────────────────────────────────────────

def conectar():
    """Establece conexión a PostgreSQL."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL:")
        print(f"  {e}")
        print()
        print("Verifica que PostgreSQL esté corriendo y que los datos de")
        print("conexión en .env sean correctos.")
        sys.exit(1)


# ─── Utilidades ───────────────────────────────────────────────────────────────

def slugify(razon_social):
    """Convierte razón social a slug para nombre de archivo.

    Ejemplo: 'CERVECERÍA MARINA SPA' → 'cerveceria-marina-spa'
    """
    # Normalizar unicode: quitar acentos y caracteres especiales
    texto = unicodedata.normalize("NFKD", razon_social)
    texto = texto.encode("ascii", "ignore").decode("ascii")
    # Minúsculas, reemplazar espacios y caracteres no alfanuméricos por guiones
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    # Limpiar guiones duplicados y en los extremos
    texto = re.sub(r"-+", "-", texto).strip("-")
    return texto


def fmt_monto(n):
    """Formatea un número como monto chileno.

    Ejemplo: 1234567 → '$1.234.567'
    """
    if n is None:
        return "$0"
    # Convertir a entero y formatear con separador de miles
    entero = int(round(n))
    signo = "-" if entero < 0 else ""
    entero = abs(entero)
    formateado = f"{entero:,}".replace(",", ".")
    return f"{signo}${formateado}"


def fmt_fecha(d):
    """Formatea una fecha como 'YYYY-MM-DD' o '—' si es None."""
    if d is None:
        return "—"
    if isinstance(d, str):
        return d
    return d.strftime("%Y-%m-%d")


# ─── Argumentos CLI ───────────────────────────────────────────────────────────

def parse_args():
    """Parsea argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Genera fichas wiki de clientes desde PostgreSQL"
    )

    # Grupo mutuamente exclusivo: selección de clientes
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument(
        "--todos", action="store_true",
        help="Procesar todos los clientes"
    )
    grupo.add_argument(
        "--ruts", type=str,
        help="RUTs separados por coma (ej: 12345678-9,98765432-1)"
    )
    grupo.add_argument(
        "--cliente", type=str,
        help="Buscar por razón social (coincidencia parcial)"
    )

    parser.add_argument(
        "--origen", type=str, default=None,
        help="Etiqueta de origen para logging"
    )

    return parser.parse_args()


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = parse_args()

    print("=" * 60)
    print("  WIKI UPDATE — Zigurat ERP")
    print("=" * 60)

    # Determinar modo de ejecución
    if args.todos:
        modo = "todos los clientes"
    elif args.ruts:
        modo = f"RUTs: {args.ruts}"
    elif args.cliente:
        modo = f"busqueda: '{args.cliente}'"
    print(f"  Modo: {modo}")
    if args.origen:
        print(f"  Origen: {args.origen}")
    print()
