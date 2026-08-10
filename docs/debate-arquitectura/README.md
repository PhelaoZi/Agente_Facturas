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

| Quién | Rol |
|---|---|
| **ChatGPT 5.6** | Propuso la mejora arquitectónica inicial (docs 01, 03, 05) |
| **Claude Opus 5** (Claude Code) | Contrainforme, réplica y propuesta de reparación (docs 02, 04, 06) |
| **Codex, GPT-5.6** | Auditoría externa de la propuesta de reparación (doc 07) |

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

### Parte 2 — Reparación de datos (docs 06 y 07)

| # | Autor | Documento | Resultado |
|---|---|---|---|
| 06 | Claude | Propuesta de reparar las líneas de logística | Migración sobre datos históricos de producción |
| 07 | Codex | Auditoría externa | **NO-GO.** Encontró un contraejemplo que refuta el supuesto central |

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

## Estado al 2026-08-10

- Parte 1 cerrada, con roadmap acordado. Paso 1 implementado.
- Parte 2 **detenida en NO-GO**. La base de datos no fue modificada.
- El defecto de fondo sigue abierto: `parse_dte.py` descarta las líneas
  `"Logistica"` y además no guarda los descuentos globales (`DscRcgGlobal`), así
  que la tabla `productos` no permite reconstruir el ingreso por producto.
- La auditoría propone separar tres capas: evidencia cruda inmutable, atribución
  derivada, y una vista canónica única para agente, dashboard, wiki y nube.

---

## FALTAN LOS TRES DOCUMENTOS DE ChatGPT

Los documentos **01, 03 y 05** los escribió ChatGPT 5.6 y **no están en este
repositorio**: llegaron pegados en la conversación y nunca se guardaron a disco.

Sin ellos el registro tiene solo un lado del debate, que es justo lo que este
historial existe para evitar.

Guardar los originales con estos nombres exactos:

```
01-2026-08-09-chatgpt-especificacion-mejora.md
03-2026-08-09-chatgpt-informe-revision.md
05-2026-08-09-chatgpt-respuesta-preguntas-abiertas.md
```

Se dejan pendientes a propósito en vez de reconstruirlos desde la conversación:
en un registro destinado a auditoría, una transcripción de segunda mano no vale
lo mismo que el original.
