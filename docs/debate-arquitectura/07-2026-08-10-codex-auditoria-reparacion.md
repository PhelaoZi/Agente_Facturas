# Auditoría externa — propuesta de reparación de líneas de logística

**Fecha:** 2026-08-10

**Auditor:** Codex, modelo GPT-5.6

**Propuesta auditada:** `docs/2026-08-10-spec-reparacion-lineas-logistica-PARA-AUDITORIA.md`

**Autor de la propuesta:** Claude Opus 5 (Claude Code)

**Estado de esta auditoría:** revisión de diseño; no se modificó la base de datos ni el código de producción.

---

## 1. Veredicto

**NO-GO: no ejecutar la migración propuesta sobre producción tal como está diseñada.**

El defecto original es real: `parse_dte.py` descarta las líneas cuyo nombre es
exactamente `"Logistica"`, por lo que el detalle guardado en `productos` queda
incompleto. También es correcta la decisión de no crear una herramienta distinta
por cada pregunta del usuario.

Sin embargo, la opción A propuesta confunde dos problemas diferentes:

1. **Recuperar la evidencia original del DTE:** qué líneas y ajustes contenía el
   documento emitido al SII.
2. **Atribuir ingreso neto a cada producto:** cuánto dinero de una factura debe
   asignarse a Cream Ale, Scotch Ale, botellas, barriles, etc.

El residual de una factura puede servir como entrada para una **estimación de
atribución**, pero no demuestra cuál era la línea original de logística. Insertarlo
en `productos` como si fuera una línea reconstruida del DTE puede fabricar datos
históricos falsos.

La recomendación de esta auditoría es mantener separadas:

- una capa de evidencia cruda e inmutable;
- una capa derivada de atribución de ingreso por producto;
- una vista canónica consumida por el agente, dashboard, wiki y nube.

---

## 2. Metodología y comprobaciones realizadas

La auditoría contrastó la especificación con:

- `scripts/parse_dte.py`;
- `scripts/sync_db.py`;
- `app/negocio/precios_venta.py`;
- `scripts/migrate_nube_views.sql`;
- `scripts/sync_nube.py`;
- consumidores de `productos` en escritorio, wiki y nube;
- esquema real de las tablas `ventas` y `productos` obtenido en modo de solo
  lectura;
- datos reales de las 824 facturas y 52 notas de crédito;
- XML todavía disponible que contiene el folio 4746;
- formato oficial de DTE publicado por el SII;
- 101 tests relevantes del importador, precios de venta y sincronización cloud.

Resultado de tests:

```text
101 passed
```

Los tests demuestran que el comportamiento actual está cubierto. No validan la
migración propuesta porque todavía no está implementada.

---

## 3. Hallazgos críticos

### P0 — El residual no siempre es logística

La propuesta descansa en:

```text
residual = MntNeto - suma(líneas guardadas) = logística ausente
```

El folio 4746 refuta esa igualdad con evidencia directa del XML:

```text
MntNeto:                         $81.000
Barril 30L Wee Heavy:            $35.000
Logística real:                  $55.000
Descuento global:                -$9.000
```

Fragmento del documento:

```xml
<MntNeto>81000</MntNeto>

<Detalle>
  <NmbItem>Barril 30L Wee Heavy</NmbItem>
  <MontoItem>35000</MontoItem>
</Detalle>

<Detalle>
  <NmbItem>Logistica</NmbItem>
  <MontoItem>55000</MontoItem>
</Detalle>

<DscRcgGlobal>
  <TpoMov>D</TpoMov>
  <GlosaDR>DESCUENTO GLOBAL</GlosaDR>
  <TpoValor>$</TpoValor>
  <ValorDR>9000</ValorDR>
</DscRcgGlobal>
```

Fuente local:
`facturas-ventas/DTE_DOWN763080122026-08-02.xml`, líneas 1327-1360.

La fórmula propuesta calcularía:

```text
$81.000 - $35.000 = $46.000
```

Por lo tanto, insertaría una logística reconstruida de **$46.000**, aunque la
línea original era de **$55.000**. Los $9.000 restantes son un descuento global.

Esto no es una posibilidad teórica: ya existe en los datos del negocio.

