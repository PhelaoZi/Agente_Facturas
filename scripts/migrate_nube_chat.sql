-- migrate_nube_chat.sql — Zigurat Movil, Fase 4 (chat de consultas)
-- Tablas PROPIAS de la nube: el unico lugar donde la nube escribe.
-- Idempotente: sync_nube.py la aplica en CADA corrida (autoreparacion si la
-- replica se recrea). NUNCA agregar estas tablas a TABLAS_ORDEN.

CREATE TABLE IF NOT EXISTS chat_sesiones (
    id          BIGSERIAL PRIMARY KEY,
    mensajes    JSONB NOT NULL DEFAULT '[]'::jsonb,
    creado      TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Log de uso/costo por consulta: base del tope de gasto diario y auditoria.
CREATE TABLE IF NOT EXISTS chat_uso (
    id               BIGSERIAL PRIMARY KEY,
    sesion_id        BIGINT REFERENCES chat_sesiones(id),
    modelo           TEXT NOT NULL,
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    n_llamadas_tools INTEGER NOT NULL DEFAULT 0,
    costo_usd        NUMERIC(10,6) NOT NULL DEFAULT 0,
    creado           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_uso_creado ON chat_uso (creado);
