# Chat de análisis confiable — Diseño (Fase 2a)

**Fecha:** 2026-06-20
**Estado:** Aprobado por el usuario (diseño), pendiente de plan de implementación.

## Objetivo

Hacer que el chat del dashboard responda con **números exactos** sobre deuda,
ventas, flujo de caja y costos, reutilizando la lógica de negocio ya probada del
proyecto, en vez de que el modelo improvise SQL cada vez. Es la primera mitad de
la "Fase 2" (la segunda —acciones de escritura con confirmación— es un proyecto
aparte, fuera de alcance aquí).

## Problema que resuelve

Hoy el agente (`app/agent/orchestrator.py`) tiene una sola herramienta de datos:
`mcp__postgres__query` (SQL crudo de solo lectura). Para preguntas con reglas
delicadas (deuda con `COALESCE(monto_total_ajustado, ...)`, exclusión de NC,
estado de pago por `fecha_pago`) o con lógica compleja (proyección de flujo de
caja: ~150 líneas en `scripts/flujo_caja.py`), el modelo puede equivocarse. El
propio CLAUDE.md documenta que dos instancias del agente dieron deudas
contradictorias. Las "calculadoras fijas" eliminan esa clase de error.

## Decisión de arquitectura (Opción 1, aprobada)

Agregar un **segundo servidor MCP in-process** ("negocio") con herramientas
curadas, **y mantener** `mcp__postgres__query` para preguntas ad-hoc. El system
prompt instruye: para deuda/ventas/flujo/costos usar SIEMPRE la herramienta
curada; el SQL crudo queda solo para lo que ninguna herramienta cubra.

Esto reutiliza exactamente el patrón que el agente ya usa para el lienzo
(`app/agent/publish_tools.py` → `build_lienzo_server` con `create_sdk_mcp_server`
y `@tool`). No introduce tecnología nueva.

**100% solo lectura.** Esta fase no escribe ni modifica nada en la BD y no cambia
el modelo de permisos. Cero riesgo sobre los datos.

## Estructura de archivos

| Archivo | Responsabilidad | Nuevo/Modificado |
|---|---|---|
| `app/briefing/data.py` | Deuda/cobranza (ya existe y probado). Se le agrega `deuda_cliente()`. | Modificado |
| `app/negocio/__init__.py` | Marca el paquete. | Nuevo |
| `app/negocio/ventas.py` | Funciones de ventas: ranking, total, periodo, cliente, producto. | Nuevo |
| `app/negocio/costos.py` | Costos por SKU + márgenes (reusa `vista_costo_sku` + precios confirmados). | Nuevo |
| `app/negocio/flujo.py` | `proyectar_flujo()` — lógica extraída de `scripts/flujo_caja.py`. | Nuevo |
| `scripts/flujo_caja.py` | Pasa a importar `proyectar_flujo()` y solo imprime. Misma salida de CLI. | Modificado (refactor) |
| `app/agent/tools_negocio.py` | Servidor MCP "negocio": envuelve las funciones de datos como `@tool`. | Nuevo |
| `app/agent/orchestrator.py` | Registra el servidor "negocio" y sus tools en `allowed_tools`. | Modificado |
| `app/agent/system_prompt.py` | Agrega la "regla de oro" (preferir herramientas curadas). | Modificado |
| `tests/test_negocio_ventas.py` | Tests de ventas con cursor falso. | Nuevo |
| `tests/test_negocio_costos.py` | Tests de costos/márgenes con cursor falso. | Nuevo |
| `tests/test_negocio_flujo.py` | Tests de la proyección de flujo con cursor falso. | Nuevo |
| `tests/test_briefing_data.py` | Test de `deuda_cliente`. | Modificado |
| `tests/test_tools_negocio.py` | Tests del servidor MCP (nombres de tools, registro). | Nuevo |

**Patrón de las funciones de datos:** igual que `app/briefing/data.py` — reciben
un cursor `RealDictCursor`, devuelven estructuras Python simples (dicts/listas),
y son testeables con un cursor falso (sin BD). Reglas canónicas del proyecto:
`COALESCE(monto_total_ajustado, monto_total)`, `tipo_documento != 61`,
`fecha_pago IS NULL` = pendiente, excluir `estado = 'incobrable'`.

## Herramientas expuestas al agente

Servidor MCP "negocio" (nombres `mcp__negocio__*`):

| Herramienta | Qué responde | Parámetros | Fuente de datos |
|---|---|---|---|
| `deuda_total` | Deuda total + desglose por antigüedad | — | `briefing.data.resumen_cobranza` |
| `deuda_cliente` | Deuda de un cliente (por nombre o RUT) | `nombre` | `briefing.data.deuda_cliente` (nuevo) |
| `ranking_deudores` | Top N deudores | `limite=5` | `briefing.data.top_deudores` |
| `facturas_vencidas` | Facturas pendientes > N días | `dias=30` | `briefing.data.facturas_vencidas` |
| `ventas_total` | Total vendido (global o por rango) | `desde=None, hasta=None` | `negocio.ventas.total` |
| `ranking_clientes` | Top N clientes por venta | `limite=10` | `negocio.ventas.ranking` |
| `ventas_cliente` | Ventas de un cliente | `nombre` | `negocio.ventas.por_cliente` |
| `ventas_producto` | Buscar ventas por producto | `nombre` | `negocio.ventas.por_producto` |
| `flujo_caja` | Proyección de caja a 4 semanas | `saldo_inicial=None` | `negocio.flujo.proyectar_flujo` |
| `costos_sku` | Costo unitario por SKU | `receta=None, sku=None` | `negocio.costos.costos_sku` |
| `margenes` | Margen por cerveza (precio venta − costo) | `receta=None` | `negocio.costos.margenes` |

