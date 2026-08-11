# Cierre del debate y decisión de qué se hace

**Fecha:** 2026-08-10
**Decide:** Christian de la Fuente (dueño del proyecto)
**Redacta:** Claude Opus 5 (Claude Code)
**Cierra:** el intercambio abierto en `01-…` y continuado hasta `09-…`

---

## 1. El debate se cierra acá

No hay documento 11. Las nueve preguntas del documento 09 no se responden en
otra ronda: se responden haciendo o no haciendo cada cosa, y eso queda escrito
abajo.

El intercambio ya rindió lo que tenía que rendir. Tres veces un documento
afirmó algo que la evidencia después refutó, y las tres veces lo que zanjó fue
un dato, no un argumento. Seguir discutiendo a esta altura ya no está
produciendo datos nuevos.

---

## 2. Qué quedó resuelto

**El NO-GO del documento 09 se acepta.** Verifiqué sus siete objeciones contra
la base antes de aceptarlas. Cinco quedaron confirmadas:

| Objeción | Verificación |
|---|---|
| Los 7 desajustes del ILA son descuentos | Confirmado, y más fuerte de lo que planteó: los 7 caen en porcentajes redondos (10%, 33,3%, 50%, 10%, 10%, 20%, 10%) |
| `productos.total_linea` es bruto pre-descuento | Confirmado — se sigue de lo anterior |
| Los signos de las NC son inconsistentes | Confirmado: 40 con ILA positivo, 12 negativo, con las 62 líneas positivas y `monto_neto` negativo |
| `(tipo_documento, folio, producto)` no identifica una línea | Confirmado: folio 4344 tiene dos filas `Barril 30L Cream Ale` |
| El prompt de la nube seguía abierto | Confirmado |

Dos resultaron más débiles de lo planteado, y queda registrado por honestidad
del expediente, no para reabrir nada:

- **El redondeo del ILA.** La objeción es correcta en el principio y no cambia
  ninguna cifra: validando hacia adelante —`redondeo(base × 0,205)` contra el
  ILA declarado— la partición es idéntica, **815 exactos, 0 en la banda de ±$1,
  7 fuera**, sobre 822 facturas. Se adopta igual el encuadre de validación
  hacia adelante, porque es el correcto.
- **El clasificador por descarte.** Sus dos contraejemplos son reales
  (`Arriendo maquina schopera` $59.000 folio 4354, `Malta.Boortmalt.Pilsen 25`
  $162.918 folio 4447), pero son **exactamente las dos únicas facturas con
  ILA = 0**: el control del ILA ya los detecta. Es una falla detectada, no
  silenciosa. Se arregla igual.

**Un dato propio que sí cambia el plan:** hay **123 nombres distintos** en
`productos`, con erratas abundantes (`Baril`, `Balck IPA`, `Scoth Ale`,
`Sctout Cafe`, `Barril 30L Scotch Ale Ale`, `Botella 33cc`). El
"reconocimiento positivo con categoría desconocida" es la regla correcta, pero
no sale de una expresión regular: hay que revisar y mapear los 123 a mano. Ese
trabajo no estaba presupuestado en ninguna de las dos propuestas.

---

## 3. Lo que se va a hacer

### Paso 1 — Cerrar el modo de falla · **HECHO hoy**

El agente ya no entrega dinero por producto sacado del detalle, en ninguno de
los dos canales: prompt del PC (`app/agent/system_prompt.py`) y prompt del
teléfono (`functions/_shared/chat_prompt.ts`). Por producto, el detalle sirve
para unidades; si preguntan pesos y ninguna herramienta lo cubre, el agente
dice que no tiene la cifra confiable y ofrece las unidades.

Tres tests lo fijan (`tests/test_prompt_dinero_por_producto.py`). Suite: 530
en verde.

Falta el despliegue de la función a la nube para que tome efecto en el
teléfono.

**Terminado cuando:** ninguna interfaz responde plata por producto desde el
detalle incompleto. *(Local sí; nube pendiente de deploy.)*

### Paso 2 — Dejar de perder evidencia

