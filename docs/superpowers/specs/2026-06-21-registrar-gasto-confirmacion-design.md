# Registrar gasto con confirmación — Diseño (Fase 2b, acción 1)

**Fecha:** 2026-06-21
**Estado:** Aprobado por el usuario (diseño), pendiente de plan de implementación.

## Objetivo

Permitir que el chat del dashboard **registre un gasto** (`cuentas_por_pagar`)
a partir de lenguaje natural, pero **sin que el agente escriba nunca en la BD**:
el agente solo *propone*, el usuario *confirma con un botón*, y un paso
determinista *ejecuta* la escritura. Es la primera acción de escritura de la
Fase 2b; las demás (marcar pago, conciliar) reutilizarán este mismo mecanismo.

## Decisión de arquitectura (aprobada — "propose / confirm / execute")

El chat es **stateless** (sin memoria entre mensajes) y el dashboard funciona
por request/response. Por eso NO se usa confirmación conversacional ("escribe
sí") ni escritura directa del agente. En cambio:

1. El agente llama a una herramienta **`proponer_gasto`** que NO escribe: publica
   un artefacto de tipo `accion` (igual que publica KPIs/tablas en el lienzo).
2. El frontend dibuja ese artefacto como una **tarjeta de confirmación** con los
   datos del gasto y botones **Confirmar / Cancelar**.
3. Al **Confirmar**, el navegador hace `POST /api/registrar-gasto` con los
   parámetros exactos; un handler **determinista** valida y ejecuta el `INSERT`.

**Consecuencia de seguridad clave:** el agente sigue **sin poder escribir** en la
BD (su única herramienta nueva solo arma un artefacto). El único camino que
escribe es el endpoint determinista, disparado por el botón. Por eso **no se
cambia `permission_mode`** ni se le da acceso de escritura al agente.

## Estructura de archivos

| Archivo | Responsabilidad | Nuevo/Modificado |
|---|---|---|
| `app/negocio/gastos.py` | `validar_gasto()` (puro) + `registrar_gasto(cur, ...)` (INSERT). | Nuevo |
| `app/agent/tools_acciones.py` | Servidor MCP "acciones": `proponer_gasto` publica un artefacto `accion`. | Nuevo |
| `app/agent/orchestrator.py` | Registra el servidor "acciones" (con collector) + allowed_tools. | Modificado |
| `app/agent/system_prompt.py` | Regla: para gastos, proponer; nunca afirmar que se registró. | Modificado |
| `app/dashboard.py` | Nuevo route `POST /api/registrar-gasto` (valida + escribe). | Modificado |
| `app/dashboard_ui.html` | `renderArtefactos`: nuevo caso `tipo === 'accion'` (tarjeta + botones). | Modificado |
| `tests/test_negocio_gastos.py` | Tests de validación y de registro (cursor falso). | Nuevo |
| `tests/test_tools_acciones.py` | Test: `proponer_gasto` registrado y publica artefacto `accion`. | Nuevo |
| `tests/test_orchestrator.py` | Test: `proponer_gasto` en allowed_tools. | Modificado |
| `tests/test_system_prompt.py` | Test: el prompt menciona la regla de gastos. | Modificado |

## Componentes

### `app/negocio/gastos.py` (la lógica determinista)
- `validar_gasto(descripcion, monto, fecha, proveedor=None, categoria=None) -> dict`:
  función pura que valida y normaliza. Reglas: `descripcion` no vacía; `monto`
  convertible a float y > 0 (acepta "185.000" / "185000"); `fecha` parseable a
  `YYYY-MM-DD`. Devuelve un dict con los valores limpios o lanza `ValueError` con
  un mensaje claro. Es el **gatekeeper** y se testea de forma aislada.
- `registrar_gasto(cur, descripcion, monto, fecha, proveedor, categoria) -> int`:
  ejecuta el `INSERT INTO cuentas_por_pagar (...) RETURNING id` (mismo SQL que
  `agregar_gasto.py`) y devuelve el `id` nuevo. Recibe un cursor (el endpoint
  maneja conexión y commit). Testeable con cursor falso.

### `app/agent/tools_acciones.py` (la propuesta)
- `build_acciones_server(collector)` (patrón de `build_lienzo_server`): define
  `@tool("proponer_gasto", ...)` con params `{descripcion, monto, fecha,
  proveedor, categoria}`. La herramienta **no escribe**: arma y publica un
  `Artifact(tipo="accion", titulo="Confirmar gasto", payload={...})` en el
  collector, y devuelve al agente un texto tipo "Propuesta de gasto lista para
  confirmar" (para que el agente NO afirme que se registró).
- Payload del artefacto:
  ```json
  {
    "tipo_accion": "registrar_gasto",
    "params": {"descripcion": "...", "monto": 185000, "fecha": "2026-06-30",
               "proveedor": "...", "categoria": "..."},
    "resumen": "Gasto: Luz · $185.000 · vence 30/06/2026"
  }
  ```
- No se modifica `app/canvas/artifacts.py`: `accion` es solo un nuevo valor de
  `tipo`; `Artifact` ya tiene `tipo/titulo/payload` y el `Collector` ya los junta.

### `app/dashboard.py` (el ejecutor determinista)
- Nuevo branch en `do_POST` (ya enruta por `path`): `path == "/api/registrar-gasto"`.
  Lee el JSON, llama a `gastos.validar_gasto(...)`; si falla, responde
  `400 {ok:false, error}`. Si valida, abre conexión, `gastos.registrar_gasto(...)`,
  hace commit y responde `200 {ok:true, id, mensaje}`. Maneja excepciones de BD
  devolviendo `500 {ok:false, error}` (nunca finge éxito).

### `app/dashboard_ui.html` (la tarjeta)
- En `renderArtefactos(cont, arts)` (que ya distingue `kpi/tabla/grafico/informe`),
  agregar `else if (a.tipo === 'accion')`: dibuja una tarjeta con `payload.resumen`
  y los campos, más botones **Confirmar** y **Cancelar**. Confirmar hace
  `fetch('/api/registrar-gasto', {method:'POST', body: JSON.stringify(payload.params)})`,
  y al recibir `ok:true` muestra "Registrado (id N)" y deshabilita los botones;
  ante error muestra el mensaje. Cancelar descarta la tarjeta.

### `app/agent/system_prompt.py` (la regla)
- Bloque nuevo: cuando el usuario pida registrar/anotar un gasto, usar
  `proponer_gasto` con los datos que dé (pedir los que falten: descripción,
  monto, fecha; proveedor y categoría son opcionales). **Nunca** decir que el
  gasto quedó registrado: solo queda *propuesto* hasta que el usuario apriete
  Confirmar. Si faltan datos clave, pedirlos antes de proponer.

## Flujo de datos

```
Usuario: "anota un gasto de luz de 185 mil para el 30 de junio"
  → agente llama proponer_gasto(descripcion="Luz", monto=185000, fecha="2026-06-30")
    → publica Artifact(tipo="accion", payload={tipo_accion:"registrar_gasto", params, resumen})
  → run_agent devuelve {texto, artefactos:[... accion ...]}
  → frontend dibuja la tarjeta con botones Confirmar/Cancelar
  → [usuario aprieta Confirmar]
    → POST /api/registrar-gasto {params}
      → validar_gasto() → registrar_gasto(cur) → commit → {ok:true, id}
    → tarjeta muestra "Registrado (id N)"
  → el gasto ya aparece en flujo de caja (mcp__negocio__flujo_caja)
```

## Manejo de errores

- Validación falla (monto/fecha/descripcion) → `400` con mensaje; la tarjeta lo
  muestra; no se escribe nada.
- Error de BD en el INSERT → `500` con mensaje; la tarjeta lo muestra; el usuario
  puede reintentar.
- El agente nunca afirma éxito: su texto habla de "propuesta", el éxito lo reporta
  la tarjeta tras el POST.

## Pruebas

- `validar_gasto`: casos válidos e inválidos (monto no numérico, fecha mala,
  descripción vacía) — puro, sin BD.
- `registrar_gasto`: con cursor falso que devuelve un id; verifica el valor.
- `proponer_gasto`: con un `Collector`, verifica que publica un artefacto
  `tipo="accion"` con el payload correcto y que NO toca la BD.
- `orchestrator`: `proponer_gasto` queda en `allowed_tools`.
- `system_prompt`: el prompt contiene la regla de gastos.
- Integración (real): pedir al chat un gasto de prueba, confirmar vía endpoint,
  verificar que aparece en `cuentas_por_pagar` y en el flujo de caja, y borrarlo.

## Seguridad

- El agente NO escribe: su herramienta nueva solo publica un artefacto.
- Único camino de escritura: `POST /api/registrar-gasto`, determinista y validado.
- No se cambia `permission_mode`. El `INSERT` usa parámetros (sin SQL inyectable).

## Fuera de alcance (siguientes acciones de la Fase 2b)

- Marcar factura como pagada (`fecha_pago`) — reutiliza propose/confirm/execute.
- Conciliar pagos del banco.
- Editar o borrar gastos desde el chat (por ahora, solo alta).
