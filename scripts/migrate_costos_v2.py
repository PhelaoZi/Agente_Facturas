#!/usr/bin/env python3
"""
migrate_costos_v2.py - Zigurat ERP
Migración del módulo de costos de producción (capa B).
Idempotente: se puede ejecutar múltiples veces sin efectos secundarios.

Uso:
    python scripts/migrate_costos_v2.py
"""

import os
import sys
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

# Categorías estándar para clasificar insumos
CATEGORIAS_VALIDAS = (
    'malta', 'lupulo', 'levadura', 'adjunto', 'clarificante',
    'envase', 'tapa', 'etiqueta', 'caja'
)

# Migración por pasos. Cada string se ejecuta como sentencia única.
MIGRATIONS = [
    # 1. maestro_insumos: agregar columnas categoria, activo, precio_revisar
    """
    ALTER TABLE maestro_insumos
        ADD COLUMN IF NOT EXISTS categoria VARCHAR(20) NOT NULL DEFAULT 'malta',
        ADD COLUMN IF NOT EXISTS activo BOOLEAN NOT NULL DEFAULT TRUE,
        ADD COLUMN IF NOT EXISTS precio_revisar BOOLEAN NOT NULL DEFAULT FALSE
    """,
    # 2. CHECK de categoría (drop+create para idempotencia)
    """
    ALTER TABLE maestro_insumos
        DROP CONSTRAINT IF EXISTS chk_categoria_insumo
    """,
    """
    ALTER TABLE maestro_insumos
        ADD CONSTRAINT chk_categoria_insumo CHECK (categoria IN (
            'malta','lupulo','levadura','adjunto','clarificante',
            'envase','tapa','etiqueta','caja'
        ))
    """,
    # 3. recetas: agregar costo_mano_obra_lote, costo_servicios_lote, merma_porcentaje
    """
    ALTER TABLE recetas
        ADD COLUMN IF NOT EXISTS costo_mano_obra_lote NUMERIC(12,2) NOT NULL DEFAULT 300000,
        ADD COLUMN IF NOT EXISTS costo_servicios_lote NUMERIC(12,2) NOT NULL DEFAULT 185000,
        ADD COLUMN IF NOT EXISTS merma_porcentaje     NUMERIC(5,2)  NOT NULL DEFAULT 5.0
    """,
    """
    ALTER TABLE recetas DROP CONSTRAINT IF EXISTS chk_merma
    """,
    """
    ALTER TABLE recetas ADD CONSTRAINT chk_merma CHECK (merma_porcentaje BETWEEN 0 AND 30)
    """,
    """
    ALTER TABLE recetas DROP CONSTRAINT IF EXISTS chk_litros
    """,
    """
    ALTER TABLE recetas ADD CONSTRAINT chk_litros CHECK (litros_lote_estandar > 0)
    """,
    # 4. Tabla formatos
    """
    CREATE TABLE IF NOT EXISTS formatos (
        id           SERIAL PRIMARY KEY,
        nombre       VARCHAR(50) UNIQUE NOT NULL,
        capacidad_ml INTEGER NOT NULL CHECK (capacidad_ml > 0),
        retornable   BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
    # 5. Seed de formatos (idempotente con ON CONFLICT)
    """
    INSERT INTO formatos (nombre, capacidad_ml, retornable) VALUES
        ('Botella 330ml',     330,    FALSE),
        ('Barril 30L acero',  30000,  TRUE),
        ('Barril 30L PET',    30000,  FALSE)
    ON CONFLICT (nombre) DO NOTHING
    """,
    # 6. Tabla sku
    """
    CREATE TABLE IF NOT EXISTS sku (
        id            SERIAL PRIMARY KEY,
        receta_id     INTEGER NOT NULL REFERENCES recetas(id),
        formato_id    INTEGER NOT NULL REFERENCES formatos(id),
        codigo        VARCHAR(30) UNIQUE NOT NULL,
        nombre        VARCHAR(100) NOT NULL,
        unidades_caja INTEGER,
        activo        BOOLEAN NOT NULL DEFAULT TRUE,
        UNIQUE(receta_id, formato_id, unidades_caja),
        CHECK (unidades_caja IS NULL OR unidades_caja IN (12, 24))
    )
    """,
    # 7. Tabla sku_envasado
    """
    CREATE TABLE IF NOT EXISTS sku_envasado (
        id        SERIAL PRIMARY KEY,
        sku_id    INTEGER NOT NULL REFERENCES sku(id) ON DELETE CASCADE,
        insumo_id INTEGER NOT NULL REFERENCES maestro_insumos(id),
        cantidad  NUMERIC(10,4) NOT NULL CHECK (cantidad > 0),
        UNIQUE(sku_id, insumo_id)
    )
    """,
    # 8. Recategorizar los 15 insumos existentes (todos son cerveza/líquido)
    """
    UPDATE maestro_insumos
    SET categoria = CASE
        WHEN nombre ILIKE 'malta%'           THEN 'malta'
        WHEN nombre ILIKE '%trigo malteado%' THEN 'malta'
        WHEN nombre ILIKE 'lupulo%'          THEN 'lupulo'
        WHEN nombre ILIKE 'levadura%'        THEN 'levadura'
        WHEN nombre ILIKE 'clarificante%'    THEN 'clarificante'
        ELSE categoria
    END
    WHERE categoria = 'malta' AND nombre IS NOT NULL
    """,
    # 9. Detección de precios sospechosos (maltas > $50.000/kg)
    """
    UPDATE maestro_insumos
    SET precio_revisar = TRUE
    WHERE categoria = 'malta' AND precio_neto_unitario > 50000
    """,
    # 10. Drop vista vieja (si existe) y crear vista_costo_sku
    """
    DROP VIEW IF EXISTS vista_costos_recetas
    """,
    """
    CREATE OR REPLACE VIEW vista_costo_sku AS
    WITH
    costo_liquido AS (
        SELECT r.id AS receta_id,
               r.litros_lote_estandar * (1 - r.merma_porcentaje/100) AS litros_envasables,
               (SUM(rd.cantidad_requerida * mi.precio_neto_unitario)
                + r.costo_mano_obra_lote
                + r.costo_servicios_lote) AS costo_lote_total
        FROM recetas r
        JOIN receta_detalle rd ON rd.receta_id = r.id
        JOIN maestro_insumos mi ON mi.id = rd.insumo_id
        GROUP BY r.id
    ),
    costo_envase AS (
        SELECT se.sku_id,
               SUM(se.cantidad * mi.precio_neto_unitario) AS costo_envasado_unitario
        FROM sku_envasado se
        JOIN maestro_insumos mi ON mi.id = se.insumo_id
        GROUP BY se.sku_id
    )
    SELECT s.id AS sku_id,
           s.codigo,
           s.nombre,
           r.nombre_cerveza,
           f.nombre AS formato,
           cl.costo_lote_total / cl.litros_envasables * (f.capacidad_ml/1000.0) AS costo_liquido_unitario,
           COALESCE(ce.costo_envasado_unitario, 0) AS costo_envasado_unitario,
           (cl.costo_lote_total / cl.litros_envasables * (f.capacidad_ml/1000.0)
            + COALESCE(ce.costo_envasado_unitario, 0)) AS costo_total_unitario
    FROM sku s
    JOIN recetas r        ON r.id = s.receta_id
    JOIN formatos f       ON f.id = s.formato_id
    JOIN costo_liquido cl ON cl.receta_id = s.receta_id
    LEFT JOIN costo_envase ce ON ce.sku_id = s.id
    WHERE s.activo
    """,
]


def main():
    print("=" * 60)
    print("ZIGURAT ERP — Migración Costos de Producción (capa B)")
    print("=" * 60)
    print()

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    print(f"Conectado a: {DB_CONFIG['dbname']}")
    print()

    try:
        with conn:
            cur = conn.cursor()
            for i, sql in enumerate(MIGRATIONS, 1):
                cur.execute(sql)
                print(f"  OK migración {i}/{len(MIGRATIONS)}")

            # Reportar precios sospechosos detectados
            cur.execute("""
                SELECT id, nombre, unidad, precio_neto_unitario
                FROM maestro_insumos
                WHERE precio_revisar = TRUE
                ORDER BY id
            """)
            sospechosos = cur.fetchall()

        print()
        print("Migración completada.")
        print()
        print("Estructura creada:")
        print("  - maestro_insumos: +categoria, +activo, +precio_revisar")
        print("  - recetas: +costo_mano_obra_lote, +costo_servicios_lote, +merma_porcentaje")
        print("  - tabla nueva: formatos (3 filas seed)")
        print("  - tabla nueva: sku")
        print("  - tabla nueva: sku_envasado")
        print("  - vista nueva: vista_costo_sku (reemplaza vista_costos_recetas)")
        print()

        if sospechosos:
            print(f"ATENCIÓN: {len(sospechosos)} insumo(s) con precio sospechoso (precio_revisar=TRUE):")
            for row in sospechosos:
                print(f"  id={row[0]:3d}  {row[1]:30s}  ${row[3]:>15,.2f}/{row[2]}")
            print()
            print("Corregir con: /actualizar-precio-insumo \"<nombre>\" <precio_correcto>")
        else:
            print("Sin precios sospechosos detectados.")

    except psycopg2.Error as e:
        print(f"\nERROR en migración: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
