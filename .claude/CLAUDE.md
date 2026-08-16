# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Qué va en este archivo — y qué no

**Si la respuesta vive en la base de datos o en el código, no se escribe aquí.**
Se va a desincronizar y nadie se va a enterar. Aquí va solo lo que ningún
`SELECT` puede contestar: por qué las cosas son como son.

- **Va aquí:** reglas de negocio y el *por qué* de las decisiones — la línea
  "Logistica" es precio y no un servicio, el envase PET es pass-through,
  `fecha_pago` es la fuente de verdad del cobro.
- **No va aquí:** esquema de la BD, claves primarias, listas de skills,
  dependencias con versiones. Eso ya vive en la base, en `.claude/skills/` y en
  `requirements.txt` — se consulta cuando hace falta.
- **Va en `.claude/rules/`:** lo que solo importa al tocar cierta área. Carga
  solo con esos paths, no en cada sesión.

Razón: `CLAUDE.md` se carga completo en cada sesión, se use o no. Y la
documentación duplicada es **peor que no tener documentación**. Este archivo
tuvo una tabla de claves primarias con 3 de 6 filas falsas (declaraba PK
compuesta en `conciliaciones` y en `productos`; hace rato ambas son `id
serial`). Sin esa tabla el agente consulta la BD y acierta; con ella, le cree y
se equivoca en silencio.

---

## Proyecto

**Zigurat ERP — Agente Facturas**
Automatización de sincronización semanal de facturas electrónicas DTE del SII a PostgreSQL.
Empresa: Elaboradora y Comercializadora Vintage SPA (Zigurat Brewery).

---

## Trabajo en paralelo con Antigravity

En este repo trabajan dos agentes alternándose según los créditos disponibles:
Claude Code y Antigravity. **Ambos pueden tocar cualquier carpeta** (no hay
reparto de áreas), así que la coordinación depende de git:

- **Antes de empezar:** `git status` y `git log --oneline -5`. Puede haber
  trabajo ajeno, incluso sin commitear. Si lo hay, no tocarlo ni mezclarlo —
  commitear solo los archivos propios (nunca `git add .`).
- **Al terminar:** commitear siempre.
- Si hay que corregir trabajo del otro: commitear su versión tal cual primero
  (atribuida a él), y los cambios propios en un commit aparte.

Protocolo completo en `AGENTS.md` (raíz).

---

## Comandos frecuentes

Los flujos de negocio se ejecutan con las skills del proyecto — cada una
documenta su uso y argumentos en `.claude/skills/`.

```bash
python -m pytest -q                  # Suite de tests del proyecto
python app/dashboard.py              # Dashboard web → http://localhost:8777 (o iniciar_dashboard.bat)
python scripts/generar_brief.py      # Brief diario manual
python scripts/backup_db.py          # Backup manual (la tarea programada corre sola a las 23:00)
```

Los scripts de `scripts/` se ejecutan directo solo para debug, nunca en
producción. Las migraciones de esquema (`scripts/migrate_*.py`) son idempotentes.

---

## Arquitectura

### Pipeline DTE (núcleo del proyecto)

```
XML del SII (ISO-8859-1) → parse_dte.py → changes.json → validate_changes.py → sync_db.py → PostgreSQL
```

Los 3 scripts en `scripts/` son secuenciales y obligatorios. `sync_db.py` se bloquea si no existe el flag `.changes_validated` que deja `validate_changes.py`. Nunca ejecutar `sync_db.py` sin validación previa.

#### Evidencia (`dte_*`) vs. datos de trabajo (`ventas`, `productos`)

Desde el 2026-08-10 conviven dos capas, y **mezclarlas es el error que hay que
evitar**:

| Capa | Tablas | Regla |
|---|---|---|
| **Evidencia** | `dte_lineas`, `dte_ajustes_globales`, `dte_impuestos`, `dte_archivos` | El DTE tal como lo emitió el SII. Se escribe una vez y **nadie la corrige**. Signos y montos tal cual vienen del XML |
| **Trabajo** | `ventas`, `productos` | Lo de siempre. `productos` sigue sin la línea `"Logistica"` a secas y con las mismas columnas, porque de ahí dependen la vista local, el sync a la nube y todos los filtros ya escritos |

