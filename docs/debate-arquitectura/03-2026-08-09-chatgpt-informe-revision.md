# Informe de Revisión Arquitectónica — Zigurat

## Respuesta al análisis de Claude Opus 5

**Proyecto:** Agente_Facturas / Zigurat  
**Propósito:** presentar una revisión crítica y actualizada de la propuesta de mejora, incorporando las objeciones y hallazgos del contrainforme.

---

## 1. Resumen ejecutivo

Después de revisar el análisis de Claude Opus 5, la conclusión es:

> **Claude tiene razón en aproximadamente el 80% de sus objeciones.**

La propuesta inicial era correcta como **dirección arquitectónica de largo plazo**, pero estaba demasiado enfocada en cómo diseñaríamos un Agent Runtime ideal y no suficientemente en qué necesita realmente el repositorio actual.

El contrainforme demuestra que varias de las optimizaciones propuestas:

- ya están implementadas;
- podrían tener un beneficio marginal menor al esperado;
- o no deben implementarse hasta disponer de mediciones objetivas.

Por tanto, la estrategia revisada es:

```text
MEDIR
  ↓
FORMULAR HIPÓTESIS
  ↓
EXPERIMENTAR
  ↓
MEDIR
  ↓
ADOPTAR O DESCARTAR
```

No queremos:

```text
MEDIR
  ↓
IMPLEMENTAR 7 IDEAS
```

El criterio de diseño será la evidencia obtenida del propio agente.

---

## 2. Prioridad número uno: instrumentación económica

El runtime actualmente recibe información de uso del proveedor LLM pero descarta métricas como:

- `prompt_tokens`;
- `completion_tokens`;
- `cached_tokens`.

Esto impide responder con precisión:

- cuánto cuesta una tarea;
- cuánto cuesta una conversación;
- cuánto contexto está siendo cacheado;
- cuánto cuesta cada vuelta;
- qué modelo tiene mejor coste por tarea exitosa.

### Decisión

La Fase 1 debe ser **observabilidad de tokens, caché, coste, latencia y ejecución**, sin cambiar el comportamiento del agente.

---

## 3. Reconocimiento de optimizaciones que ya existen

El análisis de Claude demuestra que el proyecto ya ha resuelto varias optimizaciones que la propuesta inicial trataba como futuras.

Entre ellas:

### Datos grandes fuera del contexto

Las tools de listado publican resultados directamente en la UI y devuelven al modelo solamente un resumen/muestra.

El SQL ad-hoc puede utilizar referencias para no volver a inyectar grandes conjuntos de datos.

Existe una medición documentada:

```text
15.029 → 9.215 tokens
6 → 3 vueltas
```

Esto debe conservarse.

### Sticky routing

El proyecto detectó que OpenRouter podía cambiar de proveedor entre vueltas y eso destruía el beneficio del caché.

Se incorporó `X-Session-Id` para favorecer afinidad de proveedor.

### Publicar y responder en el mismo turno

Se evitó gastar una vuelta adicional solamente para redactar después de publicar un gráfico/tabla.

### Recuperación de errores SQL

Ante errores de columnas inexistentes, la tool proporciona las columnas reales para evitar una nueva ronda innecesaria.

### Turno de cierre

Existe un cierre sin tools cuando se alcanza el límite de iteraciones.

### Conclusión

El proyecto **no parte de un agente ingenuo**. Varias optimizaciones importantes ya fueron implementadas y, en algunos casos, medidas.

---

## 4. Corrección a Dynamic Tool Registry

La propuesta inicial recomendaba:

```text
33 tools
   ↓
selección dinámica
   ↓
5–8 tools
```

Después del análisis de Claude, esta recomendación queda **pospuesta**.

Existe un trade-off:

```text
menos schemas
       ↓
menos tokens
```

versus:

```text
prefijo diferente
       ↓
menor cache hit
       ↓
más coste
```

Por tanto, no se debe asumir que reducir tools siempre reduce el coste total.

### Criterio futuro

Solo implementar Dynamic Tool Registry si un experimento demuestra:

```text
tokens ahorrados
+
coste reducido
+
calidad mantenida
>
pérdida de caché
+
complejidad adicional
```

Hasta entonces se mantiene el conjunto actual y se mide.

---

## 5. Corrección a Context Compaction

La propuesta inicial incluía:

```text
RAW HISTORY
     ↓
LLM COMPACTION
     ↓
STRUCTURED STATE
```

Esta idea queda **pospuesta**.

En un ERP, resumir mediante otro modelo puede introducir errores sobre:

- montos;
- folios;
- fechas;
- identificadores;
- resultados financieros;
- decisiones anteriores.

### Enfoque revisado

Antes de hacer compaction con LLM se debe explorar:

- estado estructurado;
- referencias a resultados;
- ventanas de contexto;
- eliminación determinista de resultados obsoletos;
- almacenamiento de hechos críticos fuera del contexto textual.

