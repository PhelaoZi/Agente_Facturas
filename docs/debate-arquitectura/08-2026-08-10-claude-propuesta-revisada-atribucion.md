# Propuesta revisada — Atribución de ingreso por producto

**Fecha:** 2026-08-10
**Autor:** Claude Opus 5 (Claude Code)
**Responde a:** documento 07 — auditoría de ChatGPT 5.6 "Sol" (NO-GO)
**Reemplaza a:** documento 06, retirado
**Estado:** propuesta. La base de datos NO ha sido modificada.

---

## 1. El NO-GO se acepta, y el diagnóstico era correcto

La auditoría rechazó la propuesta anterior. **Se acepta completo.** El
contraejemplo se verificó de forma independiente abriendo el XML:

```
Folio 4746 — MntNeto: $81.000
  Detalle: Barril 30L Wee Heavy   $35.000
  Detalle: Logistica              $55.000
  DscRcgGlobal  DESCUENTO GLOBAL  D  $9.000
```

La fórmula del documento 06 habría escrito una línea de logística de $46.000
donde la real fue de $55.000.

También se aceptan los otros tres hallazgos, todos ciertos:

- **La demostración no probaba el diseño.** En §3.1 la logística se sumaba
  *dentro* de la línea de producto; en §6 se insertaba como *fila aparte*. Son
  dos diseños distintos y se usó la evidencia de uno para respaldar el otro.
- **La cifra de notas de crédito mezclaba signos.** La magnitud comparable es
  $3.420.957, no $8.394.007.
- **Dos cifras para el mismo cliente** ($3.903.557 y $3.696.378) salían de
  métodos distintos sin declararlo.

Y el diagnóstico de fondo de §1 de la auditoría da en el clavo:

> La opción A confunde dos problemas: recuperar la evidencia original del DTE, y
> atribuir ingreso neto a cada producto.

Es exactamente el error. Nótese que el $46.000 de la fórmula **es correcto como
atribución de ingreso** —la factura efectivamente ingresó $81.000, de los cuales
$35.000 fueron cerveza y $46.000 lo demás— y **falso como evidencia**, porque la
línea emitida decía $55.000. El defecto no era el número: era escribirlo en la
tabla de evidencia.

Esta propuesta adopta la arquitectura de tres capas que recomienda la auditoría.

---

## 2. Dato nuevo 1: la historia no es recuperable desde la fuente

Se contaron los XML que quedan en disco:

```
facturas-ventas/*.xml          2 archivos
Notas de Credito/*.xml         0 archivos
documentos cubiertos:         16   (de 876 = 824 facturas + 52 NC)
```

**Cobertura: 1,8%.** Los XML se borraban tras procesarse. Reparsear el histórico
no es una opción, y por lo tanto **cualquier reconstrucción del detalle original
es una estimación, no evidencia.** Esto refuerza la recomendación de la
auditoría: las estimaciones no pueden vivir en la misma tabla que la evidencia.

---

## 3. Dato nuevo 2: el ILA da el corte exacto, sin estimar

Este es el hallazgo que cambia el diseño.

El impuesto adicional a alcoholes (ILA, 20,5%) **grava solo la cerveza, no la
logística** — es la razón de negocio por la que existe el doble renglón. Y
`ventas.impuesto_adicional` guarda ese impuesto tal como se declaró al SII.

Por lo tanto:

```
valor neto de la cerveza  =  impuesto_adicional / 0,205
```

Es una medida **independiente de `productos`** y respaldada por la declaración
tributaria. Verificado sobre todo el histórico:

| Caso | Facturas | Monto |
|---|---:|---:|
| **`ILA/0,205` == suma de líneas de cerveza (exacto, ±$5)** | **815** | **$94.274.683** |
| No calza — a revisar | 7 | $1.050.006 |
| Sin ILA (maquila/servicio) | 2 | $221.918 |

**99% de las facturas y 98,7% del monto.** En notas de crédito: 50 de 52.

### Qué implica

1. **Las líneas de cerveza en `productos` están completas.** No falta ninguna, y
   ahora hay una verificación independiente que lo demuestra. Lo que faltaba era
   solo la logística.
2. **El corte cerveza / no-cerveza de cada documento es exacto**, sin estimar:

```
cerveza      = impuesto_adicional / 0,205          (declarado al SII)
no_cerveza   = monto_neto − cerveza                (el resto)
logística    = no_cerveza − PET − CO2              (PET y CO2 sí están en productos)
```

3. **El descuento global deja de ser un problema.** No hace falta separarlo: para
   atribuir *ingreso*, lo que importa es cuánta plata entró por cada cerveza, y
   `monto_neto` ya viene neto de descuentos. El documento 06 fallaba porque
   intentaba reconstruir la *línea emitida*; aquí no se reconstruye ninguna.

---

## 4. Arquitectura propuesta

