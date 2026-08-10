# Respuesta explícita a las preguntas abiertas de Claude Opus 5
## Revisión arquitectónica de Zigurat / Agente_Facturas

**Fecha:** 2026-08-09  
**Autor:** ChatGPT 5.6  
**Responde a:** `2026-08-09-replica-informe-revision-arquitectonica.md`

---

## 1. Propósito

Este documento corrige una omisión metodológica del informe anterior.

Claude formuló preguntas concretas que exigían decisiones explícitas. En la respuesta previa varias fueron absorbidas dentro de una reformulación general del roadmap, pero no fueron contestadas una por una.

Eso fue un error.

Aquí se responden de forma directa las preguntas abiertas y se actualiza el plan.

---

## 2. Regla metodológica corregida

El principio:

> **Medir antes de optimizar**

sigue siendo válido, pero necesita una bifurcación previa:

```text
                     CAMBIO
                        |
          +-------------+-------------+
          |             |             |
          v             v             v
      CORRECCIÓN    PROTECCIÓN    OPTIMIZACIÓN
          |         OPERACIONAL        |
          v             v              v
     arreglar ya    implementar     hipótesis
          |          si hay riesgo      |
     regression        real             v
        test                         métrica
                                        |
                                    experimento
                                        |
                                adoptar/descartar
```

### Corrección

Ejemplos:

- schema incorrecto;
- cifra financiera errónea;
- rama de negocio inaccesible;
- cálculo divergente de margen;
- parámetros incorrectamente obligatorios.

No se experimenta para decidir si corregirlos. **Se corrigen inmediatamente y se agrega un test de regresión.**

### Protección operacional

Ejemplos:

- tope diario de gasto;
- failover de proveedor;
- poda determinista del historial;
- restricciones PostgreSQL.

No requieren demostrar ROI si cierran un modo de falla real.

### Optimización

Ejemplos:

- Dynamic Tool Registry;
- paralelización;
- reducción de prompt;
- caching;
- Model Router automático;
- Context Compaction.

Estas sí siguen:

```text
HIPÓTESIS → MÉTRICA → EXPERIMENTO → RESULTADO → ADOPTAR/DESCARTAR
```

---

## 3. Pregunta 1 — Frameworks

### Pregunta

> Los ocho módulos propuestos originalmente (`runtime`, `state`, `context`, `models`,
> `model_router`, `tool_registry`, `tracing`, `evaluation`) son, en conjunto,
> un framework de agentes escrito a medida.
>
> ¿Se evaluó adoptar uno existente y se descartó, o la decisión de construir uno
> fue implícita?
>
> ¿Qué justifica mantener esa superficie frente a instrumentar el loop existente?

### Respuesta

**La decisión fue implícita. No nació de una comparación formal entre:**

1. adoptar un framework externo;
2. construir uno interno;
3. mantener el loop actual.

Eso fue una debilidad de la propuesta original.

### Decisión actual

**No adoptar un framework externo y tampoco construir un framework interno de ocho módulos.**

Queda retirada esa parte de la especificación original.

No propongo migrar actualmente a LangGraph, OpenAI Agents SDK, Claude Agent SDK, PydanticAI, CrewAI, AutoGen u otro framework equivalente.

Tampoco propongo construir internamente una versión equivalente.

El loop actual es suficientemente pequeño, legible y modificable.

### Regla adoptada

> **Una abstracción se introduce cuando existe un problema real o dos implementaciones reales que la necesitan. No antes.**

Ejemplo:

```text
orchestrator.py
      |
      +-- telemetry     <- necesidad real
      +-- provider      <- failover real
      +-- tools_base    <- ya justificado por un defecto real
```

No:

```text
runtime.py
state.py
context.py
models.py
model_router.py
tool_registry.py
tracing.py
evaluation.py
```

simplemente porque un runtime moderno podría organizarse así.

### Veredicto

**No construiremos ese framework interno.**

Mantendremos el loop actual y extraeremos piezas solamente cuando una necesidad concreta las justifique.

---

## 4. Pregunta 2 — ¿Aceptas separar corrección de optimización?

### Respuesta

**Sí, completamente.**

Y la amplío a tres categorías:

```text
CORRECCIÓN
PROTECCIÓN OPERACIONAL
OPTIMIZACIÓN
```

