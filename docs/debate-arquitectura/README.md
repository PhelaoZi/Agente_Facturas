# Debate de arquitectura — historial completo

Registro cronológico de una revisión arquitectónica del agente de Zigurat, hecha
por varias IA que se auditan entre sí. **Está pensado para que una IA externa
pueda leerlo completo y auditar tanto las conclusiones como el proceso.**

---

## Cómo leer esto

Los archivos van numerados en orden cronológico. Cada uno responde al anterior.
La regla que se siguió: **cada documento es autocontenido** — incluye el código y
los números que cita, porque quien lo lee no tiene acceso al repositorio.

Los documentos **no están corregidos a posteriori**. Varios contienen
afirmaciones que después resultaron equivocadas, y se dejan tal cual: el valor
del registro está en poder ver dónde falló cada razonamiento y quién lo detectó.

---

## Los participantes

| Quién | Rol | Documentos |
|---|---|---|
| **ChatGPT 5.6** (variante "Luna", según el dueño del proyecto — no confirmado) | Propuesta arquitectónica inicial y sus dos revisiones | 01, 03, 05 |
| **Claude Opus 5** (Claude Code) | Contrainforme, réplica y propuesta de reparación de datos | 02, 04, 06 |
| **ChatGPT 5.6 "Sol"**, vía Codex, **a máximo esfuerzo** | Auditoría externa de la propuesta de reparación | 07 |

**Por qué se anota la variante del modelo.** El documento 07 es el único que
encontró un contraejemplo verificable —un XML real que refuta el supuesto
central del documento 06— y lo hizo revisando el formato oficial del SII y
abriendo el archivo, no razonando sobre lo que el sistema debería contener. Fue
también el que corrió a máximo esfuerzo.

Es una sola observación y no prueba una regla, pero queda registrada porque es
justo el tipo de dato que una auditoría posterior querría tener: **qué modelo,
con cuánto esfuerzo, produjo cuál calidad de hallazgo.**

---

## El hilo

### Parte 1 — Arquitectura del runtime del agente (docs 01 a 05)

| # | Autor | Documento | Tesis |
|---|---|---|---|
| 01 | ChatGPT | Especificación de mejora | Convertir el proyecto en un Agent Runtime agnóstico al modelo: 8 fases, 8 módulos nuevos |
| 02 | Claude | Contrainforme | La mayoría de las fases no aplican; 3 problemas reales que la propuesta no ve |
| 03 | ChatGPT | Informe de revisión | Acepta ~80%. Se equivoca en el orden de una tarea y no responde la pregunta sobre frameworks |
| 04 | Claude | Réplica | Ese orden habría enterrado un bug de negocio; se repite la pregunta sin responder |
| 05 | ChatGPT | Respuesta a preguntas abiertas | Responde las 4. Retira la propuesta de framework. **Mejora** el criterio de decisión |

**Resultado: convergieron.** Ver "Acuerdos" abajo.

### Parte 2 — Reparación de datos (docs 06 a 10)

| # | Autor | Documento | Resultado |
|---|---|---|---|
| 06 | Claude | Propuesta de reparar las líneas de logística | Migración sobre datos históricos de producción |
| 07 | ChatGPT "Sol" | Auditoría externa | **NO-GO.** Encontró un contraejemplo que refuta el supuesto central |
| 08 | Claude | Propuesta revisada de atribución | Acepta el NO-GO, retira el doc 06, propone tres capas y la identidad del ILA |
| 09 | ChatGPT "Sol" | Respuesta a la propuesta revisada | **NO-GO al algoritmo, GO por fases.** Siete objeciones nuevas |
| 10 | Christian | **Cierre y decisión** | Se acepta el NO-GO. Qué se hace, qué no, y qué queda mal a sabiendas |

El contraejemplo del documento 07, verificado después de forma independiente:

```
Folio 4746 — MntNeto: $81.000
  Detalle: Barril 30L Wee Heavy   $35.000
  Detalle: Logistica              $55.000
  DscRcgGlobal  DESCUENTO GLOBAL  D  $9.000
```

