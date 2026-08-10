# Especificación para auditoría externa — Reparación de las líneas de logística

**Fecha:** 2026-08-10
**Autor:** Claude Opus 5 (Claude Code)
**Destinatario:** ChatGPT 5.6, como auditor externo
**Estado:** propuesta NO implementada. Nada se ha tocado en la base de datos.

---

## 0. Qué se pide de esta auditoría

Se propone una **migración sobre la base de datos de producción** de un negocio
real. Antes de ejecutarla se busca una revisión crítica e independiente.

No se busca aprobación. Se busca que se ataque el diseño: supuestos no
verificados, modos de falla no considerados, y sobre todo **si la opción elegida
es la correcta** frente a las alternativas.

Este documento es autocontenido — el auditor no tiene acceso al repositorio. Todo
número viene de una consulta ejecutada contra la base real y está transcrito tal
cual salió.

---

## 1. Dónde encaja esto en el plan acordado

En el intercambio previo (especificación → contrainforme → revisión → réplica →
respuesta a preguntas abiertas) quedó acordado un marco de decisión de tres vías:

```
CORRECCIÓN   → se arregla al descubrirse, con test de regresión
PROTECCIÓN   → se implementa si cierra un modo de falla real, sin exigir ROI
OPTIMIZACIÓN → hipótesis → métrica → experimento → adoptar/descartar
```

**Esto es una CORRECCIÓN.** El sistema hoy responde mal una pregunta legítima de
negocio, con cifras que subestiman las ventas en ~67%.

Se descubrió ejecutando el paso 1 del roadmap acordado (telemetría), que ya está
implementado y en producción.

---

## 2. El defecto

### 2.1 Contexto de negocio imprescindible

Esta cervecería factura cada barril en **dos líneas separadas** dentro de la
misma factura, para optimizar carga tributaria (el impuesto a alcoholes de 20,5%
grava solo la cerveza, no el servicio):

| Línea | Descripción | Ejemplo |
|---|---|---|
| 1 | Producto | "Barril 30L Cream Ale" — $20.000 |
| 2 | Logística | "Logistica" — $35.370 |

**El precio real del barril es la suma: $55.370.** La "logística" no es un
servicio: es parte del precio de la cerveza, contabilizado aparte.

### 2.2 El defecto

El importador de facturas electrónicas descarta las líneas de logística al
guardar en la tabla `productos`:

```python
ITEMS_NO_CATALOGO = {"logistica"}
...
if (nombre or "").lower().strip() in ITEMS_NO_CATALOGO:
    continue                      # la línea NO se guarda
```

El monto sí sobrevive en la cabecera (`ventas.monto_neto` viene de `<MntNeto>`
del documento tributario), pero **el detalle desaparece**.

Ejemplo real, factura 4750:

```
Todas las líneas en la tabla `productos`:
   Barril 30L Cream Ale   cantidad=2   total=$40.000
   (no hay más)

Pero ventas.monto_neto = $110.740
$110.740 − $40.000 = $70.740 = 2 × $35.370   ← la logística exacta
```

### 2.3 Consecuencia observada

Un usuario preguntó: *"nómbrame los 5 mejores clientes que compran barril de 30L
Cream Ale durante todo el 2026"*.

El agente respondió con un ranking correcto en orden y en unidades, pero con
montos calculados desde `productos`:

| Cliente | Barriles | Reportado | Real |
|---|---:|---:|---:|
| A&C Servicios Gastronómicos | 80 | $1.200.064 | **$3.903.557** |
| Restaurante Marina | 61 | $1.220.000 | **$3.375.316** |
| Inversiones y Servicios | 57 | $855.041 | **$2.751.396** |
| VDT SPA | 11 | $220.000 | **$615.494** |
| Ubuntu Patagonia | 4 | $80.000 | **$221.480** |
| **Total** | | **$3.575.105** | **$10.867.242** |

Reportó el **33%** de la venta real. Y el orden del ranking por monto cambia:
A&C pasa a ser el primero.