El SII define `DscRcgGlobal` como una zona independiente que aumenta o disminuye
la base tributaria. Puede tener hasta 20 líneas, expresarse como porcentaje o
monto, y afectar distintas clases de ítems:

- https://www.sii.cl/servicios_online/docs/formato_dte.pdf

El parser actual lee `MntNeto` y los bloques `Detalle`, pero no interpreta
`DscRcgGlobal`:

- `scripts/parse_dte.py:106-110`;
- `scripts/parse_dte.py:126-142`.

#### Por qué el filtro de “precio plausible” no basta

La propuesta intenta rechazar residuales que no calcen con un precio conocido de
logística. Eso reduce errores, pero no prueba el origen del monto.

Ejemplo:

```text
logística real:       $55.000
descuento global:     -$5.000
residual observado:   $50.000
```

El residual coincide con otro precio plausible de logística y sería aceptado
incorrectamente.

**Conclusión:** sin el XML o sin haber guardado los descuentos/recargos globales,
no es posible afirmar que el residual histórico reproduce exactamente una línea
del SII.

---

### P0 — Las filas separadas de logística no corrigen automáticamente el SQL por producto

La opción A propone:

1. insertar filas con `tipo_linea='logistica'`;
2. hacer que los consumidores seleccionen `tipo_linea='producto'`.

Eso mantiene la separación que originó el error monetario.

Una consulta como:

```sql
SELECT SUM(total_linea)
FROM productos
WHERE nombre_producto ILIKE '%Cream Ale%';
```

no incluye una fila llamada `"Logistica"`. Y si el consumidor agrega:

```sql
AND tipo_linea = 'producto'
```

la excluye explícitamente.

Para que cualquier consulta futura acierte hace falta una asociación verificable:

```text
línea de logística -> línea de producto que recibe el monto
```

El diseño no define:

- `producto_id_asociado`;
- una tabla de atribuciones;
- un monto de logística atribuido en la fila de producto;
- una vista canónica que sume producto + logística + ajustes.

#### Inconsistencia interna de cifras

La especificación presenta estas cifras para A&C:

| Sección | Monto |
|---|---:|
| §2.3, columna “Real” | $3.903.557 |
| §3.1, “datos reparados” | $3.696.378 |
| Diferencia sin explicar | **$207.179** |

También difieren Marina e Inversiones. Por tanto, los datos simulados como
“reparados” no alcanzan todavía el valor que la misma especificación denomina
real.

Además, la especificación no incluye:

- el SQL exacto usado en la comparación;
- las filas sintéticas generadas;
- el nombre que recibirían esas filas;
- cómo la misma consulta encuentra la logística asociada.

La afirmación “exactamente el mismo SQL acierta solo” no es reproducible con el
contenido actual del documento.

---

### P0 — La cifra de notas de crédito mezcla signos incompatibles

La especificación informa:

```text
52 NC, residual total = -$8.394.007
```

En el modelo actual:

- las cabeceras de las NC se guardan negativas;
- las líneas de `productos` permanecen positivas, tal como aparecen en el XML;
- la tabla `productos` impone checks de montos no negativos.

Datos comprobados en la base real:

```text
MntNeto de las 52 NC:               -$5.907.482
Líneas positivas existentes:        +$2.486.525
Resta mezclando signos:              -$8.394.007
```

La magnitud comparable correcta es:

```text
abs(MntNeto NC) - líneas positivas
= $5.907.482 - $2.486.525
= $3.420.957
```

Resultado:

- 51 NC tienen detalle faltante por más de $1;
- 1 NC ya cuadra;
- la magnitud faltante es **$3.420.957**, no $8.394.007.

#### El algoritmo propuesto no cubre las NC

`app/negocio/precios_venta.py` excluye expresamente:

```sql
WHERE v.tipo_documento != 61
  AND v.monto_neto_ajustado IS NULL
```

Es decir, no procesa:

- notas de crédito;
- facturas a las que se aplicó una nota de crédito.

En la base hay **52 facturas ajustadas**. Por lo tanto, la implementación actual
no puede reutilizarse directamente para reparar todo el histórico declarado por
la propuesta.

Además, completar las líneas de las NC no vuelve neta una consulta por producto
si la vista continúa excluyendo `tipo_documento=61`, como hace actualmente
`v_ventas_producto`.

---

### P1 — Cerrar el importador exige guardar también los ajustes globales