La fórmula del documento 06 (`residual = MntNeto − líneas guardadas`) habría
reconstruido una logística de **$46.000** donde la real era de **$55.000**. El
filtro de "precio plausible" que proponía como control tampoco lo habría
detectado: un descuento puede correr el residual justo encima de otro precio
válido.

---

## Acuerdos alcanzados

### Criterio de decisión (propuesto por Claude, mejorado por ChatGPT)

```
CORRECCIÓN   → se arregla al descubrirse, con test de regresión
PROTECCIÓN   → se implementa si cierra un modo de falla real, sin exigir ROI
OPTIMIZACIÓN → hipótesis → métrica → experimento → adoptar/descartar
```

Nació de una discrepancia concreta: "medir antes de optimizar" es correcto,
"medir antes de corregir" no lo es. ChatGPT agregó la categoría del medio.

### Frameworks

Ni adoptar uno externo ni construir uno interno. Se mantiene el loop propio
(~620 líneas) y se extraen piezas solo cuando una necesidad concreta lo
justifique.

### Roadmap

| # | Paso | Estado |
|---|---|---|
| 1 | Telemetría de tokens, caché, latencia y costo | ✅ implementado |
| 2 | Tope de gasto diario + poda del historial | pendiente |
| 3 | Clase `Proveedor` con failover | pendiente |
| 4 | Dos semanas de datos reales sin tocar nada | pendiente |
| 5 | Unificar precio/margen entre escritorio y nube | pendiente |
| 6 | Benchmark v0 (15 tareas con verdad calculable) | pendiente |
| 7 | Experimentos de optimización | pendiente |

Pospuestos hasta tener datos: tool registry dinámico, paralelización de tools,
compaction con LLM, router automático de modelos.

---

## Qué se aprendió (lo que un auditor debería mirar)

**Tres veces un documento afirmó algo que la evidencia después refutó.** Ese es
el patrón más útil de este registro:

1. **ChatGPT (doc 01)** propuso 8 fases de arquitectura. Medir el código mostró
   que varias resolvían problemas inexistentes y una podía empeorar el costo.
2. **ChatGPT (doc 03)** recomendó no eliminar una dependencia antes de
   instrumentar. Al eliminarla apareció un defecto que ninguna métrica habría
   detectado: el agente respondía $33.205.652 cuando la cifra real era
   $113.013.363.
3. **Claude (doc 06)** afirmó que el residual de una factura equivale a la
   logística ausente, con evidencia estadística. La auditoría (doc 07) encontró
   un XML real —folio 4746— con un descuento global que refuta la igualdad.

En los tres casos falló lo mismo: **razonar sobre lo que el sistema *debería*
hacer en vez de leer lo que hace.** Y en los tres, lo que resolvió la discusión
fue un dato concreto, no un argumento.

---

---

## Estado al 2026-08-10 — CERRADO

El debate está cerrado. **No hay documento 11.** Lo decidido está en el
documento 10 y de ahí en adelante se ejecuta, no se discute.

- Parte 1 cerrada, con roadmap acordado. Paso 1 (telemetría) implementado.
- Parte 2 cerrada por decisión, tras dos NO-GO consecutivos.
- **Paso 1 de la reparación hecho el 2026-08-10:** ninguno de los dos agentes
  entrega ya dinero por producto desde el detalle incompleto; tres tests lo
  fijan. Falta el deploy de la función a la nube.
- Pasos 2 a 4 (dejar de perder evidencia, atribución histórica, conectar
  consumidores) definidos en el documento 10 con su criterio de terminado.
- La base de datos no fue modificada en ningún momento de este debate.

---

## Integridad del registro

Los siete documentos son **los originales**, tal como los escribió cada modelo.
Ninguno fue reconstruido desde la conversación ni editado después: solo se les
cambió el nombre de archivo para numerarlos en orden.

Los tres de ChatGPT (01, 03, 05) los aportó el dueño del proyecto desde su
conversación original, precisamente para que el registro no quedara con un solo
lado del debate.
