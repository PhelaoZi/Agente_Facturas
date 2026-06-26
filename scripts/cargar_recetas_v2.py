#!/usr/bin/env python3
"""
cargar_recetas_v2.py — Zigurat ERP
Carga las 4 recetas desde Recetas.xlsx.
Para Cream Ale y Scotch Ale: BORRA y recarga receta_detalle.
Para Paint it Black y Stout Café/Cacao: crea receta + detalle.
Idempotente (upsert en recetas, delete+insert en detalle).

Uso:
    python scripts/cargar_recetas_v2.py
    python scripts/cargar_recetas_v2.py /ruta/alternativa/Recetas.xlsx
"""
import os, sys, re
from pathlib import Path

from _console import force_utf8

force_utf8()

try:
    import psycopg2
    import openpyxl
except ImportError as e:
    print(f"ERROR: Falta dependencia — {e}")
    print("Instala con: pip install psycopg2-binary openpyxl")
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

EXCEL_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "OneDrive" / "Recetas.xlsx"

# Parámetros estándar de lote (CLAUDE.md — capa B)
LOTE_LITROS     = 540
COSTO_MO        = 300_000
COSTO_SERVICIOS = 185_000
MERMA_PCT       = 5.0

# Normaliza nombres de receta leídos del Excel (row 0, .title())
RECIPE_NAMES = {
    "Cream Ale":        "Cream Ale",
    "Scotch Ale":       "Scotch Ale",
    "Paint It Black":   "Paint it Black",
    "Stout Cafe/Cacao": "Stout Café/Cacao",
}

# Mapeo nombre_excel.lower().strip() → nombre en maestro_insumos
INSUMO_MAP = {
    "pilsen":                           "Malta Pilsen",
    "caradextrina":                     "Malta Caradex",
    "trigo":                            "Trigo Malteado claro",
    "levadura ay4":                     "Levadura AY4",
    "lupulo magnum":                    "Lupulo Magnum",
    "fosforico":                        "Fosfórico",
    "fosfórico":                        "Fosfórico",
    "clarificante polyclar coccion":    "Clarificante Polyclar coccion",
    "clarificante polyclar maduracion": "Clarificante Polyclar 10 maduracion",
    "clarifiante maduracion sb3":       "Clarificante SB3 maduracion",
    "pale ale":                         "Malta Pale Ale",
    "munich":                           "Malta Munich",
    "cara 50":                          "Malta Cara 50",
    "biscuit":                          "Malta Biscuit",
    "aroma":                            "Malta Arome",
    "chocolate":                        "Malta Chocolate",
    "cara aroma":                       "Malta Cara Aroma",
    "carafa 2":                         "Malta Carafa 2",
    "cebada tostada":                   "Cebada Tostada",
    "avena":                            "Avena",
    "hojuela de cebada":                "Hojuela de Cebada",
    "frambuesa":                        "Frambuesa",
    "vainilla":                         "Vainilla",
    "cafe":                             "Café",
    "cacao":                            "Cacao",
}

UNIDAD_MAP = {
    "kg": "kg",
    "grs": "gr", "gr": "gr", "grs.": "gr",
    "ml": "ml", "ml.": "ml",
    "ltrs": "litro", "ltr": "litro", "litros": "litro", "litro": "litro",
}


def parse_cantidad(qty_str):
    """'100 kg' → (100.0, 'kg'),  '500 grs' → (500.0, 'gr')"""
    if not qty_str:
        return None, None
    m = re.match(r"([\d.]+)\s*(\w+\.?)", str(qty_str).strip())
    if not m:
        return None, None
    cantidad = float(m.group(1))
    unidad = UNIDAD_MAP.get(m.group(2).lower().rstrip("."), m.group(2).lower())
    return cantidad, unidad


