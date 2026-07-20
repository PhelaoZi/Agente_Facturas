# CLAUDE.md — app/

Guía específica para trabajar en la capa de aplicación (dashboard web + chat de negocio). Se carga automáticamente además del `CLAUDE.md` raíz cuando Claude Code trabaja con archivos bajo `app/`.

---

## Dashboard y chat de negocio (Centro de Comando)

Panel web local (`app/dashboard.py`, servidor `http.server` en
http://localhost:8777) que muestra la panorámica del negocio (ventas, cobranza,
flujo, costos) y, sobre todo, un **chat con un agente** que responde preguntas y
ejecuta acciones. Se abre con doble clic en `iniciar_dashboard.bat` o
`python app/dashboard.py`. Las consultas son de solo lectura; las escrituras
pasan por confirmación (ver abajo).

### Arquitectura por capas (`app/`)

| Capa | Archivos | Rol |
|------|----------|-----|
| Negocio | `app/negocio/{ventas,costos,flujo,gastos}.py` | Funciones puras que reciben un cursor y devuelven datos. Sin manejo de conexión. Testeadas con cursor falso. |
| Acciones | `app/negocio/acciones.py` | Registro `tipo_accion → (validar, ejecutar)` de las escrituras confirmadas. |
| Lienzo | `app/canvas/artifacts.py` | `Artifact` (tipo: kpi/grafico/tabla/informe/**accion**) + `Collector`. |
| Agente | `app/agent/*` | Orquestador del Claude Agent SDK + tools MCP in-process + system prompt. |
| Briefing | `app/briefing/{data,render}.py` | Datos y render del brief diario. |

El orquestador (`app/agent/orchestrator.py`) corre un **loop propio contra el
Model Gateway de OpenRouter** (migración de Antigravity, 2026-07-20; antes usaba
el Claude Agent SDK) exponiendo cuatro servidores MCP in-process — `lienzo`
(publicar artefactos), `negocio` (lecturas), `acciones` (proponer escrituras) y
`memoria` (aprendizaje persistente) — traducidos al formato de tools de OpenAI,
más `mcp__postgres__query` de solo lectura. `run_agent()` en `dashboard.py`
devuelve `{texto, artefactos}` al frontend.

**Selector de modelo:** la UI ofrece varios modelos y `POST /api/ask` acepta
`model`. La lista viva está en `MODELOS_CHAT_PERMITIDOS` (`dashboard.py`) y debe
coincidir con las `<option>` de `dashboard_ui.html`; el servidor **valida contra
esa whitelist** (el id llega del navegador). Default: `z-ai/glm-5.2`. Todos
deben soportar *tool calling*: el agente encadena hasta 8 iteraciones de tools.

**El agente del chat corre AISLADO y determinista:**
- No lee este CLAUDE.md: todo su conocimiento vive en
  `app/agent/system_prompt.py` + su memoria persistente. Si cambias una regla de
  negocio aquí, replicarla en el system prompt.
- Requiere `OPENROUTER_API_KEY` en `.env`.
- **`ejecutar_sql_local` es de SOLO LECTURA y nunca hace commit** (blindaje
  2026-07-20): valida una sola sentencia `SELECT`/`WITH`, abre la sesión con
  `set_session(readonly=True)` — Postgres mismo rechaza escrituras aunque el
  texto burle la validación (CTE con `DELETE ... RETURNING`) — y aplica
  `statement_timeout` + tope de filas. **No reintroducir ningún `conn.commit()`
  ahí:** las escrituras solo pasan por el mecanismo propose/confirm/execute.

**Memoria del agente (dos capas):**
- Conversación: `run()` devuelve `(texto, session_id)` y reanuda con `resume`;
  el botón "Limpiar" de la UI resetea vía `POST /api/chat-reset`.
- Largo plazo: `memoria-agente/MEMORIA.md` (índice compacto, inyectado al system
  prompt en cada consulta) + `memoria-agente/notas/*.md` (detalle bajo demanda,
  tools `mcp__memoria__guardar_nota` / `leer_nota`). El agente aprende reglas
  del negocio y correcciones del usuario; las notas se commitean a git.

### Herramientas del agente

- **Lectura (`mcp__negocio__*`):** `deuda_total`, `deuda_cliente`,
  `ranking_deudores`, `facturas_vencidas`, `ventas_total`, `ranking_clientes`,
  `ventas_cliente`, `ventas_producto`, `flujo_caja`, `costos_sku`, `margenes`,
  `listar_gastos`. Aplican las reglas canónicas (montos ajustados, excluir NC,
  `fecha_pago`); el prompt obliga a usarlas en vez de improvisar SQL.
- **Publicar en el lienzo (`mcp__lienzo__*`):** `publicar_kpi`,
  `publicar_grafico`, `publicar_tabla`, `publicar_informe`.
- **Proponer escrituras (`mcp__acciones__proponer_*`):** ver mecanismo abajo.

### Acciones de escritura — patrón propose / confirm / execute (Fase 2b)

**Invariante de seguridad (no negociable):** el agente **NUNCA escribe** en la
BD. Sus tools de acción solo *proponen*: publican un `Artifact(tipo="accion")`
con `{tipo_accion, params, resumen}`. El frontend lo dibuja como **tarjeta con
botón Confirmar**. Al confirmar, el navegador hace `POST /api/ejecutar-accion` y
un **endpoint determinista** valida y ejecuta el `INSERT/UPDATE/DELETE`. Ese
endpoint es el único camino de escritura. **No se cambia `permission_mode`.**

```
Usuario pide algo → agente lista (listar_gastos) y llama proponer_X
                  → publica Artifact accion {tipo_accion, params, resumen}
Frontend dibuja tarjeta → [Confirmar] → POST /api/ejecutar-accion {tipo_accion, params}
Endpoint: acciones.validar(tipo, params)        (ValueError → 400, sin tocar BD)
          acciones.ejecutar(cur, tipo, clean)   (error de BD → 500; éxito → 200 {ok, **result})
```

- **Registro:** `app/negocio/acciones.py` mapea cada `tipo_accion` a un par
  `(validar, ejecutar)`. Agregar una acción nueva = una fila ahí + una tool
  `proponer_X` en `app/agent/tools_acciones.py`. La tarjeta del frontend no
  vuelve a cambiar (es genérica: postea `{tipo_accion, params}`).
- **Acciones de gasto (`cuentas_por_pagar`) implementadas:** `registrar_gasto`,
  `borrar_gasto` (borrado definitivo), `editar_gasto` (UPDATE parcial; nombres
  de columna desde whitelist, valores parametrizados) y `marcar_gasto_pagado`
  (`pagado=TRUE, fecha_pago`). El agente las usa por descripción: primero
  `listar_gastos` para ubicar el id; la tarjeta muestra los datos exactos antes
  de confirmar (red de seguridad contra tocar el gasto equivocado).
- **Acciones de cobranza implementadas** (lógica en `app/negocio/cobranza.py`;
  `ventas.py` sigue siendo solo lectura): `marcar_factura_pagada` escribe
  `ventas.fecha_pago` (fuente de verdad del estado de cobro) — el agente ubica
  el folio con `deuda_cliente`/`facturas_vencidas` y propone con
  `proponer_marcar_factura_pagada` (fecha opcional, default hoy, nunca futura);
  rechaza doble marcado (no pisa pagos ya registrados). Para fechas mal
  registradas, `corregir_fecha_pago` (tool `proponer_corregir_fecha_pago`)
  exige factura ya pagada y fecha explícita; la tarjeta muestra
  `fecha anterior → nueva`.
- **El endpoint nunca finge éxito:** 400 en validación o gasto inexistente, 500
  en error de BD; cierra la conexión en `finally`.
- Diseños y planes detallados en `docs/superpowers/specs/` y `.../plans/`
  (`2026-06-21-registrar-gasto-confirmacion`,
  `2026-06-22-acciones-gasto-mecanismo-generico`).

### Próximas acciones (roadmap)

Reutilizarán este mismo mecanismo: **conciliar pagos del banco** desde el chat
y **desmarcar un pago** (volver `fecha_pago` a NULL).