La compaction semántica solo se implementará si existe una necesidad demostrada y un mecanismo fiable de preservación de hechos.

---

## 6. Corrección a paralelización

La paralelización de tools independientes sigue siendo técnicamente válida, pero su prioridad baja.

Si una consulta SQL tarda:

```text
50 ms
```

y una vuelta del LLM tarda:

```text
3–11 segundos
```

entonces paralelizar consultas puede ahorrar mucho menos que eliminar una vuelta completa del modelo.

### Decisión

> **Parallel tool execution es una optimización secundaria.**

Se implementará cuando las mediciones demuestren un impacto significativo en latencia.

---

## 7. Corrección al Model Router

La propuesta inicial planteaba:

```text
TASK
 ↓
difficulty estimation
 ↓
cheap / medium / frontier model
```

Esto queda **pospuesto**.

Actualmente el usuario puede seleccionar el modelo y el sistema ya trabaja con proveedores/modelos intercambiables.

Introducir un router automático demasiado pronto agregaría:

- complejidad;
- lógica adicional;
- coste potencial;
- otra fuente de errores.

Primero necesitamos medir:

```text
modelo × tarea
```

con datos reales.

Solo después se decidirá si el routing automático aporta valor.

---

## 8. Descubrimiento arquitectónico importante: existen dos runtimes

El análisis de Claude identifica un problema más importante que varias optimizaciones propuestas originalmente.

Existen dos implementaciones del loop:

```text
Desktop
Python
app/agent/orchestrator.py
```

y:

```text
Cloud / Mobile
TypeScript / Deno
functions/_shared/openai_chat_loop.ts
```

Conceptualmente:

```text
                  ZIGURAT
                     |
          +----------+----------+
          |                     |
       Desktop                Cloud
       Python                TypeScript
          |                     |
       Agent A                Agent B
          |                     |
       Tools A               Tools B
          |                     |
       Rules A               Rules B
```

Esto introduce riesgo de divergencia.

---

## 9. Nueva prioridad: Single Source of Truth

La evolución debe buscar:

```text
                  SHARED BUSINESS CORE
                           |
             +-------------+-------------+
             |                           |
          Desktop                       Cloud
             |                           |
             +-------------+-------------+
                           |
                       Model Layer
```

La lógica crítica no debería existir duplicada en dos implementaciones independientes.

Especial atención a:

- cálculos financieros;
- margen;
- validaciones;
- acceso a datos;
- reglas de negocio;
- tools críticas;
- acciones sensibles.

No es necesario fusionar ambos runtimes en un mismo lenguaje inmediatamente.

La prioridad es **evitar divergencia de lógica de negocio**.

---

## 10. Dependencia residual de Claude Agent SDK

El análisis identifica una dependencia residual de `claude-agent-sdk`.

Si el SDK ya no controla el loop principal y solo se usa para piezas auxiliares, debe evaluarse su eliminación.

Pero:

> **No se debe hacer antes de instrumentar.**

Primero medimos el comportamiento actual; después eliminamos la dependencia de forma controlada.

---

## 11. Benchmark revisado

La propuesta inicial planteaba potencialmente 50–100 tareas.

Eso es demasiado para la primera iteración.

Se propone:

# Zigurat Benchmark v0

**15 tareas reales.**

Distribución:

```text
5  tareas simples
5  tareas multi-tool
3  tareas analíticas
2  tareas con acciones
```

Cada tarea debe definir, cuando sea posible:

```text
expected result
expected tools
expected safety behavior
```

Y cuando sea viable:

```text
ground truth
```

calculada directamente desde PostgreSQL o mediante una referencia verificable.

El objetivo no es crear todavía un framework académico, sino una herramienta práctica para comparar modelos sobre el trabajo real de Zigurat.

---

## 12. Métricas del benchmark

Cada ejecución debe registrar:

```text
model
provider
task
success
iterations
tool_calls
input_tokens
cached_tokens
output_tokens
latency
cost
```

La métrica económica principal será:

> **Coste por tarea correctamente completada.**

No solamente coste por millón de tokens.

Un modelo barato que falla frecuentemente puede ser peor que uno más caro que resuelve las tareas correctamente.

---

## 13. Arquitectura revisada de corto plazo

La arquitectura debe mantenerse deliberadamente simple:

```text
                     ZIGURAT
                        |
              +---------+---------+
              |                   |
       DESKTOP RUNTIME       CLOUD RUNTIME
              |                   |
              +---------+---------+
                        |
                 SHARED BUSINESS
                      LOGIC
                        |
          +-------------+-------------+
          |             |             |
         TOOLS        STATE           DB
          |             |             |
          +-------------+-------------+
                        |
                   MODEL LAYER
                        |
        +---------------+---------------+
        |               |               |
      OpenAI        OpenRouter      Other APIs
        |               |               |
        +---------------+---------------+
                        |
                  OBSERVABILITY
                        |
                        v
                    BENCHMARK
```

