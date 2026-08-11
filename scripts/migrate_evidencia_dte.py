#!/usr/bin/env python3
"""
migrate_evidencia_dte.py — Zigurat ERP
Crea la capa de evidencia del DTE: las tablas dte_lineas, dte_ajustes_globales,
dte_impuestos y dte_archivos. Idempotente.

Por qué existe
--------------
Hasta el 2026-08-10 el pipeline descartaba cuatro cosas que el XML sí trae, y
que no se pueden reconstruir después:

- las líneas llamadas "Logistica" a secas, que son cerca de la mitad del precio
  del barril (por eso el ingreso por producto salía a un tercio de lo real);
- los descuentos globales <DscRcgGlobal>, que hacen que el monto de una línea NO
  sea su neto;
- el código de impuesto por línea <CodImpAdic>, que es el SII declarando cuál
  línea es cerveza;
- los <ImptoReten> más allá del primero, con su tipo y su tasa.

Del histórico ya no hay nada que hacer: sobreviven 2 XML de 876 documentos. Esto
corta la hemorragia hacia adelante.

Estas tablas son EVIDENCIA: se escriben una vez, fieles al XML, y nadie las
corrige después. Los cálculos derivados (atribución de ingreso por producto) van
en tablas aparte que se pueden recalcular de cero.
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

SQL = """
-- ── Detalle completo, sin filtrar ────────────────────────────────────────────
-- Es `productos` sin recortes: incluye la logística y las columnas que el SII
-- declara por línea. Los montos van con el signo del XML original (las notas de
-- crédito los traen positivos); el signo económico se deriva de tipo_documento
-- al atribuir, nunca del valor guardado.
CREATE TABLE IF NOT EXISTS dte_lineas (
    id               SERIAL PRIMARY KEY,
    tipo_documento   INTEGER NOT NULL,
    folio            INTEGER NOT NULL,
    -- <NroLinDet>: sin esto, dos líneas del mismo producto en un documento son
    -- indistinguibles. Ya pasa: el folio 4344 tiene dos "Barril 30L Cream Ale".
    nro_linea        INTEGER,
    nombre_producto  TEXT,
    descripcion      TEXT,
    cantidad         NUMERIC(14, 3),
    precio_unitario  NUMERIC(14, 2),
    total_linea      NUMERIC(14, 2),
    -- <CodImpAdic>: el 26 es el ILA de cervezas (20,5%). Lo llevan las líneas de
    -- cerveza y no la logística: es el propio SII clasificando, mejor que
    -- cualquier match por nombre sobre 123 descripciones con erratas.
    cod_imp_adic     INTEGER,
    creado           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Un documento no puede traer dos veces la misma línea. Atrapa un doble sync.
CREATE UNIQUE INDEX IF NOT EXISTS idx_dte_lineas_unica
    ON dte_lineas (tipo_documento, folio, nro_linea)
    WHERE nro_linea IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_dte_lineas_documento
    ON dte_lineas (tipo_documento, folio);

-- ── Descuentos y recargos sobre el documento ─────────────────────────────────
-- El folio 4746 trae un descuento global de $9.000 sobre $90.000 en líneas: es
-- el contraejemplo que refutó dos propuestas de reparación seguidas.
CREATE TABLE IF NOT EXISTS dte_ajustes_globales (
    id                SERIAL PRIMARY KEY,
    tipo_documento    INTEGER NOT NULL,
    folio             INTEGER NOT NULL,
    nro_linea         INTEGER,
    tipo_movimiento   TEXT,      -- 'D' descuento, 'R' recargo
    glosa             TEXT,
    tipo_valor        TEXT,      -- '$' monto fijo, '%' porcentaje
    valor             NUMERIC(14, 2),
    indicador_exento  INTEGER,
    creado            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dte_ajustes_documento
    ON dte_ajustes_globales (tipo_documento, folio);

-- ── Impuestos, con su tipo y su tasa ─────────────────────────────────────────
-- `ventas.impuesto_adicional` guarda solo el primer <MontoImp> y da por supuesta
-- una tasa de 20,5%. Un DTE puede traer varios, y la tasa hay que leerla.
CREATE TABLE IF NOT EXISTS dte_impuestos (
    id              SERIAL PRIMARY KEY,
    tipo_documento  INTEGER NOT NULL,
    folio           INTEGER NOT NULL,
    tipo            INTEGER,
    tasa            NUMERIC(6, 2),
    monto           NUMERIC(14, 2),
    creado          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dte_impuestos_documento
    ON dte_impuestos (tipo_documento, folio);

-- ── El XML de origen ─────────────────────────────────────────────────────────
-- Christian venía borrando los XML después de procesarlos, y por eso el
-- histórico ya no se puede auditar. Un XML trae varios documentos: el hash y la
-- ruta se repiten, una fila por documento.
CREATE TABLE IF NOT EXISTS dte_archivos (
    id              SERIAL PRIMARY KEY,
    tipo_documento  INTEGER NOT NULL,
    folio           INTEGER NOT NULL,
    hash_sha256     TEXT NOT NULL,
    archivo_origen  TEXT,
    ruta_archivo    TEXT,
    recibido        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tipo_documento, folio)
);
"""

TABLAS = ["dte_lineas", "dte_ajustes_globales", "dte_impuestos", "dte_archivos"]


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SQL)
            for tabla in TABLAS:
                cur.execute("""
                    SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_name = %s
                """, (tabla,))
                columnas = cur.fetchone()[0]
                cur.execute(f"SELECT COUNT(*) FROM {tabla}")
                print(f"OK: {tabla} lista ({columnas} columnas, {cur.fetchone()[0]} filas).")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