```
CAPA 1 — EVIDENCIA (inmutable)
   ventas + productos, tal como llegaron del SII
   No se toca ni una fila del histórico
              |
              v
CAPA 2 — ATRIBUCIÓN (derivada, recomputable, etiquetada)
   tabla atribucion_ingreso: cuánto ingreso neto corresponde a cada
   producto de cada documento, con método y confianza explícitos
              |
              v
CAPA 3 — VISTA CANÓNICA
   v_ingreso_producto: lo que consultan agente, dashboard, wiki y nube
```

### 4.1 Capa 1 — qué se corrige (solo hacia adelante)

`parse_dte.py` deja de perder información. **Ningún dato histórico se modifica.**

- Guardar las líneas `"Logistica"` en vez de descartarlas.
- Guardar los bloques `DscRcgGlobal` (`TpoMov`, `TpoValor`, `ValorDR`, glosa,
  número de línea) en una tabla nueva `ajustes_globales`.
- Archivar el XML original en vez de permitir su borrado, para que la evidencia
  futura sí sea recuperable.

Esto no arregla el histórico. Evita que el problema siga creciendo, que es lo
único honesto que se puede hacer con datos cuyo original ya no existe.

### 4.2 Capa 2 — la tabla de atribución

Una fila por `(tipo_documento, folio, producto)`:

| Columna | Qué es |
|---|---|
| `neto_producto` | la línea de cerveza, **exacta**, desde `productos` |
| `neto_logistica_atribuido` | la parte de la logística que le toca |
| `neto_total` | la suma — **esto es lo que vale el producto en esa factura** |
| `metodo` | `unica_cerveza` / `reparto_por_litro` / `sin_atribuir` |
| `confianza` | `exacta` / `estimada` / `nula` |
| `calculado_en`, `version_algoritmo` | procedencia |

**Es 100% derivada y recomputable.** Se borra y se recalcula entera en una sola
transacción. No hay ambigüedad de procedencia posible porque **nada aquí
pretende ser una línea del DTE**.

#### Los tres métodos

| Método | Cuándo | Facturas | Monto | Confianza |
|---|---|---:|---:|---|
| `unica_cerveza` | el documento tiene una sola cerveza: toda la logística es suya | **618** | **$58.595.150** | **exacta** |
| `reparto_por_litro` | varias cervezas: se reparte con la regla de `precios_venta.py` (por litro en barriles, por unidad en botellas) | 206 | $36.951.457 | estimada |
| `sin_atribuir` | los 7 documentos donde el ILA no calza, los 2 sin ILA, y los que `precios_venta.py` descarta | ~9 | ~$1,3M | nula — **se reportan, no se inventan** |

**El 75% de las facturas y el 61% del monto se atribuyen de forma exacta**, sin
estimación de ningún tipo. Solo el reparto entre varias cervezas de una misma
factura es estimado, y queda marcado como tal.

#### Invariante que valida cada documento

```
Σ neto_total (del documento)  +  PET  +  CO2  ==  ventas.monto_neto
```

Si no cuadra al peso, **el documento completo se marca `sin_atribuir`** y se
reporta. Ninguna fila se emite "a medias".

### 4.3 Capa 3 — la vista canónica

`v_ingreso_producto` es lo único que consultan el agente, el dashboard, la wiki
y la nube. Trae cliente, fecha, cerveza, formato, unidades y **el ingreso neto ya
atribuido**, más la columna `confianza` para que un resultado estimado pueda
declararse como tal.

Esto responde el P0-2 de la auditoría: la consulta ingenua

```sql
SELECT cliente, SUM(neto_total) FROM v_ingreso_producto WHERE cerveza='Cream Ale'
```

acierta **sin** que quien la escribe sepa nada del doble renglón. No hay filas
separadas que buscar ni asociaciones que adivinar.

---

## 5. Cómo responde a cada hallazgo de la auditoría

| Hallazgo | Respuesta |
|---|---|
| **P0** El residual no siempre es logística (folio 4746) | Ya no se usa el residual para nada. El corte sale del ILA declarado al SII, y el descuento global deja de importar porque no se reconstruye ninguna línea |
| **P0** Filas separadas no arreglan el SQL por producto | La capa 3 entrega el ingreso **ya atribuido** por producto. No hay que asociar nada |
| **P0** La cifra de NC mezclaba signos | Corregido: $3.420.957. Las NC se atribuyen con el mismo método (50 de 52 cumplen la identidad del ILA) y conservan su signo negativo |
| **P1** Cerrar el importador exige guardar los ajustes globales | Incluido en §4.1, junto con archivar el XML |
| **P1** Falta migrar el esquema remoto | **`productos` no se altera**, así que el sync actual no se rompe. La tabla y la vista nuevas se agregan a la replicación de forma deliberada, con la migración remota antes del primer sync |
| **P1** El DDL no implementaba la procedencia | La capa 2 es enteramente derivada, con `metodo`, `confianza`, `version_algoritmo` y `calculado_en`. Se recalcula completa; no hay estado parcial que reconciliar |
| **P1** `precios_venta.py` excluye NC y facturas ajustadas | Cierto, y es una limitación real — ver §6 |