---

## 3. Por qué esto NO se resuelve con una herramienta nueva

Este punto es central y fue una corrección al primer diagnóstico del autor.

El agente tiene dos caminos: 16 herramientas de negocio con reglas canónicas
embebidas, y **SQL de solo lectura para todo lo demás**. La primera reacción fue
proponer una herramienta nueva (`ranking_clientes_producto`). El dueño del
proyecto objetó, con razón: *"¿entonces hay que hacer una tool por cada pregunta
que se me ocurra?"*.

**Los tres caminos disponibles hacia "dinero por producto" están rotos:**

| Camino | Qué le pasa |
|---|---|
| Tabla `productos` | le falta la logística (no se importó) |
| Vista `v_ventas_producto` (local) | filtra `NOT ILIKE '%logist%'` a propósito |
| Vista `v_lineas_factura` (nube) | solo **etiqueta** las líneas presentes; no recupera las ausentes |

Los dos filtros son **correctos para su propósito original**: evitar que
"Logistica" aparezca como un producto del catálogo al preguntar *qué cerveza se
vende más*. Son incorrectos al preguntar *cuánta plata*.

### 3.1 Demostración de que el problema es el dato, no el SQL

Se ejecutó **exactamente la misma consulta** contra los datos actuales y contra
los datos reparados (logística reconstruida y repartida):

| Mismo SQL, sobre… | A&C | Marina | Inversiones |
|---|---:|---:|---:|
| `productos` (hoy) | $1.200.064 | $1.236.666 | $902.541 |
| datos reparados | **$3.696.378** | **$3.353.157** | **$2.748.400** |

El SQL improvisado por el agente **estaba bien escrito**. Leía una tabla
incompleta. Reparado el dato, la misma consulta ingenua acierta sola.

**Implicación de diseño:** una herramienta protege solo a quien pasa por ella.
Reparar el dato protege todos los caminos a la vez — el SQL del agente, las 16
herramientas, el dashboard, el chat móvil, y las consultas manuales.

---

## 4. Magnitud medida

Sobre las **824 facturas** históricas (excluidas notas de crédito):

| Caso | Facturas | Monto ausente |
|---|---:|---:|
| a) Ya cuadran (la logística sí quedó registrada, bajo nombre variante) | 47 | $0 |
| b) Falta logística, **un solo producto** → reparto trivial | 602 | $35.613.886 |
| c) Falta logística, **varios productos** → requiere regla de reparto | 175 | $20.590.037 |
| **Total ausente de `productos`** | | **$56.203.923** |

El caso (c), 21% de las facturas y 37% del monto, es el riesgo real del proyecto.

### 4.1 Evidencia de que el residual ES la logística

`ventas.monto_neto` viene de `<MntNeto>` del documento tributario. Si el
documento trajera descuentos globales, el residual los incluiría y no sería
logística pura. Se verificó empíricamente calculando el residual por unidad en
facturas de un solo producto tipo barril:

```
$35.370 por barril  ->  294 facturas
$35.000 por barril  ->   63 facturas
$50.000 por barril  ->   34 facturas
$32.836 por barril  ->   25 facturas
$65.000 por barril  ->   17 facturas
$52.000 por barril  ->   16 facturas
```

Son precios **redondos y repetidos**, coincidentes con los precios de logística
documentados del negocio ($35.370 para Cream/Scotch Ale, $50.000 para Stout).
No es ruido de descuentos. Además, **cero residuales negativos** en las facturas.

### 4.2 Complicación no resuelta: las notas de crédito

```
Notas de crédito (tipo 61): 52 en total, 52 con residual, monto −$8.394.007
```

Las NC se guardan con montos negativos y **también** perdieron sus líneas de
logística. Las NC actualizan `monto_neto_ajustado` de la factura referenciada, de
modo que reparar las facturas sin reparar las NC dejaría una inconsistencia
nueva. **Este punto está sin diseñar y es una pregunta explícita para el
auditor** (§8).