def leer_recetas_excel(path):
    """
    Parsea el Excel con 4 recetas en columnas paralelas.
    Col 0-1: Cream Ale, Col 3-4: Scotch Ale,
    Col 6-7: Paint it Black, Col 9-10: Stout Café/Cacao.
    Devuelve dict: nombre_receta → [(nombre_insumo_bd, cantidad), ...]
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = [row for row in ws.iter_rows(values_only=True) if any(c is not None for c in row)]

    # Fila 0: nombres de receta
    COLUMNAS = [(0, 1), (3, 4), (6, 7), (9, 10)]
    nombres_raw = [str(rows[0][c[0]]).strip().title() for c in COLUMNAS]
    nombres_receta = [RECIPE_NAMES.get(n, n) for n in nombres_raw]

    recetas = {nombre: [] for nombre in nombres_receta}

    for row in rows[1:]:
        for (col_n, col_q), nombre_receta in zip(COLUMNAS, nombres_receta):
            ingrediente = row[col_n]
            cantidad_str = row[col_q]
            if not ingrediente:
                continue
            key = str(ingrediente).strip().lower()
            nombre_bd = INSUMO_MAP.get(key)
            if not nombre_bd:
                print(f"  AVISO: sin mapeo '{ingrediente}' en {nombre_receta}")
                continue
            cantidad, _ = parse_cantidad(cantidad_str)
            if cantidad is None:
                print(f"  AVISO: cantidad inválida '{cantidad_str}' para '{ingrediente}'")
                continue
            recetas[nombre_receta].append((nombre_bd, cantidad))

    return recetas


def main():
    if not EXCEL_PATH.exists():
        print(f"ERROR: No se encontró el Excel en {EXCEL_PATH}")
        print("Uso: python scripts/cargar_recetas_v2.py [ruta_excel]")
        sys.exit(1)

    print(f"Leyendo: {EXCEL_PATH}")
    recetas = leer_recetas_excel(EXCEL_PATH)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            cur = conn.cursor()

            # Cache nombre → id de todos los insumos
            cur.execute("SELECT nombre, id FROM maestro_insumos")
            insumo_ids = {row[0]: row[1] for row in cur.fetchall()}

            for nombre_receta, ingredientes in recetas.items():
                # Upsert receta (crea si no existe, actualiza si existe)
                cur.execute(
                    """
                    INSERT INTO recetas
                        (nombre_cerveza, litros_lote_estandar,
                         costo_mano_obra_lote, costo_servicios_lote, merma_porcentaje)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (nombre_cerveza) DO UPDATE SET
                        litros_lote_estandar = EXCLUDED.litros_lote_estandar,
                        costo_mano_obra_lote = EXCLUDED.costo_mano_obra_lote,
                        costo_servicios_lote = EXCLUDED.costo_servicios_lote,
                        merma_porcentaje     = EXCLUDED.merma_porcentaje
                    RETURNING id
                    """,
                    (nombre_receta, LOTE_LITROS, COSTO_MO, COSTO_SERVICIOS, MERMA_PCT)
                )
                receta_id = cur.fetchone()[0]

                # Borrar detalle anterior y recargar desde Excel
                cur.execute("DELETE FROM receta_detalle WHERE receta_id = %s", (receta_id,))
                borrados = cur.rowcount

                filas_ok = 0
                sin_id = []
                for nombre_bd, cantidad in ingredientes:
                    insumo_id = insumo_ids.get(nombre_bd)
                    if not insumo_id:
                        sin_id.append(nombre_bd)
                        continue
                    cur.execute(
                        """
                        INSERT INTO receta_detalle (receta_id, insumo_id, cantidad_requerida)
                        VALUES (%s, %s, %s)
                        """,
                        (receta_id, insumo_id, cantidad)
                    )
                    filas_ok += 1

                print(f"\n{nombre_receta} (id={receta_id})")
                print(f"  Ingredientes cargados: {filas_ok}/{len(ingredientes)}")
                if borrados:
                    print(f"  Filas antiguas eliminadas: {borrados}")
                if sin_id:
                    print(f"  ATENCIÓN — no encontrados en maestro_insumos: {sin_id}")

        print("\nRecetas cargadas correctamente.")

    except psycopg2.Error as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