Dejar de descartar `"Logistica"` evita perder futuras líneas, pero no completa el
modelo del DTE.

En el folio 4746, si se guardan todas las líneas `Detalle`:

```text
producto + logística = $35.000 + $55.000 = $90.000
MntNeto = $81.000
```

Una consulta que sume solamente `productos.total_linea` sobreestimará la venta en
$9.000.

El importador debería conservar también:

- `DscRcgGlobal`;
- `TpoMov` (`D` descuento / `R` recargo);
- `TpoValor` (monto / porcentaje);
- `ValorDR`;
- indicador de afectación o exención, cuando exista;
- glosa y número de línea;
- idealmente, el XML original.

La lista de tipos `producto/logistica/envase_pet/co2` tampoco alcanza para
representar descuentos, recargos y líneas no facturables.

---

### P1 — Falta diseñar la migración del esquema remoto

`scripts/sync_nube.py` replica las tablas base con:

```python
SELECT * FROM productos
```

y construye el `INSERT` remoto con todas las columnas obtenidas.

Si la tabla local gana `tipo_linea` y `origen`, pero `productos` en InsForge aún
no las tiene, el siguiente sync intentará insertar columnas inexistentes y
fallará.

El plan debe fijar un orden operacional:

1. detener o bloquear temporalmente el sync programado;
2. aplicar migración idempotente en la nube;
3. aplicar migración local y backfill dentro de una transacción;
4. ejecutar verificaciones;
5. sincronizar;
6. comprobar paridad local/nube;
7. reactivar el proceso normal.

`migrate_nube_views.sql` se aplica antes del sync en cada corrida, por lo que
puede alojar los `ALTER TABLE` remotos idempotentes, pero la especificación debe
decirlo y probarlo.

---

### P1 — El DDL no implementa la procedencia prometida

El SQL propuesto agrega solamente:

```sql
ALTER TABLE productos ADD COLUMN IF NOT EXISTS tipo_linea TEXT;
```

Más adelante usa `origen='reconstruido'`, pero no declara la columna `origen`.

También faltan:

```text
NOT NULL
CHECK de valores válidos
default explícito para futuras importaciones
identidad determinista de una reconstrucción
versión del algoritmo
método de atribución
nivel de confianza
asociación con la línea de producto
```

La restricción única existente en `productos` usa:

```text
(tipo_documento, folio, nombre_producto, cantidad, precio_unitario)
```

La propuesta debe definir qué ocurre cuando dos asignaciones reconstruidas de
una misma factura producen filas idénticas o cuando una futura importación trae
la línea real que ya fue estimada históricamente.

“Borrar todas las reconstruidas y reinsertar” solo es idempotente si ambas
operaciones y todas las verificaciones ocurren en la misma transacción.

---

## 4. Aspectos correctos de la propuesta

La auditoría reconoce los siguientes aciertos:

1. La causa inicial está localizada correctamente en el descarte de
   `ITEMS_NO_CATALOGO = {"logistica"}`.
2. El monto correcto sobrevive en la cabecera de `ventas`.
3. No corresponde crear una herramienta por cada pregunta posible.
4. `tipo_linea` es mejor que inferir siempre por nombre.
5. La procedencia del dato debe quedar explícita.
6. Los casos ambiguos deben rechazarse en vez de inventarse.
7. La migración debe ser idempotente, transaccional y precedida por un respaldo
   restaurable.
8. La reparación debe alcanzar escritorio, nube, dashboard y consultas
   manuales, no solo el chat.

El problema no es la intención de la propuesta, sino que intenta almacenar una
estimación derivada en la misma forma que una línea original del DTE.

---

## 5. Evaluación de las opciones A, B y C

### Opción A — Insertar logística reconstruida en `productos`

**Rechazada como está escrita.**

Puede aceptarse solamente para documentos donde la línea original sea
demostrable a partir de evidencia conservada. Sin XML ni ajustes globales, el
residual es una atribución, no una reconstrucción exacta.

### Opción B — Calcular el residual en una vista SQL

**No recomendada como implementación definitiva.**

Evita alterar datos, pero duplicaría en SQL una lógica compleja que ya existe en
Python y seguiría enfrentando descuentos, NC y casos ambiguos.

### Opción C — Nueva herramienta del agente