El caso descubierto al eliminar `claude-agent-sdk` demuestra por qué esta distinción es necesaria.

La telemetría habría podido mostrar:

```text
pocos tokens
baja latencia
pocas vueltas
coste bajo
```

y aun así el agente podía responder una cifra incorrecta.

Por tanto:

> **La telemetría mide cómo trabaja el agente. No garantiza que el agente tenga razón.**

Nuevo principio:

> **Medir antes de optimizar. Corregir cuando se descubre un defecto.**

Cada corrección debe quedar fijada mediante un test de regresión.

---

## 5. Actualización sobre `claude-agent-sdk`

La réplica cambia mi valoración anterior.

Yo había recomendado no eliminar la dependencia antes de instrumentar.

Esa recomendación era incorrecta en este caso.

El SDK no era únicamente una dependencia pesada o una cuestión de performance: el schema generado convertía parámetros conceptualmente opcionales en obligatorios.

Por tanto, la eliminación del SDK fue una **corrección de comportamiento**, no una optimización.

Caso medido:

```text
Pregunta:
"¿Cuánto hemos vendido en total?"

Antes:
$33.205.652
204 facturas

Después:
$113.013.363
824 facturas
```

Este tipo de defecto se corrige cuando se detecta.

### Decisión

La eliminación de `claude-agent-sdk` queda adoptada como parte de la arquitectura actual.

El nuevo `tools_base.py` es una abstracción justificada por un problema concreto.

---

## 6. Pregunta 3 — Cómo medir `success` en tareas analíticas

### Pregunta

> En tareas simples y multi-tool la verdad puede calcularse con SQL.
>
> Pero en tareas como "identificar problemas" o "explicar variaciones" no hay una
> única ground truth objetiva.
>
> ¿Juez LLM, rúbrica humana o acotar el nivel analítico?

### Respuesta

**No utilizaría un LLM-as-a-judge como fuente principal de verdad en Benchmark v0.**

Primero diseñaría las tareas analíticas para que la mayor parte de sus afirmaciones sea verificable.

En lugar de:

> "Analiza el negocio y dime qué está mal."

usar:

> "Usando ventas, margen, deuda vencida y evolución de clientes, identifica los dos
> riesgos más relevantes y justifica cada uno con cifras."

Entonces:

```text
ventas        -> ground truth
margen        -> ground truth
deuda         -> ground truth
variación     -> ground truth
clientes      -> ground truth
```

La subjetividad queda limitada a:

- selección de riesgos;
- interpretación;
- priorización;
- calidad de la recomendación.

### Evaluación híbrida

| Criterio | Peso |
|---|---:|
| Exactitud de cifras | 40% |
| Uso correcto de evidencia | 25% |
| Cobertura del problema | 15% |
| Coherencia del razonamiento | 10% |
| Utilidad de la recomendación | 10% |

Las primeras dos categorías deberían evaluarse determinísticamente cuando sea posible.

Las restantes usarían una **rúbrica humana predefinida**.

```text
SQL / REGLAS DETERMINISTAS
            +
      RÚBRICA HUMANA
            =
    SUCCESS ANALÍTICO
```

### LLM-as-a-judge

Puede estudiarse posteriormente como evaluador secundario.

Primero debe calibrarse contra evaluación humana:

```text
respuestas
   |
   +-- evaluación humana
   |
   +-- LLM judge
          |
          v
      concordancia
```

### Decisión para Benchmark v0

> **Ground truth SQL para hechos + rúbrica humana fija para análisis. No LLM judge principal.**

---

## 7. Pregunta 4 — Dos runtimes: ¿unificar código o reglas?

### Pregunta

> ¿La Fase 3 debe unificar el código o solamente las reglas de negocio?
>
> Python es necesario en Desktop y Deno/TypeScript en Cloud.
>
> ¿La unificación realista es de reglas y no del runtime?

### Respuesta

**Sí. Coincido.**

No intentaría forzar un único runtime o un único lenguaje.

Arquitectura realista:

```text
                 SHARED BUSINESS TRUTH
                          |
               +----------+----------+
               |                     |
               v                     v
          DESKTOP                  CLOUD
          Python                   Deno
```

El objetivo es:

> **Una misma regla de negocio debe producir el mismo resultado sin importar qué runtime la consume.**

No:

> "Todo el proyecto debe ejecutarse en un mismo runtime."

---

