# Spec: Chat móvil — paridad de consultas con el Centro de Comando

**Fecha:** 2026-07-19 · **Estado:** aprobado por Christian (chat, mismo día)

## Problema

El chat del teléfono responde "solo veo datos agregados" ante preguntas como
"¿cuál es la última factura?" o "dame el detalle del folio X". Causa: tiene 13
tools fijas y su prompt le prohíbe el SQL. El chat del PC sí responde cualquier
consulta porque además de sus tools canónicas tiene `mcp__postgres__query` de
solo lectura.

## Alcance (aprobado)

1. **5 tools nuevas** en `functions/_shared/chat_tools.ts`:
   - `ultimas_facturas(limite)` — últimas N facturas (folio, fecha, cliente,
     total ajustado, pagada/pendiente).
   - `detalle_factura(folio)` — cabecera + todas las líneas etiquetadas
     (producto / logistica / envase_pet), aviso de NC aplicada, estado de pago.
   - `costos_sku(receta?, sku?)` y `margenes(receta?)` — espejo de
     `app/negocio/costos.py` (misma lógica y precios confirmados).
   - `consulta_sql(consulta)` — SQL ad-hoc de SOLO lectura sobre la réplica.
2. **Seguridad de `consulta_sql` (3 capas):** (a) solo `SELECT`/`WITH`, una
   sola sentencia (sin `;` interno); (b) transacción Postgres `READ ONLY` —
   la BD rechaza cualquier escritura; (c) `statement_timeout` + tope de filas
   y de caracteres en la salida. Opera solo contra la réplica InsForge; la BD
   local no participa.
3. **Costos a la réplica:** `sync_nube.py` replica `vista_costo_sku` (local)
   como tabla `costo_sku` (nube), con columnas explícitas. Las views nuevas
   (`v_lineas_factura`, `v_factura_cabecera`) van en `migrate_nube_views.sql`,
   que pasa a aplicarse en CADA sync (idempotente, patrón autoreparable de las
   migraciones de chat) — deja de requerir `--init` para actualizar views.
4. **Prompt** (`chat_prompt.ts`): jerarquía tools-canónicas-primero /
   `consulta_sql` último recurso, reglas SQL canónicas (COALESCE ajustados,
   excluir tipo 61, filtro Logistica/PET, folio/tipo_documento enteros) y
   catálogo de tablas/views disponibles.

## Fuera de alcance

- Escrituras de negocio desde el móvil (ventas, clientes, gastos): siguen
  bloqueadas; la agenda de tareas queda como está.
- Lienzo de artefactos y modo gerente comercial (seguimiento): solo PC.

## Aceptación

- Desde el teléfono: "¿cuál es la última factura registrada?", "detalle de la
  factura 4664", "¿cuánto cuesta producir una Cream Ale?", "margen del Stout" y
  una pregunta ad-hoc (p. ej. "¿cuántas facturas emitimos por mes este año?")
  se responden con cifras de la réplica.
- `consulta_sql` rechaza INSERT/UPDATE/DELETE/DDL y multi-sentencia; una
  escritura disfrazada (CTE `WITH x AS (DELETE ...)`) muere en la transacción
  READ ONLY de Postgres.
- `deno test` de functions y `python -m pytest -q` verdes; deploy a InsForge
  hecho y probado E2E.