---

## 5. Opciones consideradas

### Opción A — Reconstruir las líneas ausentes en `productos` (recomendada)

Agregar una columna `tipo_linea` a `productos` (`producto` / `logistica` /
`envase_pet` / `co2`) e insertar las líneas de logística faltantes, calculadas
como el residual de cada factura.

- **A favor:** los datos quedan completos. Cualquier consulta futura acierta sin
  conocer la regla. Se elimina la trampa en vez de documentarla. Una sola verdad.
- **En contra:** modifica datos históricos de producción. Irreversible sin
  respaldo. Requiere que la regla de reparto sea correcta para el caso (c).
- **No requiere los XML originales** — que ya fueron borrados tras procesarse —
  porque el monto se recupera aritméticamente de la cabecera.

### Opción B — Una vista que atribuya la logística al vuelo

Vista local que reparte el residual entre las líneas de producto sin tocar datos.

- **A favor:** reversible, cero riesgo sobre los datos, se puede revertir
  borrando la vista.
- **En contra:** la lógica de reparto correcta ya existe en Python
  (`precios_venta.py`, 380 líneas: reparto por litro en barriles, por unidad en
  botellas, normalización de barriles parciales de 20/25L a equivalente de 30L,
  descarte de facturas que mezclan familias sin logística desglosada). Una vista
  SQL haría una versión más burda → **dos formas distintas de calcular lo mismo**,
  que es la clase de divergencia que este proyecto ya está pagando entre su
  runtime de escritorio y el de la nube.

### Opción C — Una herramienta nueva del agente

Descartada por lo argumentado en §3: no escala, y deja los otros caminos rotos.

**Recomendación: A**, por dejar una sola verdad. Pero la objeción de
irreversibilidad de B es legítima y es materia de esta auditoría.

---

## 6. Diseño propuesto (opción A)

### 6.1 Cambio de esquema

```sql
ALTER TABLE productos ADD COLUMN IF NOT EXISTS tipo_linea TEXT;
-- clasificación de las líneas YA existentes
UPDATE productos SET tipo_linea = CASE
    WHEN nombre_producto ILIKE '%logist%'              THEN 'logistica'
    WHEN nombre_producto ~* '^(barril(es)?\s+)?pet\y'  THEN 'envase_pet'
    WHEN nombre_producto ILIKE '%co2%'                 THEN 'co2'
    ELSE 'producto' END
WHERE tipo_linea IS NULL;
```

### 6.2 Reconstrucción de las líneas ausentes

Por cada documento con residual > 0, insertar una o más filas con
`tipo_linea = 'logistica'` y `origen = 'reconstruido'` (columna nueva, para que
el dato reconstruido nunca se confunda con el importado del SII).

**Regla de reparto:**

- **Caso (b), 602 facturas — un solo producto:** toda la logística va a ese
  producto. Sin ambigüedad.
- **Caso (c), 175 facturas — varios productos:** se usa
  `app/negocio/precios_venta.py`, la implementación ya probada del negocio
  (reparto por litro en barriles, por unidad en botellas). **No se reimplementa
  la regla en SQL.**
- **Facturas que `precios_venta.py` descarta** (mezclan familias sin logística
  desglosada): **no se reparan**, se reportan en un informe. Inventar un reparto
  ahí sería fabricar un dato de negocio.

### 6.3 Cierre del origen del defecto

Sin esto la migración se vuelve a desactualizar con la próxima importación:

- `parse_dte.py` deja de descartar las líneas de logística y las guarda con
  `tipo_linea = 'logistica'`.
- Los consumidores que hoy filtran por nombre (`NOT ILIKE '%logist%'`) pasan a
  filtrar por `tipo_linea = 'producto'`, que es explícito y no depende de cómo se
  escribió el nombre.

### 6.4 Alcance explícitamente excluido

- No se toca `ventas.monto_neto` ni `monto_total` ni sus versiones ajustadas.
  **La cabecera ya es correcta**; el defecto es solo del detalle.
