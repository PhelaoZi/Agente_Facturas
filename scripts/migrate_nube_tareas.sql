-- migrate_nube_tareas.sql — Zigurat Movil, Fase 4 (Agenda de Tareas)
-- Tabla en la nube para agendar compromisos y tareas personales para el dueño.
-- El agente tiene permisos de escritura (INSERT/UPDATE) sobre esta tabla.

CREATE TABLE IF NOT EXISTS chat_tareas (
    id          BIGSERIAL PRIMARY KEY,
    descripcion TEXT NOT NULL,
    fecha       DATE NOT NULL,
    completada  BOOLEAN NOT NULL DEFAULT FALSE,
    creado      TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_tareas_fecha ON chat_tareas (fecha);
