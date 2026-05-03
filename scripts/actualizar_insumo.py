#!/usr/bin/env python3
"""
actualizar_insumo.py - Zigurat ERP
Crea o actualiza un insumo en maestro_insumos.
Loggea cambios de precio en logs/insumos_precios.log.

Uso:
    python actualizar_insumo.py "nombre" unidad precio_neto categoria

Ejemplo:
    python actualizar_insumo.py "Lupulo Citra" gr 9500 lupulo
    python actualizar_insumo.py "Botella 330ml ambar" un 250 envase
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

CATEGORIAS_VALIDAS = (
    'malta', 'lupulo', 'levadura', 'adjunto', 'clarificante',
    'envase', 'tapa', 'etiqueta', 'caja'
)


def _log_cambio_precio(nombre, precio_anterior, precio_nuevo):
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "insumos_precios.log"
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"{ts} | {nombre} | {precio_anterior} -> {precio_nuevo}\n"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line)


def main():
    if len(sys.argv) != 5:
        print('Uso: python actualizar_insumo.py "nombre" unidad precio_neto categoria')
        print('Ejemplo: python actualizar_insumo.py "Lupulo Citra" gr 9500 lupulo')
        print(f'Categorías válidas: {", ".join(CATEGORIAS_VALIDAS)}')
        sys.exit(1)

    nombre        = sys.argv[1].strip()
    unidad        = sys.argv[2].strip()
    precio_raw    = sys.argv[3].replace('.', '').replace(',', '.')
    categoria     = sys.argv[4].strip().lower()

    if categoria not in CATEGORIAS_VALIDAS:
        print(f"ERROR: Categoría '{categoria}' inválida.")
        print(f"Válidas: {', '.join(CATEGORIAS_VALIDAS)}")
        sys.exit(1)

    try:
        precio = float(precio_raw)
    except ValueError:
        print(f"ERROR: Precio inválido: {sys.argv[3]}")
        sys.exit(1)

    if precio <= 0:
        print(f"ERROR: Precio debe ser > 0 (recibido: {precio})")
        sys.exit(1)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    with conn:
        cur = conn.cursor()
        # Buscar si existe
        cur.execute("SELECT id, precio_neto_unitario FROM maestro_insumos WHERE nombre = %s", (nombre,))
        row = cur.fetchone()

        if row:
            insumo_id, precio_anterior = row
            cur.execute("""
                UPDATE maestro_insumos
                SET unidad = %s,
                    precio_neto_unitario = %s,
                    categoria = %s,
                    precio_revisar = FALSE,
                    actualizado_el = NOW()
                WHERE id = %s
            """, (unidad, precio, categoria, insumo_id))
            _log_cambio_precio(nombre, precio_anterior, precio)
            accion = "actualizado"
        else:
            cur.execute("""
                INSERT INTO maestro_insumos (nombre, unidad, precio_neto_unitario, categoria, actualizado_el)
                VALUES (%s, %s, %s, %s, NOW())
                RETURNING id
            """, (nombre, unidad, precio, categoria))
            insumo_id = cur.fetchone()[0]
            _log_cambio_precio(nombre, None, precio)
            accion = "creado"

    conn.close()

    precio_fmt = "$" + "{:,.2f}".format(precio).replace(",", ".")
    print(f"Insumo {accion} (id={insumo_id})")
    print(f"   Nombre:    {nombre}")
    print(f"   Unidad:    {unidad}")
    print(f"   Precio:    {precio_fmt} / {unidad}")
    print(f"   Categoría: {categoria}")


if __name__ == "__main__":
    main()
