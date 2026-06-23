# Acciones de gasto desde el chat + mecanismo de acciones genérico — Diseño (Fase 2b, acción 2)

**Fecha:** 2026-06-22
**Estado:** Diseño aprobado por el usuario (a nivel conceptual), pendiente de plan de implementación.

## Objetivo

Permitir gestionar un gasto (`cuentas_por_pagar`) completo desde el chat del
dashboard: además de **registrar** (ya implementado en la acción 1), poder
**borrar**, **editar** y **marcar como pagado** — todo con el patrón
*propose / confirm / execute*, sin que el usuario tenga que abrir la terminal.

De paso, se **generaliza** el mecanismo de confirmación (hoy cableado solo a
"registrar gasto") en una "caja de confirmación" reutilizable, para que cada
acción de escritura futura (estas tres, y luego *marcar factura pagada* y
*conciliar*) sea un enchufe rápido en vez de una tubería nueva completa.

## Decisiones aprobadas

1. **Operaciones sobre gasto:** registrar (ya hecho), **borrar**, **editar**,
   **marcar pagado**.
2. **Borrar = borrado definitivo** (`DELETE`), no soft-delete/anulado. Mantiene
   simples todas las consultas (flujo de caja, por-pagar) sin filtros nuevos.
3. **Mecanismo genérico ahora:** un registro de acciones `tipo_accion →
   (validar, ejecutar)` + un único endpoint determinista + una tarjeta de
   frontend genérica. Cada acción nueva se enchufa ahí.
4. **Marcar factura de venta como pagada** (`ventas.fecha_pago`) queda **fuera
   de alcance** — es el diseño siguiente, y reutilizará este mismo mecanismo.

## Invariante de seguridad (igual que la acción 1)

El agente **nunca escribe** en la BD. Sus herramientas nuevas solo *leen*
(`listar_gastos`) y *proponen* (publican un artefacto `accion`). El único
camino de escritura es el endpoint determinista, disparado por el botón
**Confirmar**. **No se cambia `permission_mode`.** Toda escritura va
parametrizada (`%s`).

## Arquitectura — "registro de acciones" (propose / confirm / execute generalizado)

Hoy: `proponer_gasto` → artefacto `accion` con `payload.tipo_accion =
"registrar_gasto"` → la tarjeta hace `POST /api/registrar-gasto` (cableado).

Nuevo: la tarjeta hace `POST /api/ejecutar-accion` con `{tipo_accion, params}`.
El backend tiene un **registro** `ACCIONES = { tipo_accion: (validar, ejecutar) }`.
El endpoint busca el par por `tipo_accion`, valida (errores → 400) y ejecuta
(errores de BD → 500). Cada acción nueva = una fila en el registro + una
herramienta `proponer_X` para el agente. La tarjeta del frontend no vuelve a
cambiar.

```
Usuario: "borra el gasto de la contadora"
  → agente: listar_gastos()            (lectura) → ve id=5 "Contadora" $50.000
  → agente: proponer_borrar_gasto(5)   → obtiene el gasto 5 y publica
       Artifact(tipo="accion", payload={
         tipo_accion:"borrar_gasto", params:{id:5},
         resumen:"Borrar: Contadora · $50.000 · vence 30/06/2026"})
  → frontend dibuja la tarjeta (resumen + Confirmar/Cancelar)
  → [Confirmar] → POST /api/ejecutar-accion {tipo_accion:"borrar_gasto", params:{id:5}}
       → acciones.validar("borrar_gasto", {id:5}) → {id:5}
       → acciones.ejecutar(cur, "borrar_gasto", {id:5}) → DELETE → commit
       → {ok:true, id:5, mensaje:"Gasto borrado: Contadora"}
  → la tarjeta muestra "✓ Gasto borrado: Contadora"
```

## Estructura de archivos