Existe porque el pipeline descartaba en silencio cuatro cosas irrecuperables: la
línea `"Logistica"` (≈ mitad del precio del barril), los descuentos globales
`<DscRcgGlobal>`, el `<CodImpAdic>` de cada línea y la tasa de los impuestos.
Sin eso, el ingreso por producto salía a un tercio de lo real y no había forma
de notarlo. Del histórico no hay nada que hacer: sobreviven 2 XML de 876
documentos, y por eso el archivado de `dte-archivo/` es automático.

Dos cosas que valen al consultar:

- **`cod_imp_adic = 26` es el SII declarando que esa línea es cerveza** (ILA
  20,5%). Es mejor que cualquier match por nombre: en `productos` hay 123
  descripciones distintas, con erratas (`Baril`, `Balck IPA`, `Scoth Ale`).
- **El monto de una línea NO es su neto** cuando el documento trae descuento
  global. El impuesto declarado sirve de verificación independiente: repartido
  el descuento a prorrata, `base_cerveza × tasa` tiene que dar el
  `impuesto_adicional` de la cabecera. Cuadra en 815 de 822 facturas, y los 7
  que no son exactamente los que traen descuento.

#### Dinero por producto — `v_ingreso_producto` y nada más

**El ingreso de una cerveza es su línea MÁS la logística que le toca.** La única
fuente es la vista `v_ingreso_producto`, sobre la capa derivada
`atribucion_ingreso` / `atribucion_documento`.

| Para | Usar | Nunca |
|---|---|---|
| Dinero por producto | `v_ingreso_producto`, o `mcp__negocio__ingreso_producto` | `productos`, `v_ventas_producto` |
| Unidades por producto | `v_lineas_producto`, agrupando por `cerveza` | agrupar por `nombre_producto` |

**Nunca agrupar por `nombre_producto`.** El nombre se escribe a mano en cada
factura: **125 formas distintas, 84 de ellas cerveza, que colapsan en 27**.
`Barril 30L APA` y `Barril 30L  APA` (doble espacio) son dos filas para
Postgres, así que agrupar por el texto crudo parte las unidades de una cerveza
entre sus erratas — el chat llegó a mostrar la misma cerveza dos veces en una
tabla. La traducción vive en `linea_canonica` (una fila por línea de
`productos`) y se consulta por `v_lineas_producto`, que además trae `clase`
(`cerveza`/`logistica`/`envase`/`co2`/…): **filtrar por `clase = 'cerveza'` en
vez de repetir los `ILIKE`** de más abajo.

`productos` NO se corrige: es lo que dice el documento tributario y reescribirla
sería falsificar la evidencia. Se traduce al consultar.

`calcular_atribucion.py` avisa en su informe los nombres que no reconoce —
agregarlos a `CERVEZAS` en `app/negocio/clasificacion_lineas.py`.

Sumar `productos.total_linea` da **un tercio** de lo real y además **ordena mal
el ranking de clientes**: en Cream Ale 2026 daba Marina $1.220.000 primero
cuando el real es A&C con $3.860.544.

La capa es derivada y se recalcula entera con
`python scripts/calcular_atribucion.py` (`--simular` para solo ver el informe).
No se versiona ni tiene modo sombra a propósito: el rollback es volver a
correrla. Si el lote no cuadra contra el neto de `ventas`, no escribe nada.

**Importar desde el dashboard ya encadena todo:** wiki → atribución → nube
(`_tareas_post_importacion` en `app/dashboard.py`). Existe porque no lo hacía:
el 2026-08-16 se importaron 13 facturas de agosto y quedaron fuera del dinero
por cerveza, mientras la tarea programada de la nube seguía publicando a diario
lo que hubiera. **Si la atribución falla no se publica nada** — `sync_nube`
trunca y recarga, así que subiría `ventas` nueva con el dinero por cerveza
viejo. Mejor el teléfono una versión atrás completo que media versión adelante.

El deploy del chat de la nube va por `python scripts/deploy_chat.py`, que borra
el bundle antes de construir: hacerlo a mano subió una vez el bundle de la
corrida anterior sin avisar, porque `deno bundle` falló y el archivo viejo
seguía en disco.

