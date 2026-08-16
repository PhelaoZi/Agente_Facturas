#!/usr/bin/env python3
"""
migrate_atribucion_ingreso.py — Zigurat ERP
Crea la capa DERIVADA de atribución de ingreso por producto y su vista
canónica. Idempotente.

Por qué existe
--------------
Responde "cuánta plata dejó cada cerveza", que el sistema no sabía contestar: el
ranking de Cream Ale daba un tercio de lo real porque nadie sumaba la línea de
logística.

Esta capa es DERIVADA, no evidencia. Se borra y se recalcula entera desde
`ventas` y `productos`, que no se tocan. Por eso cada fila declara `fuente`,
`metodo`, `calidad` y `version_algoritmo`: una cifra deducida de la cabecera y
una que venía escrita en el documento no pueden verse iguales en el dashboard —
así fue como se llegó a este problema.

La separación viene de la auditoría externa (docs/debate-arquitectura/09-...):

| Concepto | Responde |
|---|---|
| `fuente`  | ¿de dónde salió el dato? `linea_dte` / `residual_cabecera` |
| `metodo`  | ¿cómo se calculó? `cerveza_unica`, `reparto_litros`, … |
| `calidad` | ¿qué se puede afirmar? `deterministica` / `estimada` |
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
-- ── Una fila por documento: cobertura y por qué ──────────────────────────────
-- Permite responder "de cuánto estamos hablando y cuánto quedó afuera" sin
-- recorrer las líneas, y deja el motivo a la vista en vez de un silencio.
CREATE TABLE IF NOT EXISTS atribucion_documento (
    tipo_documento      INTEGER NOT NULL,
    folio               INTEGER NOT NULL,
    fecha               DATE,
    signo_evento        SMALLINT NOT NULL,     -- +1 factura, -1 nota de crédito
    neto_documento      NUMERIC(14, 2),
    monto_atribuido     NUMERIC(14, 2),
    monto_pass_through  NUMERIC(14, 2),        -- envase PET y CO2
    monto_sin_atribuir  NUMERIC(14, 2),
    estado              TEXT NOT NULL,         -- atribuido / no_atribuido
    motivo              TEXT,                  -- por qué, cuando no se atribuyó
    version_algoritmo   TEXT NOT NULL,
    calculado_en        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tipo_documento, folio)
);

-- ── Una fila por cerveza dentro de un documento ──────────────────────────────
CREATE TABLE IF NOT EXISTS atribucion_ingreso (
    id                      SERIAL PRIMARY KEY,
    tipo_documento          INTEGER NOT NULL,
    folio                   INTEGER NOT NULL,
    -- Identidad de la línea original. NO (tipo, folio, producto): el folio 4344
    -- tiene dos "Barril 30L Cream Ale" y esa clave los confunde.
    linea_id                INTEGER,
    cerveza                 TEXT NOT NULL,
    formato                 TEXT,
    litros                  INTEGER,
    unidades                NUMERIC(14, 3),
    monto_linea_evidencia   NUMERIC(14, 2),    -- lo que decía la línea
    logistica_atribuida     NUMERIC(14, 2),    -- lo que le tocó de logística
    ingreso_neto_atribuido  NUMERIC(14, 2),    -- la suma: el ingreso real
    fuente                  TEXT NOT NULL,
    metodo                  TEXT NOT NULL,
    calidad                 TEXT NOT NULL,
    version_algoritmo       TEXT NOT NULL,
    calculado_en            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Traducción nombre-escrito → cerveza, para TODAS las líneas ───────────────
-- Christian escribe el nombre a mano en cada factura: 125 formas distintas, de
-- las cuales 84 son cerveza y colapsan en 27. `clasificacion_lineas.py` ya lo
-- resuelve, pero vive en Python, así que cualquier SQL vuelve al nombre crudo y
-- agrupa `Barril 30L APA` y `Barril 30L  APA` (doble espacio) por separado.
--
-- NO corrige `productos`: esa tabla es lo que dice el documento tributario y
-- reescribirla sería falsificar la evidencia. Se traduce al consultar.
CREATE TABLE IF NOT EXISTS linea_canonica (
    linea_id            INTEGER PRIMARY KEY,   -- productos.id
    tipo_documento      INTEGER NOT NULL,
    folio               INTEGER NOT NULL,
    nombre_producto     TEXT,                  -- tal como se escribió
    cerveza             TEXT,                  -- NULL si la línea no es cerveza
    formato             TEXT,                  -- barril / botella / lata
    litros              INTEGER,
    clase               TEXT NOT NULL,         -- cerveza/logistica/envase/co2/…
    version_algoritmo   TEXT NOT NULL,
    calculado_en        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_linea_canonica_cerveza
    ON linea_canonica (cerveza);
CREATE INDEX IF NOT EXISTS idx_linea_canonica_clase
    ON linea_canonica (clase);

CREATE INDEX IF NOT EXISTS idx_atribucion_documento_fk
    ON atribucion_ingreso (tipo_documento, folio);
CREATE INDEX IF NOT EXISTS idx_atribucion_cerveza
    ON atribucion_ingreso (cerveza);
CREATE INDEX IF NOT EXISTS idx_atribucion_linea
    ON atribucion_ingreso (linea_id);
"""