| Archivo | Responsabilidad | Nuevo/Modificado |
|---|---|---|
| `app/negocio/gastos.py` | Lógica determinista por gasto: `obtener_gasto`, `listar`, validadores y ejecutores de borrar/editar/marcar-pagado (los de registrar ya existen). | Modificado |
| `app/negocio/acciones.py` | Registro `ACCIONES` + `validar(tipo, params)` y `ejecutar(cur, tipo, clean)`. Adaptadores de interfaz uniforme (incluido registrar). | Nuevo |
| `app/agent/tools_negocio.py` | Herramienta de lectura `listar_gastos`. | Modificado |
| `app/agent/tools_acciones.py` | Nuevas tools de propuesta `proponer_borrar_gasto`, `proponer_editar_gasto`, `proponer_marcar_gasto_pagado`. `proponer_gasto` se mantiene. | Modificado |
| `app/agent/orchestrator.py` | Sin cambios funcionales (las tools nuevas viven en servers ya registrados); solo crece la lista de `tool_names`. | Modificado |
| `app/agent/system_prompt.py` | Regla ampliada: listar/borrar/editar/marcar-pagado; siempre listar antes de actuar; nunca afirmar que la acción ya ocurrió. | Modificado |
| `app/dashboard.py` | Reemplaza el branch `/api/registrar-gasto` por `/api/ejecutar-accion` (genérico, vía `acciones`). | Modificado |
| `app/dashboard_ui.html` | La tarjeta `accion` hace `POST /api/ejecutar-accion` con `{tipo_accion, params}` en vez del endpoint cableado. | Modificado |
| Tests | `test_negocio_gastos`, `test_negocio_acciones` (nuevo), `test_tools_acciones`, `test_tools_negocio`, `test_system_prompt`. | Nuevos/Modificados |

## Componentes

### `app/negocio/gastos.py` (lógica determinista)

Funciones nuevas (todas con cursor `RealDictCursor`, sin manejar conexión/commit):

- `obtener_gasto(cur, id) -> dict | None`: `SELECT` del gasto por id. Lo usan
  las tools de propuesta para armar un resumen exacto y comprobar existencia.
- `listar(cur, filtro=None, incluir_pagados=False) -> list[dict]`: gastos
  ordenados por `fecha_vencimiento`, con `id, descripcion, monto,
  fecha_vencimiento, proveedor, categoria, pagado`. `filtro` hace
  `descripcion ILIKE %filtro%`. Por defecto excluye los ya pagados.
- **Borrar:**
  - `validar_borrar(params) -> {"id": int}`: `id` presente y entero > 0.
  - `borrar_gasto(cur, id) -> {"id", "mensaje", "descripcion"}`:
    `DELETE ... WHERE id=%s RETURNING descripcion`. Si no borró fila →
    `raise ValueError("El gasto N ya no existe.")`.
- **Editar:**
  - `validar_editar(params) -> {"id": int, "cambios": dict}`: `id` válido;
    al menos un campo de {`descripcion`, `monto`, `fecha`, `proveedor`,
    `categoria`}; normaliza `monto` (reusa `_normalizar_monto`) y `fecha`
    (`YYYY-MM-DD`) si vienen. Si no hay ningún campo → `ValueError`.
  - `editar_gasto(cur, id, cambios) -> {"id", "mensaje"}`: arma el `UPDATE
    SET` dinámico **parametrizado** solo con las columnas presentes
    (`fecha` mapea a `fecha_vencimiento`), `WHERE id=%s RETURNING descripcion`.
    Si no existe → `ValueError`.
- **Marcar pagado:**
  - `validar_marcar_pagado(params) -> {"id": int, "fecha_pago": str}`: `id`
    válido; `fecha_pago` por defecto **hoy** si no viene, si no se parsea
    `YYYY-MM-DD`.
  - `marcar_gasto_pagado(cur, id, fecha_pago) -> {"id", "mensaje"}`:
    `UPDATE cuentas_por_pagar SET pagado=TRUE, fecha_pago=%s WHERE id=%s
    RETURNING descripcion`. Si no existe → `ValueError`.

`validar_gasto` y `registrar_gasto` (registrar) **no cambian**.

### `app/negocio/acciones.py` (registro genérico)

