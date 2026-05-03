#!/usr/bin/env python3
"""
cargar_sku.py - Zigurat ERP
Crea un SKU (combinación cerveza × formato) y su sku_envasado desde JSON.

Uso:
    python cargar_sku.py sku.json

Formato del JSON:
{
  "codigo": "IPA-MAND-330-C12",
  "nombre": "IPA WC Mandarina botella 330ml caja 12",
  "receta": "IPA West Coast Mandarina",
  "formato": "Botella 330ml",
  "unidades_caja": 12,
  "envasado": [
    {"insumo": "Botella 330ml ambar", "cantidad": 1},
    {"insumo": "Tapa corona", "cantidad": 1},
    {"insumo": "Etiqueta IPA Mandarina", "cantidad": 1},
    {"insumo": "Caja carton 12", "cantidad": 0.0833}
  ]
}

Para barriles: omitir unidades_caja y dejar envasado vacío (acero) o
con barril+tapón (PET).
"""

import json
import os
import sys
from pathlib import Path

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

CATEGORIAS_ENVASE_VALIDAS = ('envase', 'tapa', 'etiqueta', 'caja')


def main():
    if len(sys.argv) != 2:
        print("Uso: python cargar_sku.py sku.json")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    if not json_path.exists():
        print(f"ERROR: Archivo no encontrado: {json_path}")
        sys.exit(1)

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    # Validación básica de campos
    for campo in ("codigo", "nombre", "receta", "formato"):
        if campo not in data:
            print(f"ERROR: falta campo '{campo}'")
            sys.exit(1)

    codigo        = data["codigo"].strip()
    nombre        = data["nombre"].strip()
    receta_nombre = data["receta"].strip()
    formato_nombre = data["formato"].strip()
    unidades_caja = data.get("unidades_caja")
    envasado      = data.get("envasado", [])

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            cur = conn.cursor()

            # Resolver receta y formato
            cur.execute("SELECT id FROM recetas WHERE nombre_cerveza = %s", (receta_nombre,))
            row = cur.fetchone()
            if not row:
                print(f"ERROR: Receta '{receta_nombre}' no existe.")
                sys.exit(1)
            receta_id = row[0]

            cur.execute("SELECT id, capacidad_ml FROM formatos WHERE nombre = %s", (formato_nombre,))
            row = cur.fetchone()
            if not row:
                print(f"ERROR: Formato '{formato_nombre}' no existe.")
                sys.exit(1)
            formato_id, capacidad_ml = row

            # Validar regla unidades_caja según formato
            es_botella = capacidad_ml < 1000
            if es_botella:
                if unidades_caja not in (12, 24):
                    print("ERROR: para Botella, unidades_caja debe ser 12 o 24.")
                    sys.exit(1)
            else:
                if unidades_caja is not None:
                    print("ERROR: para Barril, unidades_caja debe ser null/omitido.")
                    sys.exit(1)

            # Resolver insumos del envasado y validar categoría
            if envasado:
                nombres_envase = [e["insumo"].strip() for e in envasado]
                cur.execute(
                    "SELECT id, nombre, categoria FROM maestro_insumos WHERE nombre = ANY(%s)",
                    (nombres_envase,)
                )
                rows = cur.fetchall()
                mapa = {r[1]: r for r in rows}
                faltantes = [n for n in nombres_envase if n not in mapa]
                if faltantes:
                    print("ERROR: insumos de envasado no existen en maestro_insumos:")
                    for n in faltantes:
                        print(f"  - {n}")
                    sys.exit(1)
                for n, _, cat in [(r[1], r[0], r[2]) for r in rows]:
                    if cat not in CATEGORIAS_ENVASE_VALIDAS:
                        print(f"ERROR: insumo '{n}' tiene categoría '{cat}' (debe ser envase/tapa/etiqueta/caja).")
                        sys.exit(1)
            else:
                mapa = {}

            # Verificar conflicto de código
            cur.execute("SELECT id, receta_id, formato_id FROM sku WHERE codigo = %s", (codigo,))
            existente = cur.fetchone()
            if existente and (existente[1] != receta_id or existente[2] != formato_id):
                print(f"ERROR: código '{codigo}' ya existe con otra receta/formato.")
                sys.exit(1)

            # Upsert SKU
            if existente:
                sku_id = existente[0]
                cur.execute("""
                    UPDATE sku SET nombre = %s, unidades_caja = %s, activo = TRUE WHERE id = %s
                """, (nombre, unidades_caja, sku_id))
                cur.execute("DELETE FROM sku_envasado WHERE sku_id = %s", (sku_id,))
                accion = "actualizado"
            else:
                cur.execute("""
                    INSERT INTO sku (receta_id, formato_id, codigo, nombre, unidades_caja)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                """, (receta_id, formato_id, codigo, nombre, unidades_caja))
                sku_id = cur.fetchone()[0]
                accion = "creado"

            for e in envasado:
                cur.execute("""
                    INSERT INTO sku_envasado (sku_id, insumo_id, cantidad)
                    VALUES (%s, %s, %s)
                """, (sku_id, mapa[e["insumo"].strip()][0], e["cantidad"]))
    finally:
        conn.close()

    print(f"SKU {accion} (id={sku_id})")
    print(f"   Código:   {codigo}")
    print(f"   Nombre:   {nombre}")
    print(f"   Receta:   {receta_nombre}")
    print(f"   Formato:  {formato_nombre}")
    if unidades_caja:
        print(f"   Caja:     {unidades_caja} unidades")
    print(f"   Envasado: {len(envasado)} insumo(s)")


if __name__ == "__main__":
    main()
