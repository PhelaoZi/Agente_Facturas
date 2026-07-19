# Plan: Chat móvil — paridad de consultas

> **For agentic workers:** ejecución inline en la sesión del 2026-07-19 (aprobación directa de Christian). Spec: `docs/superpowers/specs/2026-07-19-chat-movil-paridad-consultas-design.md`.

**Goal:** que el chat del teléfono responda cualquier consulta de lectura (últimas facturas, detalle por folio, costos, márgenes y SQL ad-hoc blindado), sin escrituras de negocio.

**Architecture:** mismas 3 piezas de la Fase 4 — views canónicas en la réplica (`migrate_nube_views.sql`), tools fijas en `functions/_shared/chat_tools.ts`, prompt en `chat_prompt.ts` — más la réplica de `vista_costo_sku` en `sync_nube.py`. `consulta_sql` corre dentro de una transacción `READ ONLY` de Postgres.

**Tech stack:** Deno/TS (postgres.js 3.4.5), Python (psycopg2), pytest + deno test.

## Global constraints

- La réplica es de SOLO lectura de negocio; únicas escrituras permitidas: chat_sesiones/chat_uso/chat_tareas (ya existentes).
- Reglas canónicas en SQL de views, nunca en las tools.
- `sync_nube.py` sigue siendo NO FATAL (exit 1 = warning del pipeline).
- Mensajes de commit en español, commits chicos.

---

### Task 1: Views nuevas + tabla costo_sku (migrate_nube_views.sql)

**Modify:** `scripts/migrate_nube_views.sql`

- `CREATE TABLE IF NOT EXISTS costo_sku` (codigo TEXT, nombre_cerveza TEXT, formato TEXT, costo_liquido_unitario NUMERIC, costo_envasado_unitario NUMERIC, costo_total_unitario NUMERIC) — réplica materializada de `vista_costo_sku` local (mismas 6 columnas que consulta `app/negocio/costos.py`).
- `v_factura_cabecera`: ventas SIN filtro de tipo (folio, tipo_documento, fecha, rut_cliente, razon_social_receptor, neto_real y total_real con COALESCE, monto_total original, `tiene_nc` = monto_total_ajustado IS NOT NULL, fecha_pago, dias_pago). La tool avisa si el folio es una NC (tipo 61).
- `v_lineas_factura`: productos con `tipo_linea` = CASE logistica / envase_pet (regex canónica) / producto.

### Task 2: sync_nube.py replica la vista de costos y autorepara las views

**Modify:** `scripts/sync_nube.py`, **Test:** `tests/test_sync_nube.py`

- `VISTAS_REPLICADAS = {"costo_sku": ("vista_costo_sku", [6 columnas])}` — TRUNCATE junto a TABLAS_ORDEN (misma sentencia, misma transacción), lectura local con columnas explícitas (inmune a columnas extra de la vista), conteo en `ultimo_sync`.
- `aplicar_migraciones_chat` → pasa a aplicar también `SQL_VIEWS` en cada corrida (idempotente, autoreparable; crea la tabla costo_sku antes del primer sync). `--init` queda solo para el bootstrap de esquema.
- Tests: costo_sku en el TRUNCATE y replicado al final; sigue fuera de TABLAS_ORDEN; views aplicadas en cada corrida; contenido del SQL tiene las 2 views nuevas y la tabla.

### Task 3: tools canónicas nuevas (ultimas_facturas, detalle_factura, costos_sku, margenes)

**Modify:** `functions/_shared/chat_tools.ts`, **Test:** `functions/_shared/chat_tools_test.ts`

- TDD: subir el conteo TOOLS 13→18, luego cada tool con su test (formato con `formatearPesos`).
- `ultimas_facturas(limite=5, tope 20)`: v_ventas_reales ORDER BY fecha DESC, folio DESC.
- `detalle_factura(folio)`: cabecera en v_factura_cabecera (prefiere tipo 33; si solo hay 61 avisa que es NC) + líneas de v_lineas_factura etiquetadas `[Logistica]`/`[Envase PET]`; si `tiene_nc`, muestra total original → ajustado; estado pagada/pendiente.
- `costos_sku(receta?, sku?)`: tabla costo_sku, filtros ILIKE/= (4 ramas como listar_gastos).
- `margenes(receta?)`: port de `app/negocio/costos.py` — `PRECIOS_VENTA_NETO` {cream ale 55370, scotch ale 55370, stout cafe 75000, stout cacao 75000, paint it black 98000}, `_norm` sin tildes, precio solo si formato contiene "barril"; margen y % o "sin precio confirmado".

### Task 4: consulta_sql de solo lectura blindada

**Modify:** `functions/_shared/chat_tools.ts` (+ tipo `SqlCliente` con `begin` opcional), **Test:** `chat_tools_test.ts`

```ts
// Validación: 1 sentencia SELECT/WITH, sin ';' interno (se tolera uno final)
const q = consulta.trim().replace(/;\s*$/, "");
if (!/^(select|with)\b/i.test(q)) return "Error: solo SELECT/WITH...";
if (q.includes(";")) return "Error: una sola sentencia...";
// Ejecución: BEGIN READ ONLY + timeout; postgres.js
const filas = await sql.begin("read only", async (t) => {
  await t.unsafe("SET LOCAL statement_timeout = 8000");
  return await t.unsafe(q);
});
// Salida: máx 100 filas y ~4000 chars, con aviso de truncado; errores SQL
// devueltos como texto para que el modelo se corrija.
```

- Tests: rechaza INSERT/UPDATE/DELETE/DROP y `SELECT ...; DROP`, tolera `;` final, pasa opción "read only" a begin (fake lo asegura), setea timeout, trunca filas, error SQL → texto "Error de SQL".

### Task 5: prompt del chat móvil

**Modify:** `functions/_shared/chat_prompt.ts`

- Quitar "No tienes acceso a SQL". Nueva sección: tools canónicas SIEMPRE primero; `consulta_sql` solo para lo que ninguna cubre; reglas SQL canónicas (COALESCE ajustados, excluir 61 en sumas, folio/tipo_documento enteros, COUNT DISTINCT rut_cliente, filtro Logistica/PET); catálogo de tablas (6 replicadas + costo_sku) y views v_*.

### Task 6: suites locales

- `deno test functions/_shared/` y `python -m pytest -q` verdes.

### Task 7: deploy y E2E

1. `deno bundle -o nube/dist/chat.bundle.js functions/chat.ts` (patrón Fase 4).
2. Subir la function `chat` (CLI de InsForge con `.insforge/project.json`; fallback: dashboard → Functions → pegar bundle).
3. `python scripts/sync_nube.py` — aplica views nuevas + replica costo_sku.
4. E2E `python scripts/test_chat_nube.py` + verificación de Christian desde la PWA.

### Task 8: commit/push por task lógico y cierre
