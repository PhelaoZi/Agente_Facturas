# Gerente comercial — Salud de clientes y lista de seguimiento — Diseño

**Fecha:** 2026-06-24
**Estado:** Aprobado por el usuario (diseño), pendiente de plan de implementación.

## Visión y descomposición

El objetivo de largo plazo del proyecto es un agente que actúe como **gerente
comercial**: con visión completa del negocio, capaz de razonar, recomendar y
proponer acciones, no solo responder datos. Esa visión es demasiado grande para
un solo spec, así que se descompone en dos ejes:

- **Forma de actuar:** primero *pull* (el chat razona como gerente cuando se le
  pregunta), después *push* (toma la iniciativa: avisa y recomienda sin que le
  pregunten). Empezamos por **pull**.
- **Áreas de expertise**, en orden de prioridad elegido por el usuario:
  1. **Crecimiento y clientes** ← este spec.
  2. Flujo y liquidez.
  3. Cobranza y riesgo.
  4. Precios y márgenes.

Cada combinación área × forma será su propio spec → plan → implementación. **Este
documento cubre solo la primera pieza: "Crecimiento y clientes" en modo pull.**

## Objetivo de este sub-proyecto

Que el chat del dashboard, ante preguntas de crecimiento/clientes ("¿cómo vamos
con los clientes?", "¿a quién debería contactar?", "¿quién se está enfriando?"),
**diagnostique la salud de la cartera** (quién se enfría, quién se durmió, quién
no recompró) y **proponga una lista de seguimiento persistente** que el usuario
confirma y va marcando. Es el primer paso del gerente comercial: pasar de
"responde el dato" a "detecta, prioriza y propone acción".

## Problema que resuelve

Hoy el chat sabe de deuda y ventas, pero no razona sobre la *salud* de cada
cliente: no detecta que un cliente grande bajó su consumo, que otro alargó su
frecuencia de compra, o que uno nuevo no recompró. El brief diario solo lista
"clientes inactivos > 60 días". Falta (a) la capa analítica que calcula y
prioriza estas señales y (b) un lugar operativo donde anotar y seguir las
gestiones, distinto de la wiki (que es *conocimiento*, no una *lista de tareas
con estado*).

## Decisión de arquitectura (Opción A, aprobada)

Reutilizar exactamente los patrones existentes del proyecto:

- **Capa de datos** estilo `app/briefing/data.py` y `app/negocio/*`: funciones
  puras que reciben un cursor `RealDictCursor` y devuelven estructuras Python,
  testeables con cursor falso. Reglas canónicas: `tipo_documento != 61`,
  `COALESCE(monto_total_ajustado, monto_total)`, excluir `estado = 'incobrable'`.
- **Escrituras** por el mecanismo propose/confirm/execute ya probado para gastos
  (`app/negocio/acciones.py` + `app/agent/tools_acciones.py` + endpoint
  `/api/ejecutar-accion`). **El agente nunca escribe**: solo publica una tarjeta
  `Artifact(tipo="accion")` que el usuario confirma. No se cambia
  `permission_mode` ni se toca el endpoint genérico ni la tarjeta del frontend.

No se introduce tecnología nueva. La única lógica realmente nueva es la capa de
análisis de salud de clientes.

## Componentes y estructura de archivos

| Archivo | Responsabilidad | Nuevo/Modificado |
|---|---|---|
| `app/negocio/clientes.py` | **Análisis** de salud de clientes (solo lectura/cálculo). `salud_clientes(cur)`. | Nuevo |
| `app/negocio/seguimiento.py` | **CRM** de la lista: `validar_*`, `listar`, `obtener`, `agregar`, `marcar`. Espejo de `gastos.py`. | Nuevo |
| `scripts/migrate_seguimiento_comercial.py` | Crea la tabla `seguimiento_comercial` (idempotente). | Nuevo |
| `app/negocio/acciones.py` | Registra `agregar_seguimiento` y `marcar_seguimiento` en `ACCIONES`. | Modificado |
| `app/agent/tools_negocio.py` | Tools de lectura `clientes_en_riesgo`, `listar_seguimiento`. | Modificado |
| `app/agent/tools_acciones.py` | Tools `proponer_agregar_seguimiento`, `proponer_marcar_seguimiento`. | Modificado |
| `app/agent/system_prompt.py` | Bloque "gerente comercial" para preguntas de crecimiento/clientes. | Modificado |
| `app/agent/orchestrator.py` | Registra las 4 tools nuevas en `allowed_tools`. | Modificado |
| `tests/test_negocio_clientes.py` | Tests de `salud_clientes` con cursor falso. | Nuevo |
| `tests/test_negocio_seguimiento.py` | Tests del CRM con cursor falso (espejo de gastos). | Nuevo |
| Tests de `acciones`/tools | Extender para cubrir las 2 acciones y 4 tools nuevas. | Modificado |

**Separación de responsabilidades:** `clientes.py` (análisis) y `seguimiento.py`
(almacén operativo) se mantienen en archivos distintos para que ninguno crezca
demasiado: uno detecta, el otro persiste.

## Capa de análisis — `app/negocio/clientes.py`

`salud_clientes(cur)` devuelve, por cada cliente que dispare al menos una señal,
un dict con las señales activadas, su prioridad y un `motivo` legible. Calcula
con SQL agregada por cliente y clasifica en Python (mismo estilo que
`resumen_cobranza`), para que sea testeable con cursor falso. Solo facturas
(`tipo_documento != 61`), montos ajustados, excluye `incobrable`.

**Las 4 señales (umbrales como constantes con nombre, ajustables):**

| Señal | Definición | Umbral default |
|---|---|---|
| `dormido` | Días desde la última compra | > **60 días** (igual que el brief) |
| `caida_consumo` | Venta de los últimos 60 días vs los 60 anteriores | caída > **40%** |
| `bajo_frecuencia` | Brecha entre las 2 últimas compras vs la brecha histórica promedio | > **1.5×** |
| `nuevo_sin_recompra` | 1 sola factura y sin volver | entre **21 y 60 días** desde esa compra |

Notas de cálculo:
- Brecha histórica promedio = `(ultima_venta − primera_venta) / (n_facturas − 1)`,
  definida solo con `n_facturas ≥ 3`. La brecha reciente es la distancia entre
  las dos últimas compras.
- `caida_consumo` requiere historia suficiente en la ventana previa de 60 días
  (si la ventana previa es 0, no se evalúa la caída para evitar falsos positivos).
- Un cliente `dormido` no se evalúa además como `bajo_frecuencia`/`caida_consumo`
  (ya dejó de comprar; la señal relevante es que se durmió).

**Prioridad:** `alta` si el cliente está entre el **top 10 histórico** por
facturación (`total_historico`); `media` el resto. Un cliente grande que se
enfría salta primero.

**Salida por cliente** (lista ordenada por prioridad y luego `total_historico`):
```
{rut, cliente, senales: [...], prioridad, motivo,
 dias_desde_ultima, ultima_venta, total_historico, n_facturas}
```
`motivo` es la frase que llena la tarjeta de seguimiento, p. ej.
*"Cliente grande enfriándose: -52% de consumo vs sus 2 meses previos"*.

**Lo que NO hace:** no escribe, no decide a quién contactar; solo detecta y
prioriza. La decisión de meter a alguien a la lista la confirma el usuario.

## Tabla `seguimiento_comercial` (mini-CRM)

Migración idempotente `scripts/migrate_seguimiento_comercial.py` (patrón de las
otras migraciones):

```sql
CREATE TABLE IF NOT EXISTS seguimiento_comercial (
  id              SERIAL PRIMARY KEY,
  rut_cliente     TEXT NOT NULL,
  motivo          TEXT NOT NULL,
  prioridad       TEXT NOT NULL DEFAULT 'media',     -- 'alta' | 'media'
  estado          TEXT NOT NULL DEFAULT 'pendiente', -- 'pendiente' | 'contactado' | 'descartado'
  senales         TEXT,                              -- traza de qué señales lo originaron
  fecha_creacion  DATE NOT NULL DEFAULT CURRENT_DATE,
  fecha_objetivo  DATE,                              -- cuándo contactar (opcional)
  fecha_contacto  DATE,                              -- cuándo se marcó contactado
  notas           TEXT
);
```

`rut_cliente` se une a `clientes` para mostrar la razón social (sin FK dura,
como las otras tablas operativas del proyecto). **Guard contra duplicados:** al
agregar, si el cliente ya tiene una entrada con `estado = 'pendiente'`, no se
crea otra; se avisa. La lista no se llena de repetidos del mismo cliente.

## CRM — `app/negocio/seguimiento.py`

Espejo de `gastos.py` (funciones puras que reciben cursor; el commit lo maneja
quien llama):

- `validar_agregar(params) -> dict limpio` — exige `rut_cliente` y `motivo`;
  normaliza `prioridad` a `{alta, media}`, `senales` a texto, fechas opcionales
  a `YYYY-MM-DD`. Lanza `ValueError` si falta lo obligatorio.
- `agregar(cur, ...) -> {id, mensaje}` — verifica el guard de duplicado
  (consulta si hay `pendiente` para ese rut; si lo hay, lanza `ValueError`
  explicativo) e inserta. Devuelve el id nuevo.
- `validar_marcar(params) -> dict limpio` — exige `id` entero > 0 y
  `estado ∈ {contactado, descartado}`; `fecha_contacto` por defecto hoy.
- `marcar(cur, id, estado, fecha_contacto) -> {id, mensaje}` — UPDATE de estado
  + fecha; lanza `ValueError` si el id no existe.
- `obtener(cur, id) -> dict | None` y `listar(cur, estado='pendiente') -> [dict]`
  (join a `clientes` para la razón social), para mostrar la lista con ids.

## Acciones de escritura (propose/confirm/execute)

En `app/negocio/acciones.py`, agregar al registro `ACCIONES`:

```
"agregar_seguimiento": (seguimiento.validar_agregar, _ejecutar_agregar_seguimiento)
"marcar_seguimiento":  (seguimiento.validar_marcar,  _ejecutar_marcar_seguimiento)
```

En `app/agent/tools_acciones.py`, dos tools nuevas que **proponen** (publican
`Artifact(tipo="accion")`, no escriben):

- `proponer_agregar_seguimiento(rut_cliente, cliente, motivo, prioridad, senales)`
  — pre-llenable desde el análisis. Resumen de la tarjeta:
  *"Seguimiento: {cliente} · {motivo} · prioridad {prioridad}"*.
- `proponer_marcar_seguimiento(id, estado)` — usa `listar_seguimiento` primero
  para ubicar el id. Resumen: *"Marcar {cliente} como {estado}"*.

El endpoint `/api/ejecutar-accion` y la tarjeta genérica del frontend **no
cambian** (postean `{tipo_accion, params}`). Misma disciplina que gastos: las
tools devuelven texto que recuerda al agente NO afirmar que quedó guardado hasta
que el usuario confirme.

## Tools de lectura (`mcp__negocio__*`)

En `app/agent/tools_negocio.py` (cada tool abre su propia conexión con el helper
existente, formatea Markdown breve y lo devuelve):

| Tool | Qué responde | Fuente |
|---|---|---|
| `clientes_en_riesgo` | Lista priorizada de clientes con señales activas | `clientes.salud_clientes` |
| `listar_seguimiento` | Lista de seguimiento actual con ids y estado | `seguimiento.listar` |

## System prompt — bloque "gerente comercial"

En `app/agent/system_prompt.py` se agrega el rol para preguntas de
crecimiento/clientes:

- Usar **siempre** `clientes_en_riesgo` (nunca SQL crudo) para diagnosticar la
  salud de la cartera.
- Responder como gerente: **primero un diagnóstico priorizado** (quién se enfría,
  quién se durmió, quién no recompró; los grandes primero), conciso y accionable;
  opcionalmente publicar una tabla en el lienzo.
- **Luego proponer** meter a los más críticos a la lista con
  `proponer_agregar_seguimiento` (tarjetas que el usuario confirma).
- Para ver/gestionar la lista: `listar_seguimiento` y
  `proponer_marcar_seguimiento`.
- Nunca afirmar que algo quedó guardado hasta que el usuario confirme.

En `app/agent/orchestrator.py`, registrar las 4 tools nuevas en `allowed_tools`
(2 de lectura en el server "negocio", 2 de acción en el server "acciones").

## Flujo de datos

```
Usuario: "¿a quién debería contactar?"
  → orchestrator → agente llama clientes_en_riesgo
    → salud_clientes(cur) → diagnóstico priorizado (texto + tabla en el lienzo)
  → agente propone agregar los críticos: proponer_agregar_seguimiento (tarjetas)
    → usuario confirma → POST /api/ejecutar-accion {tipo_accion, params}
      → acciones.validar → seguimiento.validar_agregar
      → acciones.ejecutar → seguimiento.agregar (INSERT) → 200
  Más tarde:
  → "muéstrame la lista" → listar_seguimiento
  → "marca a X como contactado" → proponer_marcar_seguimiento → confirmar → seguimiento.marcar
```

## Relación con la wiki y el brief

- La **wiki** sigue siendo el *conocimiento* del cliente (ficha narrativa,
  patrón de pago). El seguimiento es la *lista operativa* de gestiones. Son
  complementarios; no se duplican.
- El **brief diario** ya detecta "inactivos > 60 días". `salud_clientes` lo
  supera (4 señales y priorización), pero este spec **no** modifica el brief;
  integrar el análisis al brief (forma *push*) es un sub-proyecto posterior.

## Pruebas y verificación

- `tests/test_negocio_clientes.py` — con cursor falso: cada señal dispara cuando
  corresponde y no cuando no; prioridad `alta` solo para top 10; orden de salida;
  casos borde (cliente con 1 factura, sin ventana previa, dormido no marcado como
  baja-frecuencia).
- `tests/test_negocio_seguimiento.py` — validar/agregar/marcar con cursor falso,
  incluido el guard de duplicado `pendiente` (espejo de los tests de gastos).
- Extender el test del registro de `acciones.py` (las 2 acciones nuevas
  validan/ejecutan) y el test de nombres de tools (las 4 nuevas se registran).
- Migración: correrla dos veces (idempotente, no falla la segunda vez).
- **Integración real:** preguntar al chat "¿a quién debería contactar?",
  confirmar una tarjeta, verificar la fila en `seguimiento_comercial`, marcarla
  como contactada y verificar el cambio de estado.
- Suite completa (`python -m pytest -q`) en verde, sin romper los tests
  existentes.

## Seguridad

- El análisis es solo lectura (`SELECT`).
- Toda escritura pasa por el endpoint determinista con confirmación previa del
  usuario; el agente solo propone. No se modifica `permission_mode`.
- `seguimiento_comercial` es una tabla operativa de bajo riesgo (no toca datos
  financieros ni el estado de cobro).

## Fuera de alcance (sub-proyectos posteriores)

- Forma **push**: que el gerente levante estas señales por iniciativa propia
  (integrarlas al brief diario o a notificaciones).
- Las otras áreas: flujo y liquidez, cobranza y riesgo, precios y márgenes.
- Acciones de cobranza desde el chat (marcar factura de venta pagada, conciliar
  banco) — roadmap ya documentado en CLAUDE.md, mecanismo compartido.
- Registrar el resultado de cada gestión más allá de `estado` + `notas` (un CRM
  con historial de interacciones es otra capa).