Cada fila declara `fuente` (`linea_dte` o `residual_cabecera`), `metodo` y
`calidad` (`deterministica` o `estimada`). **Toda respuesta de plata por
producto repite el período y la cobertura**: en facturas con varias cervezas la
logística se reparte a prorrata y eso no se puede verificar contra el documento.

Cobertura actual: 873 de 876 documentos, $87.887.620 atribuidos de $89.639.125
(98,0%), cuadratura exacta. Lo que queda afuera son $221.918 que no son cerveza
(malta y arriendo de schopera, correctamente excluidos) y 3 documentos por
$214.528 que mezclan un barril de 20L con latas y traen la logística sin
desglosar: no hay base común para repartir entre formatos y **falta la regla del
productor**, no el dato. Historia del problema y de la decisión en
`docs/debate-arquitectura/`.

**Los documentos con descuento global se recuperan invirtiendo el ILA**, y esa
es la única parte del sistema que lo hace. El impuesto se calcula sobre la base
ya descontada, así que dice qué proporción de las líneas sobrevivió; con eso se
escalan y el residual absorbe la imprecisión (2 pesos en el folio 4746). Nunca
se afirma que la base sea exacta —la invariante sigue exigiendo que todo sume el
neto— y las líneas quedan marcadas `estimada`. La verificación normal
(`_ila_confirma`) sigue siendo hacia adelante y no invierte nada.

**En la nube la atribución NO se recalcula: se replica.** `sync_nube.py`
materializa `v_ingreso_producto` como la tabla `ingreso_producto` y encima crea
una vista del mismo nombre que la local, así el SQL escrito contra el PC corre
igual contra la réplica. El motor vive solo acá — tenerlo en dos lados sería
tener la regla en dos lados, que es exactamente como empezó este problema.

### Pipeline de conciliación bancaria

```
Excel Itaú (transferencias/) → import_transferencias.py → movimientos_banco
                                                              ↓
facturas pendientes (ventas sin fecha_pago) ← conciliar_banco.py → conciliaciones + ventas.fecha_pago
                                                              ↓
                                              flujo_caja.py → proyección 4 semanas
                                              (usa avg dias_pago por cliente + cuentas_por_pagar)
```

---

## Base de datos

| Parámetro | Valor |
|-----------|-------|
| Motor | PostgreSQL local |
| Puerto | 5432 |
| Base de datos | `dte_facturas_chile` |
| Usuario | `postgres` |
| Conexión | Credenciales en `.env`, cargadas por `_load_env()` en cada script |

MCP server configurado en `.mcp.json` para queries ad-hoc via `@modelcontextprotocol/server-postgres`.

---

## Estructura de facturación — CRÍTICO para cálculos de ingresos

Zigurat divide cada venta de barril en **dos líneas dentro de la misma factura**:

| Línea | Descripción | Precio ejemplo | Impuestos |
|-------|-------------|---------------|-----------|
| 1 | Producto (ej: "Barril 30L Cream Ale") | $20.000 neto | IVA 19% + Impuesto Adicional 20,5% (ILA) |
| 2 | "Logistica" | $35.370 neto | Solo IVA 19% |

**El precio real del barril es la SUMA de ambas líneas: $55.370 neto ($69.990 total con impuestos).**

Esta estructura se usa para optimizar la carga tributaria: el ILA (20,5%) solo aplica al ítem de cerveza, no a logística.

### Consecuencias para queries y cálculos:

- **Nunca usar `precio_unitario` de la tabla `productos` para estimar el precio de venta** — solo refleja una parte del precio real.
- **Para calcular ingresos reales por factura**: usar `COALESCE(monto_neto_ajustado, monto_neto)` de la tabla `ventas` — ya incluye ambas líneas sumadas.
- **Para calcular el precio real por barril**: dividir el neto de la factura por el número de barriles vendidos (contar solo el ítem de cerveza, no el de logística).
- **El ítem "Logistica" en `productos` NO es un servicio separado** — es parte del precio de la cerveza disfrazado para reducir ILA.
- **`impuesto_adicional` en `ventas`** = ILA (20,5%) aplicado solo sobre el valor neto del ítem cerveza.

### Línea de envase "Barril PET" — pass-through, NO es venta de cerveza

