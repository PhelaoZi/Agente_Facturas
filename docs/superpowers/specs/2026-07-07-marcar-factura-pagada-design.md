# Acción "marcar factura pagada" desde el chat — Diseño

**Fecha:** 2026-07-07
**Estado:** aprobado (roadmap ya definido en CLAUDE.md, sección "Próximas acciones")

## Objetivo

Que el usuario pueda decirle al agente del dashboard "márcame pagada la factura
4664 con fecha 3 de julio" y, tras confirmar en la tarjeta, quede registrado
`ventas.fecha_pago` en la base de datos. Es la primera acción de escritura
sobre `ventas` (hasta ahora solo `cuentas_por_pagar` y `seguimiento_comercial`).

## Mecanismo

Reutiliza sin cambios el patrón propose/confirm/execute existente:

```
Usuario pide → agente ubica el folio (deuda_cliente) → proponer_marcar_factura_pagada
             → Artifact accion {tipo_accion, params, resumen} → tarjeta [Confirmar]
             → POST /api/ejecutar-accion → validar() → ejecutar() → UPDATE ventas
```

Ni el endpoint `/api/ejecutar-accion` ni la tarjeta del frontend cambian
(son genéricos). El agente sigue sin poder escribir: solo propone.

## Componentes

### 1. `app/negocio/cobranza.py` (módulo nuevo)

`ventas.py` es de solo lectura por diseño; la escritura de cobranza va en
módulo aparte. Tres funciones con la interfaz uniforme del registro:

- `obtener_factura(cur, folio)` — SELECT de la factura (tipo != 61) con JOIN a
  clientes: folio, fecha, razón social, rut_cliente, total real
  (`COALESCE(monto_total_ajustado, monto_total)`) y `fecha_pago`. `None` si no
  existe.
- `validar_marcar_pagada(params)` — pura: folio entero > 0; `fecha_pago`
  opcional (default hoy), formato YYYY-MM-DD, **rechaza fechas futuras**
  (un pago no puede ocurrir mañana). Devuelve `{folio, fecha_pago}`.
- `marcar_factura_pagada(cur, folio, fecha_pago)` — relee la factura con el
  mismo cursor y lanza `ValueError` si no existe o **si ya está pagada** (con
  la fecha existente en el mensaje, para no pisar un pago registrado por
  conciliación bancaria). Luego `UPDATE ventas SET fecha_pago = %s WHERE
  folio = %s AND tipo_documento != 61`. Devuelve `{folio, cliente, total,
  mensaje}`.

### 2. Registro en `app/negocio/acciones.py`

Una fila nueva: `"marcar_factura_pagada": (cobranza.validar_marcar_pagada,
_ejecutar_marcar_factura_pagada)`.

### 3. Tool `proponer_marcar_factura_pagada` en `app/agent/tools_acciones.py`

Schema `{folio: int, fecha: str}`. Lee la factura con conexión propia de solo
lectura; si no existe o ya está pagada responde al agente con el motivo (sin
tarjeta). Si procede, publica la tarjeta con resumen tipo:
`Marcar pagada F.4664 · BOTILLERIA X · $69.990 · pago el 03/07/2026`.

### 4. System prompt (`app/agent/system_prompt.py`)

Sección nueva "ACCIONES DE COBRANZA": ubicar el folio con
`mcp__negocio__deuda_cliente` (ya devuelve folios pendientes), si hay varias
candidatas preguntar cuál, fecha opcional default hoy, y la regla de siempre:
nunca afirmar que ya quedó pagada hasta que el usuario confirme.

## Validaciones (resumen)

| Caso | Resultado |
|------|-----------|
| Folio no numérico o ≤ 0 | 400 / mensaje al agente |
| Fecha con formato malo | 400 |
| Fecha futura | 400 |
| Factura no existe (o es NC) | tool avisa / ejecutar lanza ValueError → 400 |
| Factura ya pagada | tool avisa con la fecha existente / ejecutar → 400 |

Se permite fecha de pago anterior a la fecha de la factura (prepagos existen).

## Tests

- `tests/test_negocio_cobranza.py` — FakeCursor (mismo patrón que gastos):
  validaciones puras, SQL parametrizado del UPDATE, errores no-existe/ya-pagada.
- `tests/test_negocio_acciones.py` — la acción está registrada y rutea.
- `tests/test_tools_acciones.py` — el artifact arma el payload correcto y la
  tool aparece en `build_acciones_server`.

## Fuera de alcance

- Desmarcar un pago (quitar `fecha_pago`).
- Conciliar movimientos del banco desde el chat (siguiente ítem del roadmap).
- `wiki_update` automático del cliente tras confirmar (el endpoint se mantiene
  simple; la wiki se refresca con los flujos existentes).
