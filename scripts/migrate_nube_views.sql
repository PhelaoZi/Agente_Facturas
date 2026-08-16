-- migrate_nube_views.sql — Zigurat Movil
-- Esquema minimo de la replica InsForge: metadatos del sync + views canonicas.
-- Las reglas de negocio del CLAUDE.md viven AQUI y solo aqui (las edge
-- functions consultan views, nunca reimplementan reglas). Idempotente.

CREATE TABLE IF NOT EXISTS sync_meta (
    clave       TEXT PRIMARY KEY,
    valor       JSONB NOT NULL,
    actualizado TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ventas reales por factura: montos ajustados por NC, excluye las NC mismas.
CREATE OR REPLACE VIEW v_ventas_reales AS
SELECT v.folio, v.tipo_documento, v.fecha, v.rut_cliente,
       v.razon_social_receptor,
       COALESCE(v.monto_neto_ajustado,  v.monto_neto)  AS neto_real,
       COALESCE(v.monto_total_ajustado, v.monto_total) AS total_real,
       v.fecha_pago, v.dias_pago
FROM ventas v
WHERE v.tipo_documento != '61';

-- Por cobrar COBRABLE (excluye clientes castigados como incobrables).
CREATE OR REPLACE VIEW v_pendientes AS
SELECT v.folio, v.fecha, v.rut_cliente, c.razon_social,
       COALESCE(v.monto_total_ajustado, v.monto_total) AS total,
       (CURRENT_DATE - v.fecha) AS dias_desde_emision
FROM ventas v
JOIN clientes c ON c.rut_cliente = v.rut_cliente
WHERE v.tipo_documento != '61'
  AND v.fecha_pago IS NULL
  AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
  AND COALESCE(c.estado, '') <> 'incobrable';

-- Espejo EXACTO de obtener_facturas_pendientes de app/negocio/flujo.py
-- (el flujo historicamente incluye incobrables; mantener paridad).
CREATE OR REPLACE VIEW v_flujo_pendientes AS
SELECT folio, fecha, rut_cliente, razon_social_receptor,
       COALESCE(monto_total_ajustado, monto_total) AS monto
FROM ventas
WHERE fecha_pago IS NULL AND tipo_documento != '61';

-- Espejo de obtener_avg_dias_por_cliente de app/negocio/flujo.py
-- (ultimas 10 facturas pagadas, minimo 3 para promediar).
CREATE OR REPLACE VIEW v_dias_pago_cliente AS
SELECT rut_cliente, AVG(dias_pago) AS avg_dias
FROM (
    SELECT rut_cliente, dias_pago,
           ROW_NUMBER() OVER (PARTITION BY rut_cliente ORDER BY fecha DESC) AS rn
    FROM ventas
    WHERE fecha_pago IS NOT NULL AND dias_pago IS NOT NULL
      AND dias_pago > 0 AND tipo_documento != '61'
) t
WHERE rn <= 10
GROUP BY rut_cliente
HAVING COUNT(*) >= 3;

-- Lineas de producto SIN Logistica ni envases PET (filtro canonico del
-- CLAUDE.md raiz, adaptado a la columna real `nombre_producto`).
CREATE OR REPLACE VIEW v_ventas_producto AS
SELECT p.folio, v.fecha, v.rut_cliente, p.nombre_producto,
       p.cantidad, p.precio_unitario
FROM productos p
JOIN ventas v ON v.folio = p.folio AND v.tipo_documento = p.tipo_documento
WHERE v.tipo_documento != '61'
  AND p.nombre_producto NOT ILIKE '%logist%'
  AND p.nombre_producto !~* '^(barril(es)?\s+)?pet\y';

-- Replica materializada de la vista local vista_costo_sku (la llena
-- sync_nube.py en cada corrida; columnas fijas = las que consulta el chat).
CREATE TABLE IF NOT EXISTS costo_sku (
    codigo                   TEXT,
    nombre_cerveza           TEXT,
    formato                  TEXT,
    costo_liquido_unitario   NUMERIC,
    costo_envasado_unitario  NUMERIC,
    costo_total_unitario     NUMERIC
);

-- Traduccion nombre-escrito -> cerveza, replicada del PC. Christian escribe el
-- nombre a mano en cada factura: 125 formas distintas, 84 de ellas cerveza, que
-- colapsan en 27. Sin esta tabla cualquier SQL agrupa por el nombre crudo y
-- parte las unidades de una cerveza entre sus erratas.
CREATE TABLE IF NOT EXISTS linea_canonica (
    linea_id            INTEGER PRIMARY KEY,
    tipo_documento      INTEGER,
    folio               INTEGER,
    nombre_producto     TEXT,
    cerveza             TEXT,
    formato             TEXT,
    litros              INTEGER,
    clase               TEXT
);

CREATE INDEX IF NOT EXISTS idx_linea_canonica_cerveza
    ON linea_canonica (cerveza);

-- Espejo de la vista local: UNIDADES por cerveza con el nombre ya traducido.
-- `clase` reemplaza a los filtros ILIKE '%logist%' repartidos por el codigo.
CREATE OR REPLACE VIEW v_lineas_producto AS
SELECT p.id            AS linea_id,
       p.folio,
       p.tipo_documento,
       v.fecha,
       v.rut_cliente,
       c.razon_social,
       p.nombre_producto,
       lc.cerveza,
       lc.formato,
       lc.litros,
       lc.clase,
       p.cantidad,
       p.total_linea
FROM productos p
JOIN linea_canonica lc ON lc.linea_id = p.id
JOIN ventas v          ON v.folio = p.folio AND v.tipo_documento = p.tipo_documento
LEFT JOIN clientes c   ON c.rut_cliente = v.rut_cliente;

-- Replica de la capa de atribucion: cuanta plata dejo cada cerveza.
-- La atribucion NO se recalcula aqui. Se calcula en el PC (scripts/
-- calcular_atribucion.py, unica fuente de verdad) y viaja ya resuelta, igual
-- que costo_sku. Tener el motor en dos lados seria tener la regla en dos
-- lados, que es exactamente como se llego a este problema.
CREATE TABLE IF NOT EXISTS ingreso_producto (
    tipo_documento          INTEGER,
    folio                   INTEGER,
    fecha_evento            DATE,
    rut_cliente             TEXT,
    razon_social            TEXT,
    cerveza                 TEXT,
    formato                 TEXT,
    litros                  INTEGER,
    unidades                NUMERIC(14, 3),
    ingreso_neto_atribuido  NUMERIC(14, 2),
    logistica_atribuida     NUMERIC(14, 2),
    fuente                  TEXT,
    metodo                  TEXT,
    calidad                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingreso_producto_cerveza
    ON ingreso_producto (cerveza);
CREATE INDEX IF NOT EXISTS idx_ingreso_producto_fecha
    ON ingreso_producto (fecha_evento);

-- Mismo nombre que en el PC a proposito: una consulta escrita contra la BD
-- local corre igual contra la replica, y el prompt nombra una sola cosa.
CREATE OR REPLACE VIEW v_ingreso_producto AS
SELECT * FROM ingreso_producto;

-- Cabecera de CUALQUIER documento por folio (incluye NC tipo 61: la tool
-- del chat avisa si el folio es una nota de credito, no una factura).
CREATE OR REPLACE VIEW v_factura_cabecera AS
SELECT v.folio, v.tipo_documento, v.fecha, v.rut_cliente,
       v.razon_social_receptor,
       COALESCE(v.monto_neto_ajustado,  v.monto_neto)  AS neto_real,
       COALESCE(v.monto_total_ajustado, v.monto_total) AS total_real,
       v.monto_total                                   AS total_original,
       (v.monto_total_ajustado IS NOT NULL)            AS tiene_nc,
       v.fecha_pago, v.dias_pago
FROM ventas v;

-- Lineas de detalle etiquetadas (regla canonica Logistica/PET del CLAUDE.md).
-- Aqui NO se filtra: el detalle de una factura muestra TODAS sus lineas.
CREATE OR REPLACE VIEW v_lineas_factura AS
SELECT p.folio, p.tipo_documento, p.nombre_producto, p.cantidad,
       p.precio_unitario,
       (p.cantidad * p.precio_unitario) AS subtotal,
       CASE
         WHEN p.nombre_producto ILIKE '%logist%'             THEN 'logistica'
         WHEN p.nombre_producto ~* '^(barril(es)?\s+)?pet\y' THEN 'envase_pet'
         ELSE 'producto'
       END AS tipo_linea
FROM productos p;