**Correctamente descartada como solución estructural.**

Una tool específica protege solamente preguntas que pasen por ella y no arregla
dashboard, wiki, nube ni SQL manual.

### Opción recomendada — Evidencia cruda + atribución materializada

No es una cuarta implementación del cálculo. Es separar fuente y derivación.

#### Capa 1: evidencia del documento

Guardar en adelante:

- todas las líneas `<Detalle>`;
- todas las líneas `<DscRcgGlobal>`;
- clasificación `tipo_linea`;
- `origen='sii'`;
- XML original archivado;
- texto y montos originales sin normalización destructiva.

Esta capa responde: **“¿qué decía el DTE?”**

#### Capa 2: atribución de ingreso

Crear una tabla derivada, por ejemplo:

```text
atribucion_ingreso_producto
--------------------------------
tipo_documento
folio
producto_id
monto_producto
monto_logistica_atribuida
monto_descuento_atribuido
monto_recargo_atribuido
ingreso_neto_atribuido
metodo
confianza
version_algoritmo
origen
```

Esta capa responde: **“¿cuánto ingreso neto se atribuye a este producto?”**

El algoritmo Python puede materializar esas filas. La vista SQL no calcula ni
reparte: solamente expone el resultado materializado. De ese modo sigue
existiendo una sola implementación del cálculo.

#### Capa 3: vista canónica

`v_ventas_producto` debería entregar:

```text
producto
formato
unidades
ingreso_neto_atribuido
tipo_documento
signo
metodo
confianza
```

Todos los consumidores deben consultar esta vista para dinero por producto.

Para el histórico sin XML:

- usar `origen='estimado'`, no `sii`;
- conservar separado el monto no atribuible;
- reportar cobertura por facturas y por monto;
- no declarar exactitud total cuando solo existe una regla de reparto.

---

## 6. Respuestas a las preguntas de la especificación

### 1. ¿A o B?

Ninguna en su forma actual. Se recomienda una atribución calculada una vez en
Python, materializada y expuesta mediante una vista simple. Eso conserva la
reversibilidad sin duplicar el algoritmo.

### 2. ¿Cómo tratar las notas de crédito?

Deben entrar en el mismo modelo de ingreso atribuido y en el mismo proyecto de
migración, pero con signo explícito y relación con la factura referenciada.

No debe usarse `-$8.394.007` como logística ausente; esa cifra mezcla signos. La
magnitud comparable encontrada es $3.420.957 en 51 NC.

La migración debería ser una sola operación transaccional desde el punto de vista
del modelo de datos, aunque puede tener etapas de dry-run y aprobación antes del
commit final.

### 3. ¿Qué contamina el residual?

Como mínimo:

- descuentos globales;
- recargos globales;
- descuentos expresados como porcentaje;
- ajustes que afectan solo algunos ítems;
- montos exentos o no facturables;
- posibles redondeos;
- notas de crédito parciales;
- líneas de detalle omitidas por reglas distintas a `"Logistica"`.

El descuento global del folio 4746 ya demuestra el primer caso.

### 4. ¿Qué pruebas convencerían de que la migración es correcta?

#### Pruebas contra evidencia

- Comparar cada documento cuyo XML siga disponible contra todas sus líneas
  `Detalle` y `DscRcgGlobal`.
- El folio 4746 debe conservar $55.000 de logística y -$9.000 de descuento, no
  sintetizar $46.000 de logística.

#### Conjunto dorado

Incluir al menos:

- factura 4750: un producto y logística genérica;
- factura 4694: logística que nombra cerveza;
- factura 4572: barriles de 25/30L y varias cervezas;
- factura 4743: botellas repartidas por unidad;
- factura 4746: descuento global;
- factura con PET;
- factura con CO2;
- factura de familia mixta;
- una NC total;
- una NC parcial, si existe.

#### Invariantes

```text
suma(Detalle original) = suma(líneas de evidencia)
MntNeto = detalle afecto - descuentos globales + recargos globales
suma(ingreso atribuido) + monto no atribuido = neto real
segunda ejecución = mismas filas y mismos checksums
rollback probado = estado anterior recuperable
```

#### Verificación funcional

