#!/usr/bin/env python3
"""
migrate_costos_v3.py — Zigurat ERP
1. Corrige 12 precios en maestro_insumos (precio por UNIDAD, no por paquete).
2. Inserta 10 insumos nuevos con precio 0 provisional.
Idempotente.

Contexto del bug: los precios de levaduras, lúpulos y clarificantes
estaban guardados como "precio del paquete completo" en vez de
"precio por gr/ml". Eso multiplicaba el costo calculado por 100-500x.
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

# Precio por UNIDAD correcto.
# Fuentes: XML Mundo Cervecero 2026-05-20, XML Almacén Cervecero 2026-04-29,
#          Costos_Zigurat.xlsx (precio_total_lote / kg_usados).
PRECIO_CORRECCIONES = [
    ("Malta Cara Pils",                    2310.92),  # 18487.39 / 8 kg
    ("Malta Cara Ruby",                    2415.97),  # 19327.73 / 8 kg
    ("Malta Arome",                        2176.47),  # 17411.76 / 8 kg
    ("Malta Biscuit",                      2415.97),  # 19327.73 / 8 kg
    ("Malta Chocolate",                    2512.61),  # XML: PrcItem=2512.61/kg
    ("Malta Cara Aroma",                   2932.77),  # XML: PrcItem=2932.77/kg
    ("Trigo Malteado claro",               1470.59),  # 11764.71 / 8 kg
    ("Lupulo Magnum",                        36.37),  # 3636.60 / 100 gr
    ("Levadura AY4",                         89.11),  # 44555 / 500 gr
    ("Clarificante Polyclar coccion",        49.40),  # 4940 / 100 gr
    ("Clarificante Polyclar 10 maduracion",  32.40),  # 3239.50 / 100 gr
    ("Clarificante SB3 maduracion",          13.61),  # 6806.72 / 500 ml
]

# Nuevos insumos para las 4 recetas. Precio 0 provisional.
NUEVOS_INSUMOS = [
    ("Fosfórico",          "ml",    0.0, "adjunto"),
    ("Malta Cara 50",      "kg",    0.0, "malta"),
    ("Malta Carafa 2",     "kg",    0.0, "malta"),
    ("Cebada Tostada",     "kg",    0.0, "malta"),
    ("Avena",              "kg",    0.0, "adjunto"),
    ("Hojuela de Cebada",  "kg",    0.0, "adjunto"),
    ("Frambuesa",          "kg",    0.0, "adjunto"),
    ("Vainilla",           "litro", 0.0, "adjunto"),
    ("Café",               "kg",    0.0, "adjunto"),
    ("Cacao",              "kg",    0.0, "adjunto"),
]


def main():
    print("=" * 60)
    print("ZIGURAT ERP — Migración Costos v3 (corrección precios)")
    print("=" * 60)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            cur = conn.cursor()

            # 1. Corregir precios existentes
            print("\n[1] Corrigiendo precios...")
            actualizados = 0
            no_encontrados = []
            for nombre, precio in PRECIO_CORRECCIONES:
                cur.execute(
                    "UPDATE maestro_insumos SET precio_neto_unitario = %s WHERE nombre = %s",
                    (precio, nombre)
                )
                if cur.rowcount:
                    actualizados += 1
                    print(f"  OK  {nombre:45s}  ${precio:.4f}")
                else:
                    no_encontrados.append(nombre)

            # 2. Insertar insumos nuevos
            print("\n[2] Insertando insumos faltantes...")
            insertados = 0
            for nombre, unidad, precio, categoria in NUEVOS_INSUMOS:
                cur.execute(
                    """
                    INSERT INTO maestro_insumos (nombre, unidad, precio_neto_unitario, categoria)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (nombre) DO NOTHING
                    """,
                    (nombre, unidad, precio, categoria)
                )
                if cur.rowcount:
                    insertados += 1
                    print(f"  NUEVO  {nombre}")
                else:
                    print(f"  YA EXISTE  {nombre}")

        print()
        print(f"Precios corregidos: {actualizados}/12")
        print(f"Insumos nuevos: {insertados}/10")
        if no_encontrados:
            print(f"\nATENCIÓN — no encontrados (revisar nombre exacto):")
            for n in no_encontrados:
                print(f"  - {n}")

    except psycopg2.Error as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