`parse_dte.py` pasa a guardar todo lo que hoy tira: las líneas `Logistica`
(las que se llaman así a secas), `DscRcgGlobal`, los impuestos por línea con
su tipo y tasa, y `NroLinDet`. El XML se archiva con su hash antes de
considerarlo procesado.

Esto no arregla el histórico. Corta la hemorragia: desde el próximo DTE el
problema no vuelve a generarse.

**Terminado cuando:** un DTE nuevo se puede auditar completo contra su XML
archivado.

### Paso 3 — Atribución histórica, conservadora

Tabla derivada, recalculable de cero, que no toca `ventas` ni `productos`.
Cada fila declara `fuente`, `metodo` y `calidad`, e identifica la línea por
`productos.id`.

Reglas:

- El ILA se usa como **control hacia adelante**, nunca como división.
- Clasificación **positiva**, con los 123 nombres mapeados a mano. Lo
  desconocido queda `desconocida` y **nunca** se convierte en cerveza.
- Una sola cerveza, sin pass-through, con el ILA calzando → todo el neto del
  documento al producto, `calidad = deterministica`.
- Varias cervezas → reparto por litros, `calidad = estimada`.
- Descuento detectado (los 7 folios), línea desconocida o ILA = 0 →
  `no_atribuido`. Sin excepciones.
- Notas de crédito como eventos negativos, con el signo derivado del tipo de
  documento y **nunca** del signo guardado.
- Invariante: lo atribuido de un documento suma su neto, o el documento entero
  queda sin atribuir. No se publican fragmentos.

Vista `v_ingreso_producto` como única fuente de dinero por producto, y cada
respuesta declara período y cobertura.

**Terminado cuando:** el ranking de Cream Ale 30L da la cifra correcta,
cuadra contra `MntNeto` documento por documento, y la cobertura estimada
aparece marcada como tal.

### Paso 4 — Conectar los consumidores

Dashboard, wiki, agente del PC y agente del teléfono pasan a la vista
canónica. Recién ahí se levanta la prohibición del paso 1.

---

## 4. Lo que NO se va a hacer, y por qué

Del plan del documento 09 se descarta la ceremonia de despliegue: modo sombra,
versión activa con puntero, banderas de activación, ensayos de rollback,
verificación de paridad como fase propia.

La razón es de proporción. La tabla de atribución se recalcula entera en
segundos, a partir de datos que nadie modifica; el rollback es volver a correr
el script. Zigurat produce 2.500 litros al mes y el sistema lo opera una
persona. Ese aparato protege contra un riesgo que acá no existe, y su costo
real es que el paso 3 no se termine nunca.

Se conservan, eso sí, las piezas que sí cierran fallas reales: el conjunto de
casos conocidos —incluidos los 7 folios con descuento, los 2 con ILA = 0 y el
folio 4746 con XML—, las invariantes de cuadratura, y que ningún documento se
publique a medias.

**Tampoco se reparsea el histórico.** Quedan 2 XML de 876 documentos (1,8%).
No hay de dónde.

---

## 5. Lo que se sabe que queda mal

Se escribe para que nadie lo descubra después creyendo que es un hallazgo:

- El reparto entre varias cervezas de una misma factura **no es verificable**
  con los datos que hay. Cualquier distribución cuadra igual contra la
  cabecera. Va marcado como estimado de forma permanente.
- Los 6 folios con descuento que no tienen XML no se van a poder confirmar.
  Quedan sin atribuir.
- Los 123 nombres se mapean a mano una vez. Cada nombre nuevo que invente el
  productor va a caer en `desconocida` hasta que alguien lo mapee. Es
  deliberado: es preferible a que se cuele como cerveza.

---

## 6. Estado

| Paso | Estado |
|---|---|
| 1 — Cerrar el modo de falla | hecho local, falta deploy a la nube |
| 2 — Dejar de perder evidencia | siguiente |
| 3 — Atribución histórica | después del 2 |
| 4 — Conectar consumidores | después del 3 |

La base de datos no fue modificada en ningún momento de este debate.