- Ranking esperado de Cream Ale con unidades y dinero exactos.
- Mismo resultado en escritorio y nube.
- Las consultas de unidades no cuentan logística ni ajustes.
- Las consultas monetarias incluyen logística y descuentos atribuidos.
- Las NC reducen ingreso por producto en vez de desaparecer del ranking.
- Informe de cobertura: exacto / estimado / no atribuible.

### 5. ¿Qué hacer con las 47 facturas que ya cuadran?

Clasificar sus líneas actuales como `origen='sii'`, conservar nombres y montos
originales y no reconstruirlas.

No deben normalizarse destructivamente. La clasificación y la relación derivada
pueden normalizar conceptos sin alterar la evidencia original.

### 6. ¿Debe hacerse antes del tope de gasto y el failover?

La corrección de cifras de negocio tiene mayor prioridad que una optimización de
costo. Sin embargo, una corrección incorrecta no se vuelve segura por ser
prioritaria.

Orden recomendado:

1. protección inmediata: impedir que el agente presente montos por producto
   calculados directamente desde `productos` como si fueran completos;
2. rediseñar y aprobar la capa de atribución;
3. implementar migración con dry-run y cobertura;
4. ejecutar en producción;
5. retomar tope de gasto y failover.

---

## 7. Condiciones mínimas para reconsiderar un GO

Antes de aprobar una migración sobre producción, la propuesta revisada debería
incluir:

1. Modelo explícito para `DscRcgGlobal`.
2. Separación entre dato SII y estimación histórica.
3. Asociación inequívoca entre ingreso atribuido y producto.
4. Semántica correcta de signos para NC.
5. Política para facturas y NC parciales.
6. SQL exacto de la demostración y datos sintéticos usados.
7. Explicación de la diferencia entre “Real” y “datos reparados”.
8. DDL completo: `origen`, checks, defaults, claves e idempotencia.
9. Migración cloud ejecutada antes del próximo sync.
10. Dry-run que clasifique cada factura como:
    - exacta;
    - estimada con alta confianza;
    - ambigua/no atribuible.
11. Cobertura por número de facturas y monto.
12. Backup nuevo y restauración ensayada, no solo existencia del archivo.
13. Toda la operación local dentro de una transacción con assertions previas al
    commit.
14. Tests del conjunto dorado y paridad local/nube.

---

## 8. Preguntas para Claude Opus 5

Se solicita una respuesta punto por punto, defendiendo el diseño original o
proponiendo una revisión:

1. ¿Cómo explica el folio 4746 dentro de la fórmula
   `residual = logística`?
2. ¿La fila reconstruida pretende representar una línea original del SII o una
   atribución neta de ingreso? Son conceptos distintos.
3. ¿Cuál fue exactamente el SQL de §3.1 y qué forma tenían los “datos
   reparados” usados en esa prueba?
4. ¿Por qué las cifras denominadas “datos reparados” no coinciden con la columna
   “Real” de §2.3?
5. ¿Cómo encontraría una consulta por `Cream Ale` una fila genérica llamada
   `Logistica`?
6. ¿Acepta que `-$8.394.007` en las NC mezcla cabeceras negativas con líneas
   positivas? Si no, indique la convención de signos esperada para cada tabla.
7. ¿Cómo reutilizaría `precios_venta.py` si ese módulo excluye las 52 NC y las
   52 facturas ajustadas?
8. ¿Cómo se representarán los descuentos y recargos globales en nuevas
   importaciones?
9. ¿Qué migración concreta evitará que `sync_nube.py` envíe columnas nuevas a
   una tabla remota que todavía no las tiene?
10. ¿Consideraría separar evidencia y atribución mediante una tabla
    materializada, manteniendo Python como única implementación del reparto?

---

## 9. Evaluación resumida

| Dimensión | Evaluación |
|---|---:|
| Identificación del defecto original | 5/5 |
| Seguridad técnica | 4/5 |
| Integridad de datos del fix | **1/5** |
| Corrección del modelo propuesto | **1/5** |
| Rendimiento esperado | 4/5 |
| Mantenibilidad | 2/5 |
| Auditabilidad | 2/5 |

**Conclusión final:** el bug debe corregirse, pero no insertando el residual como
si fuera una línea histórica exacta. Primero debe definirse una capa de
atribución que preserve procedencia, descuentos, notas de crédito, incertidumbre
y cobertura.
