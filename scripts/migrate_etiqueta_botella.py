#!/usr/bin/env python3
"""
migrate_etiqueta_botella.py — Zigurat ERP

Agrega el insumo "Etiqueta" y lo suma al BOM de envasado de los SKU de
botella 330ml. Idempotente.

Contexto: el costo de la botella no incluia la etiqueta, asi que salia
subestimado en $193 y el margen inflado en el mismo monto. El productor
confirmo que CUALQUIER etiqueta cuesta $230 con IVA incluido, una por botella.
Los barriles no llevan etiqueta y no se tocan.
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

# $230 con IVA incluido / 1.19 = $193.28 neto. maestro_insumos guarda NETO.
PRECIO_CON_IVA = 230.0
IVA = 1.19
PRECIO_NETO_ETIQUETA = round(PRECIO_CON_IVA / IVA, 2)   # 193.28

SKUS_CON_ETIQUETA = ["CREAM-ALE-BOT-330-C12", "SCOTCH-ALE-BOT-330-C12"]


def main():
    print("=" * 60)
    print("ZIGURAT ERP — Etiqueta en el costo de la botella")
    print("=" * 60)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            cur = conn.cursor()

            print(f"\n[1] Insumo 'Etiqueta' a ${PRECIO_NETO_ETIQUETA} neto "
                  f"(${PRECIO_CON_IVA} con IVA)...")
            cur.execute(
                """
                INSERT INTO maestro_insumos (nombre, unidad, precio_neto_unitario, categoria)
                VALUES ('Etiqueta', 'unidad', %s, 'etiqueta')
                ON CONFLICT (nombre) DO UPDATE SET precio_neto_unitario = EXCLUDED.precio_neto_unitario
                """,
                (PRECIO_NETO_ETIQUETA,)
            )
            cur.execute("SELECT id FROM maestro_insumos WHERE nombre = 'Etiqueta'")
            insumo_id = cur.fetchone()[0]
            print(f"  OK  insumo id={insumo_id}")

            print("\n[2] Sumando la etiqueta al BOM de envasado...")
            agregados, ya_estaban, no_encontrados = 0, 0, []
            for codigo in SKUS_CON_ETIQUETA:
                cur.execute("SELECT id FROM sku WHERE codigo = %s", (codigo,))
                fila = cur.fetchone()
                if not fila:
                    no_encontrados.append(codigo)
                    continue
                cur.execute(
                    """
                    INSERT INTO sku_envasado (sku_id, insumo_id, cantidad)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (sku_id, insumo_id) DO NOTHING
                    """,
                    (fila[0], insumo_id)
                )
                if cur.rowcount:
                    agregados += 1
                    print(f"  NUEVO      {codigo}")
                else:
                    ya_estaban += 1
                    print(f"  YA ESTABA  {codigo}")

            print("\n[3] Costo unitario resultante:")
            cur.execute(
                """
                SELECT codigo, ROUND(costo_total_unitario, 2)
                FROM vista_costo_sku
                WHERE codigo = ANY(%s) ORDER BY codigo
                """,
                (SKUS_CON_ETIQUETA,)
            )
            for codigo, costo in cur.fetchall():
                print(f"  {codigo:26s} ${costo}")

        print(f"\nAgregados: {agregados} · ya estaban: {ya_estaban}")
        if no_encontrados:
            print("\nATENCION — SKU no encontrados (revisar el codigo exacto):")
            for c in no_encontrados:
                print(f"  - {c}")

    except psycopg2.Error as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