Cuando la cerveza se vende en barril PET (envase desechable, a diferencia del
barril de acero que se recupera), la factura trae una **tercera línea** además de
producto + Logistica: el envase ("Barril Pet 30L", "Barril PET 30L", "Barriles
Pet 30L", "Pet 20L"… hay variantes de escritura). Reglas (confirmadas por el
productor, folio 4664 como ejemplo):

- La línea PET es el **costo del envase traspasado al cliente** (pass-through
  sin margen): NO es venta de cerveza ni un producto del catálogo, aunque SÍ es
  parte del monto total facturado.
- La Logistica aplica a TODOS los formatos, **incluido PET** (es desglose
  tributario para reducir ILA, no un servicio real).
- Precio/margen real de la cerveza en PET = línea producto + línea Logistica,
  **excluyendo** la línea del envase.

**Filtro canónico para rankings/agregados por producto** (aplicado en
`app/dashboard.py`, `scripts/wiki_update.py` y la skill reporte-semanal):

```sql
AND p.nombre_producto NOT ILIKE '%logist%'
AND p.nombre_producto !~* '^(barril(es)?\s+)?pet\y'
AND p.nombre_producto NOT ILIKE '%co2%'
```

> Ojo con `parse_dte.py`: `ITEMS_NO_CATALOGO = {"logistica"}` filtra por match
> EXACTO, así que las variantes reales ("Logistica Cream Ale", "Logistic", …)
> **sí quedan en `productos`**. La tabla `productos` contiene todas esas líneas;
> la exclusión correcta se hace al consultar, con el filtro de arriba.

### Línea de CO2 — pass-through, tampoco es venta de cerveza

Zigurat instala en algunos restaurantes una **schopera de su propiedad**, y el
cilindro de CO2 que empuja la cerveza también es suyo. Cuando se acaba, le
llevan una carga nueva comprada en **Clean Ice** (aparece en las facturas de
compra) y se le cobra al cliente **exactamente lo que costó**.

- La línea de CO2 ("9 kg CO2", "Carga CO2", "Recarga CO2 9 kg"… hay variantes)
  es un **traspaso de costo sin margen**, igual que el envase PET: no es un
  producto del catálogo ni venta de cerveza, aunque sí suma en el monto
  facturado.
- Va excluida del filtro canónico de arriba y de la base de reparto de la
  logística en `app/negocio/precios_venta.py`.

### Barriles de 20L y 25L — no son otro formato

Todos los barriles son de 30L. Cuando los últimos litros del fermentador no
alcanzan a llenar uno, se despacha ese mismo barril con 20 o 25 litros dentro y
se factura como "Barril 25L". **Precio y logística escalan con los litros**, así
que `precios_venta.py` normaliza el precio a barril de 30L equivalente y todos
comparten la clave de formato `barril 30L`.

**Cómo lo calcula el productor** (confirmado por él y verificado contra las
facturas, 2026-08-16): divide *las dos* líneas del barril de 30L por 30 y
multiplica por los litros que despacha. Black IPA en abril-2024 valía $45.000 de
cerveza + $52.000 de logística por 30L; los barriles de 20L de esa semana se
facturaron a $30.000 ($45.000 × 20/30, exacto en el folio 4019) y la logística
sale a $34.667 por el mismo camino.

El caso que esto NO resuelve es una factura que mezcle barriles con latas y traiga
la logística en una sola línea: para repartirla hay que conocer la tarifa de 30L
de ese estilo, y esa no está en el documento —hay que deducirla de otras
facturas—. Son 3 documentos de abril-2024 ($214.528, 0,2%) y el productor ya no
recuerda cuál de las dos lecturas posibles fue; quedan sin atribuir a propósito.
Si vuelve a pasar, `logistica_no_repartible` lo reporta al importar y ahí
conviene resolverlo con el caso fresco.

### Precio de venta — se deduce de las facturas, no de una lista

`app/negocio/precios_venta.py` reconstruye el precio neto unitario real por
`(cerveza, formato)` sumando la línea de producto más la logística que le
corresponde. **Regla con que el productor escribe la logística** (confirmada):

- Mismo costo de logística para todo lo facturado → **una sola línea**
  `Logistica`, sin nombrar nada.
- Costos distintos → la **desglosa por estilo** (`Logistica Scotch` +
  `Logistica Stout`), y cada línea lleva la **misma cantidad** que su producto.