Cada `@tool` abre su propia conexión (helper `_con_cursor(fn, *args)` que conecta
con `app.config.DB_URL` + `RealDictCursor`, ejecuta `fn(cur, ...)`, cierra),
formatea el resultado como texto/Markdown breve y lo devuelve al agente. El
agente decide si además lo publica en el lienzo (KPI/tabla/gráfico) con las
herramientas que ya tiene.

## Flujo de datos

```
Usuario escribe en el chat
  → dashboard.run_agent(pregunta) → orchestrator.run()
    → el agente (Claude Agent SDK) elige la herramienta curada adecuada
      → @tool abre cursor → función de datos (SQL probado) → estructura Python
      → @tool formatea texto y lo devuelve al agente
    → el agente redacta la respuesta y (opcional) publica artefactos en el lienzo
  → dashboard devuelve {texto, artefactos} al navegador
```

## La regla de oro (system_prompt.py)

Se agrega un bloque al system prompt indicando:
- Para deuda, cobranza, ventas, flujo de caja y costos: **usar siempre** la
  herramienta `mcp__negocio__*` correspondiente. No reconstruir esos cálculos con
  SQL crudo.
- `mcp__postgres__query` queda reservado para preguntas ad-hoc que ninguna
  herramienta cubra.
- Las herramientas ya respetan las reglas canónicas; no hay que repetirlas en el
  SQL.

## Refactor de flujo_caja.py

`scripts/flujo_caja.py` hoy mezcla cálculo e impresión dentro de `main()`. Se
extrae la lógica de proyección a `app/negocio/flujo.py`:

```
def proyectar_flujo(cur, saldo_inicial=None, semanas=4) -> dict
```

Devuelve un dict estructurado: `saldo_inicial`, `saldo_fecha`, `horizonte`, y una
lista de semanas con `{label, ingresos, egresos, saldo_acumulado, riesgo}`, más
detalles de ingresos/egresos proyectados. `scripts/flujo_caja.py` se reescribe
para llamar a `proyectar_flujo()` y mantener **idéntica** su salida de consola
(la tarea `/flujo-caja` y su uso manual no cambian). Se reutilizan las funciones
ya existentes (`obtener_saldo_banco`, `obtener_avg_dias_por_cliente`,
`obtener_facturas_pendientes`, `obtener_gastos_pendientes`) moviéndolas o
importándolas desde el nuevo módulo.

## Costos y márgenes

- **Costo:** `costos_sku()` consulta `vista_costo_sku`
  (`codigo, nombre_cerveza, formato, costo_liquido_unitario,
  costo_envasado_unitario, costo_total_unitario`) — mismo SQL que
  `scripts/costo_sku.py`.
- **Margen:** `margenes()` cruza `costo_total_unitario` con los precios de venta
  netos confirmados. Esos precios ya existen en `app/dashboard.py` como
  `PRECIOS_VENTA_NETO` (cream ale 55370, scotch ale 55370, stout café/cacao
  75000, paint it black 98000). Se define el mismo diccionario en
  `app/negocio/costos.py` (valores idénticos, tomados de CLAUDE.md). **No se toca
  `app/dashboard.py`** para no arriesgar el panel que ya funciona; la
  deduplicación de ese diccionario queda fuera de alcance (los valores son
  estables y están en CLAUDE.md).
- **Límite conocido:** los precios confirmados son por **barril 30L**. Para
  formatos de botella no hay precio de venta confirmado, así que `margenes()`
  reporta margen solo para barriles y deja la botella como "sin precio de venta
  cargado" (no inventa un margen). Se documenta en la respuesta de la herramienta.

## Pruebas y verificación

- Tests unitarios por módulo de datos con cursor falso (patrón de
  `tests/test_briefing_data.py`): ventas, costos/márgenes, flujo, `deuda_cliente`.
- Test del servidor MCP: que `build_negocio_server` registre los nombres de tools
  esperados (patrón de `tests/test_orchestrator.py`).
- Verificación de integración (corrida real): preguntarle la deuda total al chat
  y confirmar que coincide con `/consultar-ventas → pendientes` y con el brief
  diario. Idem una proyección de flujo vs. `python scripts/flujo_caja.py`.
- Toda la suite (`python -m pytest -q`) debe quedar en verde, sin romper los 53+
  tests existentes.

## Seguridad

- Sin escrituras, sin `INSERT/UPDATE/DELETE`. Las funciones de datos solo hacen
  `SELECT`.
- No se modifica `permission_mode` ni se agregan herramientas de escritura.
- El refactor de `flujo_caja.py` no cambia su comportamiento observable.

## Fuera de alcance (Fase 2b, proyecto aparte)

- Herramientas de **escritura** con confirmación (registrar gasto, marcar pago,
  conciliar). Requieren diseñar el modelo de confirmación (el chat hoy no tiene
  memoria entre mensajes) y revisar `bypassPermissions`.
- Narración con LLM del brief diario.
- Deduplicar lógica entre `dashboard.py` (sus `q_*`) y la nueva capa `negocio/`
  más allá de `PRECIOS_VENTA_NETO`.