Interfaz uniforme: cada validador toma un `params: dict` y devuelve un dict
limpio; cada ejecutor toma `(cur, clean)` y devuelve un dict resultado
`{mensaje, id?}`. Adaptadores envuelven las funciones de `gastos.py`
(registrar, que hoy tiene firma posicional, se adapta sin tocarse).

```python
ACCIONES = {
    "registrar_gasto":      (_validar_registrar, _ejecutar_registrar),
    "borrar_gasto":         (gastos.validar_borrar, _ejecutar_borrar),
    "editar_gasto":         (gastos.validar_editar, _ejecutar_editar),
    "marcar_gasto_pagado":  (gastos.validar_marcar_pagado, _ejecutar_marcar_pagado),
}

def validar(tipo_accion, params) -> dict:
    if tipo_accion not in ACCIONES:
        raise ValueError(f"Acción desconocida: {tipo_accion!r}")
    return ACCIONES[tipo_accion][0](params)

def ejecutar(cur, tipo_accion, clean) -> dict:
    return ACCIONES[tipo_accion][1](cur, clean)
```

Separar `validar` (sin BD) de `ejecutar` (con BD) permite al endpoint
distinguir 400 (validación) de 500 (BD) y no abrir conexión para input
inválido. Ambas son testeables con cursor falso.

### `app/agent/tools_negocio.py` (lectura)

- `listar_gastos` (`@tool`, server `negocio`): params `{filtro: str}` (opcional).
  Llama `gastos.listar(cur, filtro)`. Devuelve texto con `id`, descripción,
  monto y vencimiento de cada gasto pendiente — para que el agente ubique el id
  correcto y se lo muestre al usuario. Nombre MCP: `mcp__negocio__listar_gastos`.

### `app/agent/tools_acciones.py` (propuestas)

Tres tools nuevas en el server `acciones`. Cada una **lee** el gasto con
`obtener_gasto` para armar un resumen exacto; si el id no existe, devuelve
texto de error (sin publicar artefacto) para que el agente vuelva a listar.

- `proponer_borrar_gasto(id)` → artefacto `{tipo_accion:"borrar_gasto",
  params:{id}, resumen:"Borrar: <desc> · <monto> · vence <fecha>"}`.
- `proponer_editar_gasto(id, descripcion?, monto?, fecha?, proveedor?,
  categoria?)` → valida que haya al menos un campo; artefacto
  `{tipo_accion:"editar_gasto", params:{id, <campos>}, resumen:"Editar <desc>:
  <campo> <viejo> → <nuevo>"}` (muestra antes→después de los campos cambiados).
- `proponer_marcar_gasto_pagado(id, fecha?)` → artefacto
  `{tipo_accion:"marcar_gasto_pagado", params:{id, fecha_pago}, resumen:"Marcar
  pagado: <desc> · <monto> · el <fecha>"}`.

`proponer_gasto` (registrar) se mantiene; su artefacto ya usa
`tipo_accion:"registrar_gasto"`, así que pasa por el endpoint genérico sin
cambios.

### `app/dashboard.py` (ejecutor determinista genérico)

Reemplaza el branch `/api/registrar-gasto` por `/api/ejecutar-accion`:

1. Lee `{tipo_accion, params}` del JSON (parse error → 400).
2. `clean = acciones.validar(tipo_accion, params)` (ValueError → 400, sin abrir conexión).
3. `conn = get_conn()`; `try/finally conn.close()`; dentro: `with conn:` →
   `result = acciones.ejecutar(cur, tipo_accion, clean)`; commit.
   - `ValueError` durante ejecución (p. ej. "ya no existe") → 400.
   - Otra excepción (BD) → 500, sin fingir éxito.
4. Éxito → 200 `{ok:true, **result}`.

El branch viejo `/api/registrar-gasto` se elimina (solo lo usaba la tarjeta, que
ahora apunta al genérico; es código recién creado, bajo riesgo).

### `app/dashboard_ui.html` (tarjeta genérica)

La rama `a.tipo==='accion'` cambia solo el `fetch`: ahora
`POST /api/ejecutar-accion` con `body: JSON.stringify({tipo_accion: p.tipo_accion,
params: p.params})`. Mostrar resumen, botones, éxito/error: sin cambios.

