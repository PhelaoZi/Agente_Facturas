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

## Decisiones de negocio (confirmadas por el productor)

### Etiqueta

Toda etiqueta cuesta **$230 con IVA incluido** → **$193,28 neto**, una por
botella. Hoy no está en el BOM de envasado, así que el costo de la botella está
subestimado.

### Regla de la logística

La logística es aproximadamente **la mitad del precio** de venta y se factura en
línea aparte para reducir el ILA. El productor la escribe así:

- Si todos los ítems de la factura tienen **el mismo costo de logística**, pone
  **una sola línea** `Logistica` (sin nombrar la cerveza).
- Si tienen **costos distintos**, la **desglosa por estilo**: `Logistica Scotch`
  con su precio y `Logistica Stout` con el suyo.

Verificado contra el histórico: cuando desglosa, la línea de logística lleva
**la misma `cantidad`** que la línea de producto a la que pertenece, lo que hace
la atribución exacta.

```
Folio 4694:  Barril RIS        x2 @35.000  +  Logistica RIS        x2 @60.000  → $95.000/barril
             Barril Cream Ale  x2 @20.000  +  Logistica Cream Ale  x2 @35.370  → $55.370/barril ✓
```

$55.370 es exactamente el precio confirmado de la Cream, deducido sin lista.

### Mano de obra — NO se toca

Los $300.000/lote se siguen repartiendo por litro. Esto hace que la botella
cargue lo mismo que el barril aunque embotellar dé más trabajo, así que el
margen de botella queda algo optimista. **Es un supuesto deliberado del
productor, no un error.** Fuera del alcance de este trabajo.

## Hallazgos del histórico (812 facturas de venta)

Analizar el histórico completo cambió el algoritmo respecto del boceto inicial:

| Hallazgo | Consecuencia |
|----------|--------------|
| 714 facturas tienen **residual > 0** (logística sin nombre, filtrada de `productos`) | El caso mayoritario es el residual, no la línea nombrada |
| 46 tienen residual 0 (toda la logística nombrada y guardada) | Ambos caminos deben coexistir en la misma factura |
| Se venden **CO2** ("9 kg CO2", "Carga CO2", "Recarga CO2 9 kg") | El CO2 **no es cerveza**: hay que excluirlo de la base de reparto |
| Hay **barriles de 20L y 25L**, no solo 30L | La clave de precio debe incluir la **capacidad**, no solo "barril" |
| Hay **latas 470cc** | Otra familia de formato; sin SKU en el catálogo de costos |
| Aparece "**Baril**" (sin la segunda r) | El detector de formato debe tolerar erratas y falta de tildes |
| 25 facturas desde 2025 con `monto_neto_ajustado = 0` | Son **anuladas por nota de crédito**, no datos corruptos |

La exclusión del CO2 es la que valida el enfoque. Folio 4736: 3 barriles Cream
a $20.000 + una carga de CO2 a $15.000, residual $106.110. Repartiendo el
residual solo entre los 3 barriles → $35.370 c/u → **$55.370 el barril**, el
precio confirmado, al peso. Lo mismo en los folios 4093, 4113 y 4226. Si el CO2
entrara en el reparto, los cuatro darían mal.

El folio 4672 confirma la capacidad: Barril 25L Cream a $16.666 + residual
$29.475 = **$46.141**, que es exactamente 25/30 del precio del barril de 30L.

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

**Facturas elegibles:** `tipo_documento != 61` y **`monto_neto_ajustado IS
NULL`** — es decir, sin nota de crédito aplicada. Una NC parcial rebaja el neto
sin tocar las líneas de producto, así que distorsionaría el precio; una total lo
deja en 0. Se cuentan como descartadas, no se silencian.

**Clasificación de cada línea** (sobre el nombre normalizado — minúsculas, sin
tildes):

- `logistica` — contiene `logist`
- `envase` — calza `^(barril(es)?\s+)?pet\b` → pass-through, se ignora
- `insumo` — contiene `co2` → no es cerveza, se ignora
- `cerveza` — el resto

**Formato de una línea de cerveza:** familia + capacidad, tomadas del nombre.
La familia se detecta con `barr?il` (tolera la errata "Baril"), `botella` o
`lata`; la capacidad, con el primer número seguido de `l`/`cc`/`ml`. Resultado:
`barril 30L`, `barril 25L`, `botella 330`, `lata 470`. Sin familia o sin
capacidad reconocible, esa **línea** no aporta precio — pero sus unidades siguen
contando en la base de reparto del residual, porque la logística sí se pagó por
ellas. La factura no se descarta por eso.

**Cerveza de una línea:** se busca como subcadena el `nombre_cerveza` de cada
receta, normalizado, sobre el nombre de la línea. La receta más larga que calza
gana (evita que "Stout" se coma a "Stout Café/Cacao").

**Atribución de la logística, en dos pasadas:**

1. **Nombrada.** Cada línea de logística cuyo nombre identifique una cerveza
   presente en la factura le entrega su `total_linea`. Si esa cerveza aparece en
   **más de un formato** en la misma factura, es ambigua: se descarta la factura
   completa y se reporta.
2. **Residual.** `residual = monto_neto − Σ total_linea de todas las líneas
   guardadas` (incluidas PET y CO2, que sí están en `productos`). Es la línea
   `Logistica` exacta que `parse_dte` no guarda. Se reparte **uniforme por
   unidad** entre las unidades de cerveza que no recibieron logística nombrada —
   que es justamente lo que el productor quiere decir al no desglosarla.
   Las líneas de logística nombrada que **no** identifican cerveza ("Logistic",
   "Logistica 30L") se suman al residual.
   Si esas unidades pertenecen a **más de un formato**, es ambigua: se descarta
   la factura y se reporta. Hoy no ocurre en ninguna factura del histórico.