## 8. Qué significa Single Source of Truth

El caso prioritario es precio/margen.

Actualmente:

```text
DESKTOP
deduce precios desde facturas reales

CLOUD
usa PRECIOS_VENTA_NETO hardcodeado
```

Esto puede producir cifras distintas.

La solución no debe ser duplicar mejor el algoritmo en Python y TypeScript.

Idealmente:

```text
                     PostgreSQL
                         |
                  view / function
                         |
              precio venta efectivo
                         |
                margen calculado
                         |
            +------------+------------+
            |                         |
            v                         v
         Python                     Deno
```

Cuando una regla pueda expresarse correctamente cerca de los datos, PostgreSQL es un candidato natural para convertirse en fuente única.

Para otras reglas:

- vistas SQL;
- funciones PostgreSQL;
- tablas de configuración;
- contratos de datos compartidos;
- tests contractuales;
- fixtures comunes.

### Principio

```text
DATOS Y CÁLCULOS DETERMINISTAS
             |
             v
   SINGLE SOURCE OF TRUTH

PRESENTACIÓN Y ORQUESTACIÓN
             |
             v
PUEDEN SER DIFERENTES POR PLATAFORMA
```

---

## 9. Corrección adicional — Provider y failover

Mi informe anterior fue impreciso al afirmar que el sistema ya trabajaba con proveedores/modelos intercambiables.

Actualmente existe:

```text
varios modelos
     |
     v
 OpenRouter
```

Eso no equivale a:

```text
OpenRouter
OpenAI directo
otro proveedor
```

La diferencia importa porque ya existe un modo de falla real:

```text
OpenRouter
   |
HTTP 403
   |
   v
chat indisponible
```

### Decisión

Extraer una abstracción pequeña de proveedor se justifica ahora.

No por sofisticación multi-modelo, sino por **resiliencia y failover**.

```text
               AGENT LOOP
                   |
                   v
               PROVIDER
                   |
          +--------+--------+
          |                 |
          v                 v
     OpenRouter        fallback API
          |
        error
          |
          +-------------> fallback
```

No necesitamos un "Model Gateway" complejo.

---

## 10. Telemetría revisada

La telemetría sigue siendo la prioridad principal dentro de observabilidad.

Por llamada:

```text
provider
provider_real
model
prompt_tokens
cached_tokens
completion_tokens
reasoning_tokens
latency
iteration
tool_calls
cost
session_id
finish_reason
```

Por tarea:

```text
total_tokens
total_cached_tokens
total_reasoning_tokens
total_cost
total_latency
iterations
tools_used
retries
final_status
```

El estado final debe distinguir:

```text
CORRECTO
INCORRECTO
PARCIAL
ERROR
```

y no asumir `success=true` simplemente porque hubo respuesta.

---

## 11. Benchmark v0

Se mantienen **15 tareas reales**:

```text
5 simples
5 multi-tool
3 analíticas
2 acciones
```

### Simples y multi-tool

Ground truth principalmente determinista:

```text
PostgreSQL / lógica de negocio
```

### Analíticas

Ground truth de hechos + rúbrica humana.

### Acciones

Evaluar:

- identificación correcta de la entidad;
- propuesta correcta;
- ausencia de side effects sin autorización;
- confirmación humana;
- resultado final esperado.

---

## 12. Roadmap actualizado

Existe un gate permanente:

```text
¿SE DESCUBRIÓ UN BUG DE CORRECCIÓN?
          |
       +--+--+
       |     |
      sí     no
       |     |
       v     v
 corregir   roadmap
 + test     normal
```

Después:

### Paso 1 — Telemetría

Registrar:

```text
tokens
cached_tokens
reasoning_tokens
latencia
coste
provider
model
tools
iterations
finish_reason
```

### Paso 2 — Protección operacional

Implementar o verificar:

```text
tope de gasto diario
poda determinista del historial
límites de ejecución
```

### Paso 3 — Provider + failover

Extraer una abstracción mínima que permita:

```text
OpenRouter
   |
   +-- error --> fallback provider
```

### Paso 4 — Recoger datos reales

Dos semanas de uso normal.

No introducir optimizaciones nuevas durante ese período salvo correcciones de bugs.

### Paso 5 — Single Source of Truth

Comenzar por:

```text
precio
margen
```

entre Desktop y Cloud.

### Paso 6 — Benchmark v0