La logística sin nombrar se reparte **por litro** en barriles y **por unidad**
en botellas. Una factura que mezcle familias sin desglosar la logística se
descarta y se reporta: no hay forma de saber cuánto le toca a cada una.

`PRECIOS_VENTA_NETO` en `costos.py` quedó solo de **respaldo** para un SKU que
todavía no se ha vendido nunca.

### Precios de venta por barril 30L (neto, confirmados por el productor)

| Cerveza | Ítem cerveza | Ítem logística | **Total neto** | Total con impuestos |
|---------|-------------|----------------|---------------|---------------------|
| Cream Ale | $20.000 | $35.370 | **$55.370** | $69.990 |
| Scotch Ale | $20.000 | $35.370 | **$55.370** | $69.990 |
| Stout Café/Cacao | $25.000 | $50.000 | **$75.000** | $94.375 |
| Paint it Black | $38.000 | $60.000 | **$98.000** | $124.410 |

### Estructura de costos de producción

- **Mano de obra:** $300.000/semana = $300.000/lote (1 lote/semana, costo de retiros del productor y socio)
- **Servicios variables:** $185.000/lote (agua, luz, gas)
- **Lote estándar:** 540 litros → 513 litros envasables (5% merma) → ~17 barriles de 30L

---

## Reglas críticas para queries SQL

Aplicar **siempre** al construir cualquier SQL sobre esta base de datos:

| Regla | Razón |
|-------|-------|
| `COALESCE(monto_total_ajustado, monto_total)` — nunca `monto_total` solo | Las NC actualizan `monto_total_ajustado`; ignorarlo infla totales |
| `COALESCE(monto_neto_ajustado, monto_neto)` — nunca `monto_neto` solo | Misma razón |
| `WHERE tipo_documento != '61'` en sumas de ventas | Las NC ya están descontadas en campos ajustados — incluirlas = doble conteo |
| `tipo_documento` es **texto** (`'33'`, `'61'`) | Comparar siempre con comillas |
| `folio` puede requerir `folio::integer` | Se almacena como texto |
| `COUNT(DISTINCT rut_cliente)` para contar clientes únicos | `COUNT(*)` cuenta facturas |
| `impuesto_adicional` (ILA) puede ser 0 | No es obligatorio > 0 en maquila/servicios |
| **Estado de pago: `fecha_pago IS NULL` = pendiente, `IS NOT NULL` = pagada** | **NUNCA usar JOIN a `conciliaciones` para esto** (ver sección "Estado de pago") |

### Query canónica — Ventas reales por cliente

```sql
SELECT c.razon_social, v.rut_cliente,
       SUM(COALESCE(v.monto_total_ajustado, v.monto_total)) AS total_real
FROM ventas v
JOIN clientes c ON c.rut_cliente = v.rut_cliente
WHERE v.tipo_documento != '61'
GROUP BY v.rut_cliente, c.razon_social
ORDER BY total_real DESC;
```

### Cuándo usar MCP vs /consultar-ventas

- **Consultas de negocio frecuentes** → siempre `/consultar-ventas` (usa query_ventas.py con queries probadas)
- **Consultas ad-hoc** → MCP está bien, pero verificar que el SQL cumpla las reglas de arriba
- **Si MCP da resultados raros** → agregar el comando a `query_ventas.py`

---

## Notas de Crédito — Modelo de datos

Las NC se guardan con **montos negativos** en `ventas`. Al sincronizar una NC, `sync_db.py` actualiza en la factura referenciada:
- `monto_neto_ajustado` = neto original − NC
- `monto_total_ajustado` = total original − NC

---

## Estado de pago de facturas — FUENTE DE VERDAD ÚNICA

**Una factura está pagada ⟺ `ventas.fecha_pago IS NOT NULL`. Punto.**