**Precio unitario** = `(total_linea del producto + logística atribuida) / cantidad`.

**Salida**, una fila por `(cerveza, formato)`:

```python
{"cerveza", "formato",
 "precio_ultimo", "fecha_ultimo", "folio_ultimo",
 "precio_promedio", "n_facturas", "n_descartadas"}
```

`precio_ultimo` es el precio de lista vigente; `precio_promedio` revela los
descuentos. El folio 4691 vendió Cream a $47.836 en vez de $55.370 — una lista
escrita a mano nunca lo habría mostrado.

### 3. `app/negocio/costos.py`

`margenes(cur, receta=None)` deja de leer `PRECIOS_VENTA_NETO` primero y pasa a
cruzar cada SKU con `precios_por_formato`:

- SKU → clave de formato: `Barril 30L acero` y `Barril 30L PET` → `barril 30L`
  (acero y PET comparten precio de venta y difieren en costo, que ya venía
  así); `Botella 330ml` → `botella 330`.
- Si hay precio deducido, se usa, y se arrastran `n_facturas` y
  `precio_promedio` al resultado.
- Si no hay ninguna factura para ese formato (SKU nuevo sin ventas),
  `PRECIOS_VENTA_NETO` queda de **respaldo**, marcado como tal en la salida.
- Sin ninguno de los dos, `precio_venta` y `margen` siguen en `None`. Nunca se
  inventa un margen.

Se agrega `origen` (`"facturas"` | `"lista"`) a cada fila.

### 4. `app/agent/tools_negocio.py`

La tool `margenes` pasa a devolver, por SKU, una línea autoexplicativa:

```
- Cream Ale Botella 330ml: precio $1.301 − costo $891 = margen $410 (31,5%)
  [12 facturas; promedio $1.298]
```

Y su `description` deja de decir "solo barriles". Es la corrección que más
importa para el razonamiento del modelo: la herramienta ya no lo deja a medias,
así que no tiene motivo para irse a improvisar SQL.

### 5. `app/agent/orchestrator.py`

- `MAX_ITERACIONES` de 8 a **12**.
- **Turno final forzado sin tools.** Al agotarse las iteraciones, en vez de
  devolver el string de disculpa, se hace una última llamada al modelo **sin
  `tools`** y con una instrucción de cierre: *"responde ahora con lo que ya
  averiguaste; di explícitamente qué te faltó"*. Esto es lo que arregla de raíz
  el trabajo botado: una respuesta parcial honesta sirve, el mensaje actual no.
  El string de disculpa queda solo como respaldo si esa última llamada falla.

### 6. `app/agent/system_prompt.py`

Una regla nueva en el bloque de herramientas de negocio: para costo, precio de
venta o margen de **cualquier** formato, usar `costos_sku` / `margenes` y
**nunca** calcular precios con SQL sobre `productos` — explicando por qué (la
línea `Logistica` no está guardada ahí, así que cualquier precio deducido a mano
sale a la mitad). El agente del chat no lee `CLAUDE.md`; si la regla no está en
el prompt, no existe para él.

## Tests

- `tests/test_negocio_precios.py` (nuevo), con cursor falso, un caso por regla:
  logística nombrada cruzada por cantidad; residual uniforme; CO2 y PET fuera de
  la base de reparto; capacidad 25L distinta de 30L; factura con NC descartada;
  factura ambigua descartada y contada; "Baril" reconocido.
- `tests/test_negocio_costos.py`: se **invierte**
  `test_margenes_botella_sin_precio_queda_none` — con precio deducido, la
  botella ya no da `None`. Se agrega el caso de respaldo por lista y el de
  `None` cuando no hay ni facturas ni lista.
- `tests/test_orchestrator.py`: al agotar iteraciones, `correr_loop_agente`
  hace una llamada final sin `tools` y devuelve su texto.
- `tests/test_tools_negocio.py`: la línea de `margenes` incluye el respaldo de
  facturas.
- Verificación manual contra la BD real: los folios 4694, 4736, 4672 y 4743
  deben reproducir $55.370, $55.370, $46.141 y $1.300 respectivamente.

## Riesgos y qué NO hace

- **El margen de botella sale optimista** por el reparto de mano de obra por
  litro (decisión del productor, ver arriba). La salida no lo advierte: sería
  ruido en cada respuesta. Queda documentado aquí.
- **No corrige los nombres de producto del SII.** Hay 23 variantes de logística
  y erratas como "Baril" o "Sctout". El algoritmo tolera lo observado; un nombre
  nuevo e irreconocible hace que esa factura no aporte precio, y se cuenta como
  descartada. Es el modo de falla correcto: callarse antes que inventar.
- **No toca `parse_dte.py`.** Guardar la línea `Logistica` en `productos`
  simplificaría el residual, pero cambia el pipeline y el significado de una
  tabla de la que dependen el dashboard, la wiki y el reporte semanal. El
  residual se calcula sin ese cambio.
- **Las latas 470cc no tienen SKU** en la capa de costos, así que tendrán precio
  deducido pero no margen. Correcto: no hay costo que restar.
- **El precio se deduce, no se declara.** Si el productor sube la lista y aún no
  emite una factura al precio nuevo, el sistema seguirá mostrando el viejo. Es
  el comportamiento deseado: refleja lo que realmente cobró.