- No se toca el runtime del agente ni sus herramientas.
- No se modifica ningún XML ni se re-importa nada.

---

## 7. Riesgos y controles

| Riesgo | Control propuesto |
|---|---|
| La migración corrompe datos históricos irrecuperables | Respaldo completo verificado ANTES; `scripts/backup_db.py` ya existe y corre a diario |
| El residual incluye algo que no es logística (descuentos globales) | Evidencia de §4.1; además la migración **rechaza** cualquier factura cuyo residual por unidad no calce con un precio de logística plausible, y la reporta |
| La regla de reparto del caso (c) atribuye mal | Se usa la implementación ya probada con tests, no una versión SQL nueva |
| Doble ejecución duplica las líneas | Idempotencia por `origen='reconstruido'`: se borran las reconstruidas antes de reinsertar |
| Se rompe una consulta existente que asumía que la logística no estaba | Auditoría previa de todos los consumidores de `productos` + suite de tests |
| Las NC quedan inconsistentes con las facturas | **Sin resolver — ver §8** |

---

## 8. Preguntas explícitas para el auditor

Formuladas para que puedan contestarse o refutarse con argumentos.

1. **¿A o B?** ¿Se justifica modificar datos históricos de producción para
   eliminar la trampa, o pesa más la reversibilidad de una vista aunque implique
   dos implementaciones del mismo cálculo? Nótese que este proyecto ya está
   pagando el costo de tener dos implementaciones divergentes del precio entre
   escritorio y nube.

2. **Notas de crédito.** 52 NC con −$8.394.007 de residual. Si se reparan las
   facturas y no las NC, ¿queda una inconsistencia peor que la actual? ¿Debe la
   migración cubrir ambas en una sola transacción, o hay un motivo para tratarlas
   distinto?

3. **El supuesto central.** Todo el diseño descansa en
   `residual = MntNeto − Σ(líneas guardadas) = logística`. La evidencia de §4.1
   es empírica, no una prueba. ¿Qué otro elemento del documento tributario
   chileno podría estar contaminando ese residual y no se está considerando?

4. **Verificación.** ¿Qué prueba convencería de que la migración fue correcta,
   además de "las sumas cuadran"? En particular para el caso (c), donde el
   reparto entre productos no es verificable contra la cabecera (cualquier
   reparto suma lo mismo).

5. **Las 47 facturas que ya cuadran.** Tienen su logística registrada bajo
   nombres variantes ("Logistica Cream Ale"). ¿Deben normalizarse al mismo
   `tipo_linea` y quedar como están, o hay riesgo de doble conteo si la migración
   no las distingue bien?

6. **Orden.** ¿Corresponde hacer esta corrección ahora, antes de los pasos 2 y 3
   del roadmap acordado (tope de gasto y failover de proveedor), o el marco de
   tres vías implica que protección y corrección compiten por prioridad y hay un
   criterio para ordenarlas?

---

## 9. Estado actual

Nada implementado. La base de datos no ha sido modificada. Existe respaldo
diario automático.

Lo que sí está en producción, del roadmap acordado:

- **Paso 1 — telemetría:** implementado. Una fila por llamada al modelo con
  tokens, `cached_tokens`, `reasoning_tokens`, proveedor real, latencia,
  `finish_reason`, herramientas pedidas y costo.
- Primer resultado: el caché de prefijo **sí funciona** (5.518 de 7.569 tokens
  cacheados, 73%), y detectó que el turno de cierre salta de proveedor y pierde
  el caché.
- Segundo resultado: detectó y permitió corregir que el presupuesto de salida
  del loop (1.500 tokens) era insuficiente — un modelo de razonamiento gastaba
  1.499 pensando y se quedaba sin margen para emitir la llamada a la herramienta.
- **Pendiente:** OpenRouter no devolvió el campo `cost` pese a solicitarlo con
  `usage: {include: true}`. El costo por tarea queda incompleto hasta resolverlo.