### `app/agent/system_prompt.py` (regla ampliada)

Bloque que reemplaza/extiende la regla de gastos: el agente puede registrar,
**listar, borrar, editar y marcar pagado** gastos. Para borrar/editar/marcar:
primero usar `listar_gastos` para ubicar el gasto; si hay **varios que calzan**,
mostrarlos numerados y preguntar cuál antes de proponer; luego llamar a la tool
`proponer_*` correspondiente con el `id`. **Nunca** afirmar que la acción ya
ocurrió: solo queda *propuesta* hasta que el usuario apriete Confirmar.

## Flujo de datos (editar, ejemplo con ambigüedad)

```
Usuario: "cámbiale el monto al gasto de gas a 180 mil"
  → agente: listar_gastos("gas") → [id=4 Gas $200.000]
  → agente: proponer_editar_gasto(id=4, monto="180000")
       → obtener_gasto(4); artefacto {tipo_accion:"editar_gasto",
          params:{id:4, monto:180000.0},
          resumen:"Editar Gas: monto $200.000 → $180.000"}
  → tarjeta → [Confirmar] → POST /api/ejecutar-accion
       → validar("editar_gasto", {id:4, monto:180000.0}) → {id:4, cambios:{monto:180000.0}}
       → ejecutar → UPDATE ... SET monto=180000 WHERE id=4 → commit
       → {ok:true, id:4, mensaje:"Gasto actualizado: Gas"}
```

## Manejo de errores

- JSON inválido / `tipo_accion` desconocido / validación de params → **400**, mensaje claro, sin escribir.
- Gasto inexistente al borrar/editar/marcar (RETURNING vacío) → `ValueError` → **400** ("El gasto N ya no existe").
- Error de BD en el `UPDATE`/`DELETE` → **500**, mensaje, conexión cerrada en `finally`. Nunca `ok:true` si falló.
- Una tool de propuesta con id inexistente → texto de error al agente (no publica artefacto).

## Pruebas

- `gastos.py`: validadores puros (borrar/editar/marcar-pagado, casos válidos e
  inválidos) y ejecutores con cursor falso — verifican SQL destino, parámetros
  y orden, y que "no existe" lance `ValueError`. `listar`/`obtener_gasto` mapean
  filas. Reusa el `FakeCursor` existente (captura `sql` y `params`).
- `acciones.py`: `validar` con `tipo_accion` desconocido lanza `ValueError`;
  cada acción enruta a su validador/ejecutor; resultado uniforme `{mensaje,…}`.
- `tools_acciones.py`: cada `proponer_*` arma el artefacto `accion` con
  `tipo_accion`, `params` y `resumen` correctos (tests del builder puro, como
  la acción 1).
- `tools_negocio.py`: `mcp__negocio__listar_gastos` queda en la lista de tools.
- `system_prompt.py`: el prompt menciona las tools nuevas.
- Integración (en vivo, in-process como en la acción 1): por cada acción, POST
  válido → 200 y POST inválido/inexistente → 400; más una corrida real del
  agente (`run_agent`) que liste y proponga, verificando el artefacto. Limpiar
  cualquier fila de prueba.

## Seguridad

- El agente no escribe: sus tools nuevas solo leen (`listar_gastos`,
  `obtener_gasto`) o publican artefactos. Único camino de escritura:
  `/api/ejecutar-accion`, determinista y parametrizado.
- La tarjeta muestra **los datos exactos** del gasto afectado antes de
  confirmar — red de seguridad contra tocar el gasto equivocado (hay 6 gastos
  reales en la tabla).
- No se cambia `permission_mode`.

## Fuera de alcance

- **Marcar factura de venta como pagada** (`ventas.fecha_pago`) — diseño siguiente, reusa este mecanismo.
- Conciliar pagos del banco.
- Edición/gestión de la recurrencia (`recurrente`, `periodicidad`) de un gasto.
- Operaciones masivas (borrar/editar varios a la vez).
- Deshacer una acción ya confirmada (no hay "undo"; borrar es definitivo).