# La vista va aparte: CREATE OR REPLACE VIEW falla si cambian las columnas, así
# que se elimina primero. No hay datos que perder — es una vista.
SQL_VISTA = """
DROP VIEW IF EXISTS v_ingreso_producto;
DROP VIEW IF EXISTS v_lineas_producto;

-- ── Las líneas de venta con el nombre ya traducido ───────────────────────────
-- Para UNIDADES por cerveza (para dinero está `v_ingreso_producto`). `clase`
-- reemplaza a los filtros con ILIKE '%logist%' repartidos por todo el código:
-- una columna, no una expresión que hay que acordarse de escribir bien.
CREATE VIEW v_lineas_producto AS
SELECT p.id            AS linea_id,
       p.folio,
       p.tipo_documento,
       v.fecha,
       v.rut_cliente,
       c.razon_social,
       p.nombre_producto,          -- lo que dice la factura, sin tocar
       lc.cerveza,                 -- el nombre canónico
       lc.formato,
       lc.litros,
       lc.clase,
       p.cantidad,
       p.total_linea
FROM productos p
JOIN linea_canonica lc ON lc.linea_id = p.id
JOIN ventas v          ON v.folio = p.folio AND v.tipo_documento = p.tipo_documento
LEFT JOIN clientes c   ON c.rut_cliente = v.rut_cliente;

-- ── Vista canónica: la ÚNICA fuente de dinero por producto ───────────────────
-- Modelo de eventos: las facturas suman y las notas de crédito restan, cada una
-- una vez. NO se mezcla con los montos ajustados de la factura — eso descontaría
-- la misma nota de crédito dos veces.
CREATE VIEW v_ingreso_producto AS
SELECT a.tipo_documento,
       a.folio,
       v.fecha                                   AS fecha_evento,
       v.folio_referencia,
       v.rut_cliente,
       c.razon_social,
       a.linea_id,
       a.cerveza,
       a.formato,
       a.litros,
       a.unidades,
       a.ingreso_neto_atribuido,
       a.logistica_atribuida,
       a.fuente,
       a.metodo,
       a.calidad,
       a.version_algoritmo
FROM atribucion_ingreso a
JOIN ventas v   ON v.folio = a.folio AND v.tipo_documento = a.tipo_documento
LEFT JOIN clientes c ON c.rut_cliente = v.rut_cliente;
"""

TABLAS = ["atribucion_documento", "atribucion_ingreso", "linea_canonica"]


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SQL)
            cur.execute(SQL_VISTA)
            for tabla in TABLAS:
                cur.execute(f"SELECT COUNT(*) FROM {tabla}")
                print(f"OK: {tabla} lista ({cur.fetchone()[0]} filas).")
            for vista in ("v_ingreso_producto", "v_lineas_producto"):
                cur.execute(f"SELECT COUNT(*) FROM {vista}")
                print(f"OK: {vista} lista ({cur.fetchone()[0]} filas).")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