Esta es la única definición válida de estado de cobro. Existe porque dos
instancias distintas del agente dieron respuestas contradictorias a "¿qué
facturas debe el cliente X?": una miró `fecha_pago`, otra hizo `LEFT JOIN
conciliaciones`. Ambas estaban mal.

| Campo | Rol | Regla |
|-------|-----|-------|
| `ventas.fecha_pago` | **Fuente de verdad** del estado de cobro | `NULL` = pendiente; con fecha = pagada |
| tabla `conciliaciones` | **Solo evidencia** bancaria de respaldo | Incompleta por diseño — NO usar para estado de pago |

**Por qué `conciliaciones` NO sirve como fuente de verdad:** los pagos
importados desde el Excel de seguimiento (`importar_pagos_excel.py`) escriben
`fecha_pago` pero **no** generan fila en `conciliaciones`. Determinar deuda con
un JOIN a `conciliaciones` cuenta esos pagos legítimos como deuda e infla el
saldo de casi todos los clientes.

**Invariante que debe cumplirse siempre:** toda factura con conciliación
bancaria debe tener `fecha_pago`. Es decir, `conciliaciones ⟹ fecha_pago`.
Auditar con `python scripts/lint_estado_pago.py` (debe reportar 0
inconsistencias). La corrigió `migrate_backfill_fecha_pago.py` (160 facturas de
una carga masiva del 2026-01-25 que insertó conciliaciones sin `fecha_pago`).

### Cómo consultar deuda — siempre así

```bash
/consultar-ventas → pendientes --nombre "VDT SPA"   # deuda de un cliente (nombre o RUT)
/consultar-ventas → pendientes                       # deuda total (213 facturas)
```

```sql
-- Query canónica de facturas pendientes de cobro
SELECT v.folio, v.fecha, c.razon_social,
       COALESCE(v.monto_total_ajustado, v.monto_total) AS total
FROM ventas v
JOIN clientes c ON c.rut_cliente = v.rut_cliente
WHERE v.tipo_documento != 61
  AND v.fecha_pago IS NULL
  AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
ORDER BY v.fecha;
```

> Nota: en esta BD `tipo_documento` y `folio` son **integer** (no texto). El
> casteo `folio::integer` o comparar con `'61'` funciona igual, pero `!= 61`
> sin comillas es lo correcto.

---

## Hooks y protecciones

- **PreToolUse hook** en Edit/Write: bloquea ediciones a `changes.json` (archivo temporal generado por `parse_dte.py`)
- **Flag `.changes_validated`**: creado por `validate_changes.py`, requerido por `sync_db.py`, borrado tras sync exitoso

---

## Reglas por área (`.claude/rules/`)

Instrucciones detalladas que cargan automáticamente solo al trabajar con
archivos de cada área (frontmatter `paths:`):

- `wiki-clientes.md` — wiki Karpathy y snapshots `raw/` (carga con `scripts/wiki_*.py`, `wiki/`, `raw/`)
- `costos-produccion.md` — costos capa B: tablas, vista, parámetros de lote (carga con los scripts de costos/recetas/SKU)
- `backup-y-brief.md` — backup diario de la BD y brief diario (carga con sus scripts y `app/briefing/`)
- `conciliacion-bancaria.md` — workflow banco→factura y normalización de RUTs (carga con los scripts de banco y `transferencias/`)

---

## Dashboard y chat de negocio (Centro de Comando)

Ver `app/CLAUDE.md` (arquitectura del dashboard, agente del chat, mecanismo
propose/confirm/execute de acciones — solo se carga cuando se trabaja bajo `app/`).

---

## Convenciones del proyecto

- XMLs del SII de ventas van en `facturas-ventas/` con nombre `DTE_DDMMYYYY`
- XMLs de NC van en `Notas de Credito/`
- Encoding XML: ISO-8859-1 (latin-1)
- `changes.json` es temporal — no editarlo manualmente
- Todos los scripts cargan `.env` con `_load_env()` (no usan python-dotenv)
- Transacciones: `sync_db.py` usa `with conn:` para commit automático o rollback completo

---

## Dependencias

Fijadas en `requirements.txt` (instalar con `pip install -r requirements.txt`).
Actualizar versiones a propósito y correr la suite después.

**El agente del chat no tiene dependencias.** El loop es propio y las tools se
declaran con `app/agent/tools_base.py` (100 líneas). Hasta el 2026-08-09 estaba
`claude-agent-sdk`, usada solo por su decorador `@tool` — que no permitía
declarar un parámetro opcional y por eso el agente inventaba filtros. No
reintroducirla: hay un test que lo impide.

Config de entorno: copiar `.env.example` como `.env` y completar la clave de la BD.
