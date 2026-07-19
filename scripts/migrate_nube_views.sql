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
