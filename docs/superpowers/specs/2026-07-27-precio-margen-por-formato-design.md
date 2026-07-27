# Precio y margen por formato (botellas incluidas) — Diseño

**Fecha:** 2026-07-27
**Estado:** aprobado por el productor

## Problema

En el chat del dashboard, la pregunta *"¿cuál es el costo de la botella de Cream
y Scotch de 330cc? Quiero saber el margen"* termina en
`No alcancé a terminar la consulta (límite de pasos del agente)`.

La causa no es el modelo. `mcp__negocio__margenes` solo conoce precios de venta
de **barril 30L**, escritos a mano en `PRECIOS_VENTA_NETO`
(`app/negocio/costos.py`). Para cualquier otro formato devuelve *"sin precio de
venta confirmado"*. El agente entonces improvisa SQL sobre `productos` — la
tabla donde la estructura de doble línea lo engaña, porque la línea `Logistica`
exacta **ni siquiera está guardada ahí** (`ITEMS_NO_CATALOGO` en
`parse_dte.py`). Encadena consultas buscando un dato que no puede deducir así,
agota las 8 iteraciones y el orquestador **descarta todo el trabajo acumulado**.

Son dos fallas independientes y ambas hay que arreglarlas:

1. **De datos:** no existe precio de venta para formatos que no sean barril 30L.
2. **De robustez:** al agotar los pasos, el agente devuelve un mensaje vacío de
   contenido en vez de responder con lo que ya reunió.

## Reglas de negocio (confirmadas por el productor)

### Etiqueta

Toda etiqueta cuesta **$230 con IVA incluido** → **$193,28 neto**, una por
botella. Hoy no está en el BOM de envasado, así que el costo de la botella está
subestimado.

### La logística

La logística es aproximadamente **la mitad del precio** de venta y se factura en
línea aparte para reducir el ILA. El productor la escribe así:

- Si todos los ítems de la factura tienen **el mismo costo de logística**, pone
  **una sola línea** `Logistica`, sin nombrar nada.
- Si tienen **costos distintos**, la **desglosa**: `Logistica Scotch` con su
  precio y `Logistica Stout` con el suyo.

Verificado contra el histórico: cuando desglosa, la línea de logística lleva
**la misma `cantidad`** que la línea de producto a la que pertenece, lo que hace
la atribución exacta.

```
Folio 4694:  Barril RIS        x2 @35.000  +  Logistica RIS        x2 @60.000  → $95.000/barril
             Barril Cream Ale  x2 @20.000  +  Logistica Cream Ale  x2 @35.370  → $55.370/barril ✓
```

$55.370 es exactamente el precio confirmado de la Cream, deducido sin lista.

### El CO2 es un traspaso a costo, no una venta

Zigurat instala en algunos restaurantes una **schopera de su propiedad**, y el
cilindro de CO2 que empuja la cerveza también es suyo. Cuando se acaba, le
llevan una carga nueva, comprada en **Clean Ice** (aparece en las facturas de
compra). Al cliente se le cobra **exactamente lo que costó la carga**.