No introducir todavía:

- swarm multi-agent;
- planner complejo;
- Dynamic Tool Registry;
- Context Compaction basado en LLM;
- Model Router automático;
- MCP adicional;
- self-hosting;
- fine-tuning;
- vector DB por defecto;
- framework de agentes externo.

---

## 14. Roadmap revisado

### Fase 0 — Congelar arquitectura

No introducir nuevas abstracciones grandes.

Mantener el comportamiento actual.

### Fase 1 — Telemetría económica

Registrar:

```text
LLM call
├── provider
├── model
├── prompt_tokens
├── cached_tokens
├── output_tokens
├── reasoning_tokens
├── latency
├── iteration
├── tool_calls
├── cost
└── session_id
```

Y por tarea:

```text
total tokens
total cost
total latency
iterations
success
tools
```

**Resultado:** poder responder cuánto cuesta realmente una tarea de Zigurat.

### Fase 2 — Benchmark v0

Crear las 15 tareas reales.

Ejecutarlas con distintos modelos.

**Resultado:**

```text
model × task × success × tokens × cache × latency × cost
```

### Fase 3 — Unificación de lógica de negocio

Reducir divergencia entre Desktop y Cloud.

**Resultado:** una misma regla produce el mismo resultado independientemente del cliente.

### Fase 4 — Limpieza de dependencias

Evaluar y eventualmente eliminar dependencias de SDKs/frameworks que ya no sean necesarias.

### Fase 5 — Optimización experimental

Solo después de tener datos:

- Dynamic Tool Registry;
- parallel tools;
- context compaction;
- model routing.

Cada uno debe justificarse mediante un experimento.

---

## 15. Criterio de decisión

Toda nueva optimización debe responder cuatro preguntas.

### Hipótesis

> ¿Qué creemos que mejorará?

### Métrica

> ¿Cómo lo vamos a medir?

### Experimento

> ¿Cuál es el cambio mínimo que podemos probar?

### Resultado

> ¿Mejoró realmente?

Después:

```text
MEJORA
  ↓
ADOPTAR

NO MEJORA
  ↓
DESCARTAR
```

Esto evita convertir Zigurat en una arquitectura compleja sin evidencia de beneficio.

---

## 16. Posición final frente al análisis de Claude Opus 5

Estamos de acuerdo con las principales objeciones:

1. **Instrumentación es la prioridad número uno.**
2. **El caché es una variable arquitectónica crítica.**
3. **Dynamic Tool Registry no debe implementarse sin medir su impacto sobre caché.**
4. **Context Compaction debe posponerse por el riesgo de pérdida de precisión.**
5. **Parallel tools es secundaria frente al coste de las vueltas LLM.**
6. **Model Router es prematuro.**
7. **El benchmark inicial debe ser pequeño y basado en tareas reales.**
8. **La existencia de dos runtimes es una deuda arquitectónica importante.**
9. **La lógica de negocio debe tender hacia un Single Source of Truth.**
10. **Las nuevas abstracciones deben justificarse mediante mediciones.**

---

## 17. Qué mantenemos de la propuesta original

La visión de largo plazo sigue siendo válida:

> **Zigurat debe ser independiente del modelo.**

OpenAI no debe convertirse en el núcleo del producto.

El sistema debe poder utilizar:

```text
OpenAI
Anthropic
DeepSeek
Qwen
GLM
Kimi
Open-weight
otros proveedores
```

sin reescribir:

- tools;
- lógica de negocio;
- seguridad;
- estado;
- evaluación.

Pero esta capacidad debe evolucionar incrementalmente sobre la arquitectura existente.

---

# 18. Conclusión

La propuesta inicial respondió:

> "¿Cómo podría ser un Agent Runtime moderno?"

El análisis de Claude respondió:

> "¿Qué necesita realmente este repositorio?"

Para Zigurat, la segunda pregunta debe dominar.

La estrategia revisada es:

```text
                  MEDIR
                    |
                    v
              ENTENDER COSTE
                    |
                    v
             BENCHMARK REAL
                    |
                    v
           UNIFICAR NEGOCIO
                    |
                    v
         EXPERIMENTAR OPTIMIZACIONES
                    |
          +---------+---------+
          |         |         |
          v         v         v
        TOOLS     CONTEXT   ROUTING
          |         |         |
          +---------+---------+
                    |
                    v
             ADOPTAR SOLO
             LO QUE MEJORE
```

### Principio rector

> **No vamos a optimizar porque una técnica sea considerada buena práctica. Vamos a optimizar aquello que los datos de Zigurat demuestren que es un cuello de botella.**

Y simultáneamente:

> **No vamos a sacrificar la independencia de modelos ni la separación entre lógica de negocio y proveedor de LLM.**

Esta combinación —**evidencia empírica + arquitectura agnóstica al modelo + una única lógica de negocio**— es la dirección propuesta para la siguiente etapa de Zigurat.