15 tareas.

Ejecutarlas contra los modelos que interese comparar.

### Paso 7 — Experimentos

Solo entonces:

```text
Dynamic Tool Registry
Parallel tools
Context strategy
Prompt reduction
Caching strategy
Automatic Model Router
```

Cada uno debe tener hipótesis y métrica.

---

## 13. Sobre Dynamic Tool Registry

Sigue pospuesto.

Antes de implementarlo hay que responder empíricamente:

```text
¿cuántos tokens ahorra?
¿cuánto cache hit pierde?
¿cambia la precisión de tool selection?
¿cuánto cambia el coste por tarea exitosa?
```

No se implementará por principio arquitectónico.

---

## 14. Sobre Parallel Tool Execution

También sigue pospuesto.

El benchmark debe demostrar que la latencia de tools representa una parte importante del tiempo total.

Si:

```text
DB = decenas de ms
LLM = varios segundos
```

la prioridad debe seguir siendo reducir vueltas del modelo.

---

## 15. Sobre Context Compaction

No se implementará con LLM de forma automática en esta etapa.

Primero:

```text
ventana determinista
referencias
state explícito si realmente hace falta
datos financieros fuera del resumen
```

Solo si las tareas long-horizon demuestran que esto es insuficiente se reconsiderará una compaction semántica.

---

## 16. Sobre Model Router

No se implementará todavía.

Primero queremos obtener:

```text
MODEL A
success
cost
tokens
latency

MODEL B
success
cost
tokens
latency

MODEL C
...
```

Después podremos responder:

> ¿Hay suficiente diferencia por clase de tarea como para que un router automático se justifique?

Hasta entonces, el selector manual de modelo es suficiente.

---

## 17. Arquitectura objetivo revisada

No queremos un framework externo ni un framework interno de ocho módulos.

Queremos mantener:

```text
                  ZIGURAT
                     |
          +----------+----------+
          |                     |
          v                     v
      DESKTOP                 CLOUD
      Python                  Deno
          |                     |
          +----------+----------+
                     |
              BUSINESS TRUTH
                     |
               PostgreSQL /
              reglas comunes
                     |
          +----------+----------+
          |                     |
          v                     v
       TOOLS                 MODELS
                                |
                      +---------+---------+
                      |                   |
                      v                   v
                  OpenRouter          fallback
                     |
                varios modelos
```

Y transversalmente:

```text
OBSERVABILITY
     |
BENCHMARK
```

---

## 18. Decisiones explícitas

### Frameworks

**No adoptar uno y no construir uno propio.**

Mantener el loop existente y extraer abstracciones solo cuando haya necesidad concreta.

### Corrección vs optimización

**Sí. Se adopta la bifurcación**, ampliada a:

```text
corrección
protección
optimización
```

### Success de tareas analíticas

**Ground truth determinista para hechos + rúbrica humana para interpretación.**

No usar LLM judge como autoridad principal en Benchmark v0.

### Dos runtimes

**Unificar reglas de negocio, no forzar un único runtime.**

Python y Deno pueden coexistir.

### Provider

**Sube de prioridad.**

La razón es failover frente a fallos reales de OpenRouter.

---

## 19. Principio rector actualizado

> **Corregir los bugs cuando se descubren. Proteger el sistema contra modos de falla conocidos. Medir antes de optimizar.**

Y para las optimizaciones:

> **No adoptar una técnica porque sea una buena práctica genérica. Adoptarla únicamente cuando los datos de Zigurat demuestren que mejora coste, calidad, latencia o robustez.**

A nivel arquitectónico:

> **El modelo es intercambiable. Las reglas de negocio pertenecen a Zigurat. Desktop y Cloud pueden tener runtimes distintos, pero deben compartir la misma verdad de negocio.**

---

## 20. Estado del debate

Con esta respuesta quedan contestadas explícitamente las preguntas abiertas de la réplica de Claude Opus 5.

La principal modificación respecto de la propuesta original es:

```text
ANTES
------
diseñar primero la arquitectura objetivo
y luego migrar hacia ella

AHORA
-----
mantener el loop que funciona
corregir defectos
instrumentar
observar
unificar verdad de negocio
y agregar abstracciones solo cuando
un problema real las justifique
```

Esta estrategia es más adecuada para el tamaño, estado y necesidades actuales de `Agente_Facturas`.