Es decir: el CO2 es un **pass-through sin margen, igual que el envase PET**. No
es un producto del catálogo, no es venta de cerveza y no debe aparecer en
rankings de producto. Son 6 líneas en todo el histórico ("9 kg CO2", "Carga
CO2", "Recarga CO2 9 kg", …).

### Los barriles de 20L y 25L no son otro formato

Todos los barriles son de 30L. Cuando los últimos litros del fermentador no
alcanzan a llenar uno, se despacha ese mismo barril de 30L con 20 o 25 litros
dentro y se factura como "Barril 25L". **Precio y logística escalan
proporcionalmente a los litros**, confirmado en el folio 4572:

```
Barril 30L Cream Ale   x2  $15.001  + logística $32.836  =  $47.837
Barril 30L Scotch Ale  x2  $15.000  + logística $32.836  =  $47.836
Barril 25L Cream Ale   x1  $12.500  + logística $27.363  =  $39.863 = 25/30 de $47.836 ✓
```

Los tres son el mismo precio con distinto contenido. Por eso el precio se
normaliza a **barril de 30L equivalente** (`precio × 30 / litros`) y queda una
sola serie por cerveza en vez de tres fragmentadas.

### Mano de obra — NO se toca

Los $300.000/lote se siguen repartiendo por litro. Esto hace que la botella
cargue lo mismo que el barril aunque embotellar dé más trabajo, así que el
margen de botella queda algo optimista. **Es un supuesto deliberado del
productor, no un error.** Fuera del alcance de este trabajo.

## Notas de crédito: funcionan bien

De las 812 facturas de venta, 52 tienen nota de crédito y **las 52 son
anulaciones totales** (`monto_neto_ajustado = 0`). **Parciales hay cero.** Los
campos ajustados descuentan correctamente y no hay doble conteo: el modelo de
datos vigente está sano.

La única consecuencia para este diseño es que una **factura anulada no sirve
como muestra de precio** — si la venta se deshizo, no dice a cuánto se vende. Se
excluyen y se cuentan aparte. La regla se escribe como "sin NC aplicada"
(`monto_neto_ajustado IS NULL`) en vez de "distinta de cero" para que una NC
parcial futura tampoco contamine el precio: rebajaría el neto sin tocar las
líneas de `productos`, y el residual saldría corto.

## Componentes

### 1. `scripts/migrate_etiqueta_botella.py` (nuevo)

Migración idempotente, mismo patrón que `migrate_costos_v3.py`:

- Alta del insumo `Etiqueta`, categoría `etiqueta`,
  `precio_neto_unitario = 193.28` ($230 ÷ 1,19).
- Una fila en `sku_envasado` (cantidad 1) para `CREAM-ALE-BOT-330-C12` y
  `SCOTCH-ALE-BOT-330-C12`.

Los barriles no llevan etiqueta y no se tocan. Efecto en `vista_costo_sku`:

| SKU | Costo antes | Costo después |
|-----|------------|---------------|
| Cream Ale 330cc | $697,94 | **$891,22** |
| Scotch Ale 330cc | $725,65 | **$918,93** |

### 2. `app/negocio/precios_venta.py` (módulo nuevo, solo lectura)

`precios_por_formato(cur, dias=None)` deduce el precio neto unitario real por
`(cerveza, formato)` desde las facturas. Módulo aparte porque `costos.py` habla
con la capa de costos (recetas, insumos, SKU) y esto habla con la capa de ventas
(`ventas` + `productos`); mezclarlos ataría dos dominios que hoy no se conocen.

**Facturas elegibles:** `tipo_documento != 61` y `monto_neto_ajustado IS NULL`
(sin NC aplicada, ver arriba).

**Clasificación de cada línea** (sobre el nombre normalizado — minúsculas, sin
tildes):

| Clase | Detección | Trato |
|-------|-----------|-------|
| logística | contiene `logist` | se atribuye (ver abajo) |
| envase | calza `^(barril(es)?\s+)?pet\b` | pass-through: se ignora |
| CO2 | contiene `co2` | pass-through: se ignora |
| cerveza | el resto | aporta precio |

**Formato y litros de una línea de cerveza:** la familia se detecta con
`barr?il` (tolera la errata "Baril"), `botella` o `lata`; la capacidad, con el
primer número seguido de `l`/`cc`/`ml`. De ahí salen los litros de la línea:
`Barril 25L` → 25 L/unidad, `Botella 330cc` → 0,33 L/unidad. Sin familia o sin
capacidad reconocible, esa **línea** no aporta precio, pero sus litros siguen
contando en la base de reparto del residual (la logística sí se pagó por ella).
La factura no se descarta por eso.

**Cerveza de una línea:** se busca como subcadena el `nombre_cerveza` de cada
receta, normalizado, sobre el nombre de la línea. La receta más larga que calza
gana (evita que "Stout" se coma a "Stout Café/Cacao").

**Atribución de la logística, en dos pasadas:**

1. **Nombrada.** De cada línea de logística se extrae un selector: la cerveza
   que nombra y/o la capacidad que nombra. Las líneas de cerveza que cumplen
   *todos* los criterios presentes son las candidatas. Si hay **exactamente
   una**, recibe el `total_linea` completo. Si hay cero o más de una, esa línea
   de logística se manda al residual.
   El selector por capacidad es necesario: el folio 4572 trae
   `Logistica Barril 25L`, que no nombra cerveza pero identifica sin ambigüedad
   al único barril de 25L de la factura.
2. **Residual.**
   `residual = (monto_neto − Σ total_linea de todas las líneas guardadas)
   + Σ total_linea de las logísticas no atribuidas`. El paréntesis es la línea
   `Logistica` exacta que `parse_dte` no guarda. Se reparte **proporcional a los
   litros** entre las líneas de cerveza que no recibieron logística nombrada —
   que es lo que el productor quiere decir al no desglosarla.
   Si esas líneas pertenecen a **más de una familia** (barriles y botellas
   juntos), es ambigua: se descarta la factura entera y se reporta. Hoy no
   ocurre en ninguna factura del histórico.

El reparto por litro, y no por unidad, es lo que hace que un barril parcial
reciba su logística correcta sin ningún caso especial.

**Precio unitario** = `(total_linea del producto + logística atribuida) / cantidad`,
y para barriles se **normaliza a 30L**: `× 30 / litros_por_unidad`.

**Salida**, una fila por `(cerveza, formato)` — donde formato es `barril 30L`
(normalizado) o `botella 330`:

```python
{"cerveza", "formato",
 "precio_ultimo", "fecha_ultimo", "folio_ultimo",
 "precio_promedio", "n_facturas", "n_descartadas"}
```

`precio_ultimo` es el precio vigente; `precio_promedio` revela los descuentos.
Los folios 4691 y 4572 vendieron Cream a $47.836 en vez de $55.370 — una lista
escrita a mano nunca lo habría mostrado.

**Validación obligatoria contra la BD real** (son los casos que fijaron el
diseño; si uno falla, el algoritmo está mal):

| Folio | Debe dar | Por qué |
|-------|----------|---------|
| 4694 | Cream $55.370 | logística nombrada, cruzada por cantidad |
| 4736 | Cream $55.370 | residual con CO2 excluido de la base |
| 4672 | Cream $55.369 | barril de 25L normalizado a 30L, con CO2 |
| 4572 | Cream y Scotch $47.836 | logística nombrada por capacidad + residual |
| 4743 | Scotch $1.300 · Stout $1.500 | residual uniforme en botellas |

### 3. `app/negocio/costos.py`

`margenes(cur, receta=None)` deja de leer `PRECIOS_VENTA_NETO` primero y pasa a
cruzar cada SKU con `precios_por_formato`:

- SKU → clave de formato: `Barril 30L acero` y `Barril 30L PET` → `barril 30L`
  (acero y PET comparten precio de venta y difieren en costo, que ya venía
  así); `Botella 330ml` → `botella 330`.
- Si hay precio deducido, se usa, y se arrastran `n_facturas` y
  `precio_promedio` al resultado.
- Si no hay ninguna factura para ese formato (SKU nuevo sin ventas),
  `PRECIOS_VENTA_NETO` queda de **respaldo**, marcado como tal.
- Sin ninguno de los dos, `precio_venta` y `margen` siguen en `None`. Nunca se
  inventa un margen.

Se agrega `origen` (`"facturas"` | `"lista"`) a cada fila.

### 4. Filtro canónico de productos: excluir el CO2

El filtro canónico de `CLAUDE.md` excluye logística y PET, pero **no el CO2**,
así que hoy las 6 líneas de carga de CO2 se cuentan como si fueran un producto
del catálogo. Es el mismo pass-through que el PET y debe recibir el mismo trato.
Se agrega `AND p.nombre_producto NOT ILIKE '%co2%'` en los tres lugares que
usan el filtro — `app/dashboard.py`, `scripts/wiki_update.py` y la skill
`reporte-semanal` — y se actualiza el bloque canónico de `CLAUDE.md`.

Es un arreglo pequeño y de arrastre, no parte del síntoma original, pero sale
de la misma corrección del productor y dejarlo sabiendo que está mal sería peor.

### 5. `app/agent/orchestrator.py`

- `MAX_ITERACIONES` de 8 a **12**.
- **Turno final forzado sin tools.** Al agotarse las iteraciones, en vez de
  devolver el string de disculpa, se hace una última llamada al modelo **sin
  `tools`** y con una instrucción de cierre: *"responde ahora con lo que ya
  averiguaste; di explícitamente qué te faltó"*. Esto es lo que arregla de raíz
  el trabajo botado: una respuesta parcial honesta sirve, el mensaje actual no.
  El string de disculpa queda solo como respaldo si esa última llamada falla.

### 6. `app/agent/tools_negocio.py` y `system_prompt.py`

La tool `margenes` devuelve, por SKU, una línea autoexplicativa:

```
- Cream Ale Botella 330ml: precio $1.301 − costo $891 = margen $410 (31,5%)
  [12 facturas; promedio $1.298]
```

Su `description` deja de decir "solo barriles". Es la corrección que más importa
para el razonamiento del modelo: la herramienta ya no lo deja a medias, así que
no tiene motivo para irse a improvisar SQL.

En el system prompt, una regla nueva: para costo, precio de venta o margen de
**cualquier** formato, usar `costos_sku` / `margenes` y **nunca** calcular
precios con SQL sobre `productos`, explicando por qué (la línea `Logistica` no
está guardada ahí, así que cualquier precio deducido a mano sale a la mitad).
Y una línea sobre el CO2 como pass-through. El agente del chat no lee
`CLAUDE.md`; si la regla no está en el prompt, no existe para él.

## Tests

- `tests/test_negocio_precios.py` (nuevo), con cursor falso, un caso por regla:
  logística nombrada cruzada por cantidad; logística nombrada por capacidad
  (4572); residual proporcional a litros; CO2 y PET fuera de la base; barril
  parcial normalizado a 30L; factura anulada por NC descartada; factura de
  familia mixta descartada y contada; "Baril" reconocido.
- `tests/test_negocio_costos.py`: se **invierte**
  `test_margenes_botella_sin_precio_queda_none` — con precio deducido, la
  botella ya no da `None`. Se agregan el caso de respaldo por lista y el de
  `None` sin facturas ni lista.
- `tests/test_orchestrator.py`: al agotar iteraciones, `correr_loop_agente` hace
  una llamada final sin `tools` y devuelve su texto.
- `tests/test_tools_negocio.py`: la línea de `margenes` incluye el respaldo de
  facturas.
- Verificación manual contra la BD real: la tabla de folios de la sección 2.

## Riesgos y qué NO hace

- **El margen de botella sale optimista** por el reparto de mano de obra por
  litro (decisión del productor). La salida no lo advierte: sería ruido en cada
  respuesta. Queda documentado aquí.
- **No corrige los nombres de producto del SII.** Hay 23 variantes de logística
  y erratas como "Baril" o "Sctout". El algoritmo tolera lo observado; un nombre
  nuevo e irreconocible hace que esa factura no aporte precio y se cuente como
  descartada. Es el modo de falla correcto: callarse antes que inventar.
- **No toca `parse_dte.py`.** Guardar la línea `Logistica` en `productos`
  simplificaría el residual, pero cambia el pipeline y el significado de una
  tabla de la que dependen el dashboard, la wiki y el reporte semanal. El
  residual se calcula sin ese cambio.
- **Las latas 470cc no tienen SKU** en la capa de costos: tendrán precio
  deducido pero no margen. Correcto, no hay costo que restar.
- **El precio se deduce, no se declara.** Si el productor sube la lista y aún no
  emite una factura al precio nuevo, el sistema seguirá mostrando el viejo. Es
  el comportamiento deseado: refleja lo que realmente cobró.
- **El CO2 sigue sumando en el monto facturado**, como corresponde: es plata que
  entró. Solo deja de contarse como producto vendido.