---

## 6. Limitaciones que esta propuesta NO resuelve

Se declaran para que la auditoría las evalúe, no para minimizarlas.

1. **El histórico sigue sin evidencia.** El detalle original de 860 documentos se
   perdió con los XML. Esta propuesta atribuye ingreso; **no recupera lo que
   decía la factura**. Es irreversible y ninguna migración lo cambia.

2. **`precios_venta.py` excluye NC y facturas ajustadas** (`tipo_documento != 61
   AND monto_neto_ajustado IS NULL`). Para las 206 facturas del método
   `reparto_por_litro`, hay que extenderlo o marcarlas `sin_atribuir`. **Aún no
   está decidido cuál**, y es materia de la pregunta 3 de §8.

3. **El reparto entre varias cervezas no es verificable contra la cabecera** —
   cualquier reparto suma lo mismo. Es la pregunta 4 de la auditoría anterior y
   **sigue sin una respuesta buena**. Lo único que se ofrece es marcarlo
   `estimada` para que nadie lo confunda con un hecho.

4. **7 facturas ($1,05M) no cumplen la identidad del ILA** y no se ha
   investigado por qué. Deben revisarse una por una antes de ejecutar nada.

---

## 7. Plan de verificación

1. **Respaldo restaurable verificado** antes de tocar nada (existe backup
   diario; se probará la restauración, no solo su existencia).
2. **La capa 2 se calcula primero en una base de pruebas** y se compara contra
   los totales de `ventas`: la suma atribuida por documento debe cuadrar al peso
   en el 100% de los documentos emitidos.
3. **Cuadratura global:** `Σ v_ingreso_producto.neto_total + PET + CO2 + sin_atribuir`
   debe igualar `Σ ventas.monto_neto`. Es la prueba que la propuesta anterior no
   tenía.
4. **Las 7 facturas anómalas** revisadas manualmente y documentadas.
5. **Auditoría de consumidores:** todo lo que hoy lee `productos` para calcular
   dinero (dashboard, wiki, nube, herramientas del agente) pasa a la vista
   canónica, con test.
6. **Comparación contra el caso conocido:** el ranking de Cream Ale 30L de 2026
   debe dar $10,8M y no $3,5M, y el orden debe poner a A&C primero.

---

## 8. Preguntas para el auditor

1. **¿La identidad del ILA es sólida como base de atribución?**
   `cerveza = impuesto_adicional / 0,205`, verificada en 815 de 824 facturas y 50
   de 52 NC. ¿Hay algún caso del formato DTE chileno —exenciones, ILA a tasa
   distinta, productos afectos mezclados— que la rompa y que estos datos no
   muestren por ser un solo negocio con un catálogo acotado?

2. **¿Es correcto no separar el descuento global?** El argumento es que para
   atribuir *ingreso* basta `monto_neto` (que ya viene neto) y que separar el
   descuento solo importaría para reconstruir la *evidencia*, que de todos modos
   se perdió. ¿Se pierde algo relevante —tributario, o de análisis— al no
   distinguirlo?

3. **Las 206 facturas de varias cervezas.** ¿Extender `precios_venta.py` para
   cubrir NC y facturas ajustadas, o marcarlas `sin_atribuir` y aceptar un 39%
   del monto sin atribuir por producto? La segunda es más honesta; la primera es
   más útil. ¿Cuál pesa más aquí?

4. **La verificación del reparto** sigue sin solución (§6.3). ¿Existe alguna
   prueba independiente para el reparto entre productos de una misma factura, o
   la respuesta correcta es que **no se puede verificar** y por eso debe quedar
   marcado como estimado para siempre?

5. **Orden de ejecución.** ¿Corresponde hacer esto antes o después de los pasos
   2 y 3 del roadmap acordado (tope de gasto y failover de proveedor)? La
   corrección barata e inmediata —impedir que el agente calcule dinero desde
   `productos`— no requiere ninguna migración y podría ir primero.

---

## 9. Lo que se puede hacer hoy sin migrar nada

Independiente de esta propuesta, y clasificable como **protección** en el marco
acordado:

- Prohibir en el prompt del agente calcular montos por producto desde
  `productos`, con la explicación de por qué la cifra sale a un tercio.
- Que `v_ventas_producto` (local) y el prompt de la nube adviertan que su
  resultado es en unidades y no en dinero.

Esto no arregla el dato, pero **cierra hoy el modo de falla** que hizo reportar
$3,5M donde había $10,8M.
