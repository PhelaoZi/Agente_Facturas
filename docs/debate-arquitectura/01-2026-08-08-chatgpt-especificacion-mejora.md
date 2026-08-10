# Especificación de Mejora — Agente_Facturas / Zigurat

## 1. Objetivo

Evolucionar `Agente_Facturas` hacia un **Agent Runtime agnóstico al modelo**, optimizado para tareas ERP de corto y largo alcance, con énfasis en:

- eficiencia de tokens;
- reducción de tool calls innecesarios;
- baja latencia;
- ejecución paralela de operaciones independientes;
- control seguro de acciones con efectos secundarios;
- memoria y estado eficientes;
- posibilidad de utilizar modelos de múltiples proveedores;
- medición objetiva de coste, calidad y rendimiento.

La arquitectura **no debe quedar acoplada a OpenAI**. OpenAI será un proveedor posible junto con modelos comerciales, chinos y open-weight.

---

# 2. Concepto principal

El proyecto debe convertirse en:

> **Un único runtime de agente de negocio que utiliza las mismas tools, estado y reglas de seguridad, pero puede seleccionar distintos modelos según la dificultad y naturaleza de cada tarea.**

Conceptualmente:

```text
                         ZIGURAT
                            |
                            v
                    +---------------+
                    | Agent Runtime |
                    +-------+-------+
                            |
                      Model Router
                            |
             +--------------+--------------+
             |              |              |
             v              v              v
          OpenAI        DeepSeek       Qwen / GLM
          Claude         Kimi          Open Models
             |              |              |
             +--------------+--------------+
                            |
                          Tools
                            |
              +-------------+-------------+
              |             |             |
             ERP           SQL         Actions
              |             |             |
              +-------------+-------------+
                            |
                        PostgreSQL
                            |
                       Trace / Eval
```

---

# 3. Principios arquitectónicos

## 3.1 Model-agnostic

El runtime no debe depender de un proveedor específico.

Debe existir una interfaz común:

```python
class ModelProvider:
    async def generate(
        self,
        messages,
        tools,
        model,
        **kwargs
    ):
        ...
```

Implementaciones iniciales:

- `OpenRouterProvider`
- `OpenAIProvider`

Implementaciones futuras:

- Anthropic
- DeepSeek
- Qwen
- GLM
- Kimi
- modelos self-hosted

## 3.2 Las tools pertenecen al dominio, no al proveedor

Las herramientas actuales del ERP se mantienen como lógica de negocio propia.

El proveedor de LLM solo decide cuándo y cómo solicitar una tool.

## 3.3 Seguridad antes que optimización

Las operaciones de lectura pueden optimizarse agresivamente.

Las operaciones con efectos secundarios deben conservar:

- validación;
- propuesta;
- confirmación humana;
- ejecución determinista.

## 3.4 Medir antes de optimizar

Antes de realizar una refactorización grande se debe instrumentar el runtime.

Toda optimización debe poder demostrar una mejora mediante métricas.

---

# 4. Arquitectura objetivo

```text
app/
├── agent/
│   ├── runtime.py          # Loop principal del agente
│   ├── state.py            # Estado estructurado
│   ├── context.py          # Construcción/compaction del contexto
│   ├── models.py           # Interfaz común de modelos
│   ├── model_router.py     # Selección de modelo
│   ├── tool_registry.py    # Registro y selección dinámica de tools
│   ├── tracing.py          # Métricas y trazas
│   └── evaluation.py       # Benchmark/evaluación
│
├── providers/
│   ├── openrouter.py
│   └── openai.py
│
├── tools/
│   ├── negocio/
│   ├── acciones/
│   ├── memoria/
│   ├── visualizacion/
│   └── postgres/
│
└── ...
```

La estructura exacta puede adaptarse al repositorio existente; el objetivo es separar responsabilidades, no renombrar archivos por sí mismo.

---

# 5. Agent Runtime

El runtime será responsable de:

1. recibir la solicitud;
2. crear el objetivo de la tarea;
3. obtener el conjunto de tools relevante;
4. seleccionar un modelo;
5. ejecutar el ciclo agente/tool;
6. actualizar el estado;
7. paralelizar tools de lectura independientes;
8. controlar límites;
9. detectar finalización;
10. generar la respuesta final;
11. registrar métricas.

Flujo:

```text
USER
 |
 v
RUNTIME
 |
 +--> determine task/domain
 |
 +--> select tools
 |
 +--> select model
 |
 +--> model
 |
 +--> tool calls
 |
 +--> update state
 |
 +--> model
 |
 +--> final response
```

---

# 6. Dynamic Tool Registry

## Problema actual

El agente recibe aproximadamente todas las tools disponibles en cada llamada.

Esto aumenta:

- tokens de entrada;
- tamaño del contexto;
- complejidad de selección;
- probabilidad de tool selection incorrecta.

## Solución

Implementar un `ToolRegistry` con agrupación por dominio.

Ejemplo:

```text
ventas
├── ventas_total
├── ventas_cliente
├── ventas_producto
├── ranking_clientes
└── margen_periodo

cobranza
├── deuda_total
├── deuda_cliente
├── ranking_deudores
└── facturas_vencidas

acciones
├── marcar_factura_pagada
├── editar_gasto
├── borrar_gasto
└── ...

visualizacion
├── publicar_kpi
├── publicar_grafico
├── publicar_tabla
└── publicar_informe
```

Una tarea de ventas no debería recibir las tools de gastos o visualización salvo que sean necesarias.

### Objetivo inicial

Reducir el número de schemas de tools enviados al modelo sin modificar la funcionalidad existente.

---

# 7. Ejecución paralela de tools

Las tools de lectura independientes deben ejecutarse en paralelo.

Ejemplo:

```text
                    +-- ventas_total
MODEL -------------+
                    +-- deuda_total
                    |
                    +-- margen_periodo
```

Estas operaciones no deberían bloquearse mutuamente.

Implementación objetivo:

```python
results = await asyncio.gather(
    ventas_total(...),
    deuda_total(...),
    margen_periodo(...)
)
```

## Regla

### Paralelizar

- SELECT;
- consultas de reporting;
- cálculos independientes;
- lecturas de memoria;
- otras operaciones sin efectos secundarios.

### Ejecutar secuencialmente

- writes;
- acciones confirmables;
- operaciones dependientes;
- cualquier operación donde el orden sea significativo.

---

# 8. State Management

El historial conversacional bruto no debe ser el único mecanismo de memoria.

El runtime debe mantener un estado estructurado:

```json
{
  "objective": "...",
  "facts": {},
  "completed_tasks": [],
  "pending_tasks": [],
  "relevant_tool_results": {},
  "decisions": [],
  "errors": []
}
```

## Objetivo

Evitar que un agente largo tenga que reenviar todo el historial de tools y mensajes anteriores.

---

# 9. Context Compaction

Implementar compaction progresiva.

Conceptualmente:

```text
RAW HISTORY
     |
     v
COMPACTOR
     |
     v
STRUCTURED STATE
```

El contexto debe conservar:

- objetivo;
- hechos relevantes;
- decisiones;
- tareas pendientes;
- resultados importantes;
- errores relevantes.

Debe eliminar o resumir:

- resultados antiguos ya procesados;
- tool outputs redundantes;
- mensajes que ya no aportan información;
- detalles intermedios irrelevantes.

## Regla

La compaction no debe alterar hechos de negocio.

---

# 10. Model Gateway

Todos los modelos deben exponer una interfaz común.

Ejemplo:

```python
response = await provider.generate(
    model="...",
    messages=messages,
    tools=tools,
    temperature=...,
)
```

El runtime no debe contener lógica específica de:

- OpenAI;
- Qwen;
- DeepSeek;
- GLM;
- Claude;
- etc.

Eso debe quedar encapsulado en el provider.

---

# 11. Model Router

Inicialmente debe ser simple.

Ejemplo:

```yaml
models:
  simple: "modelo-barato"
  general: "modelo-general"
  complex: "modelo-frontier"
```

Clasificación inicial:

### Simple

- consultas directas;
- tool selection sencilla;
- extracción;
- respuestas cortas.

### General

- multi-tool;
- SQL más complejo;
- análisis de negocio normal.

### Complex

- planificación;
- tareas long-horizon;
- análisis financiero complejo;
- decisiones que requieran razonamiento avanzado.

## Futuro

El router podrá utilizar:

- dificultad estimada;
- historial de éxito;
- coste;
- latencia;
- disponibilidad;
- confidence;
- tipo de tool.

---

# 12. Fast Path

Las consultas triviales no deberían entrar siempre al loop completo.

Ejemplo:

```text
"¿Cuánto vendimos este mes?"
```

Debe poder ejecutarse como:

```text
intent
  |
ventas_total()
  |
respuesta
```

El objetivo es reducir:

- iteraciones;
- tokens;
- latencia;
- coste.

---

# 13. Actions / Human-in-the-loop

Las operaciones con efectos secundarios mantienen el patrón:

```text
MODEL
 |
 v
PROPUESTA
 |
 v
VALIDACIÓN
 |
 v
CONFIRMACIÓN HUMANA
 |
 v
EJECUCIÓN DETERMINISTA
 |
 v
RESULTADO
```

Ejemplos:

- marcar factura como pagada;
- modificar gasto;
- eliminar gasto;
- crear/editar información;
- cualquier operación financiera.

El modelo nunca debe tener acceso directo a una operación destructiva sin pasar por las políticas correspondientes.

---

# 14. PostgreSQL

Mantener las protecciones existentes:

- consultas de solo lectura para SQL generado;
- `statement_timeout`;
- límite de filas;
- validación de comandos;
- validación de columnas;
- uso de `information_schema` cuando corresponda.

La optimización debe enfocarse en reducir consultas innecesarias, no en eliminar estas protecciones.

---

# 15. Observabilidad / Agent Flight Recorder

Cada ejecución debe generar una traza estructurada.

Ejemplo:

```json
{
  "task": "...",
  "model": "...",
  "provider": "...",
  "iterations": 3,
  "tool_calls": 4,
  "input_tokens": 7200,
  "cached_input_tokens": 5100,
  "output_tokens": 1200,
  "latency_ms": 4200,
  "cost_usd": 0.00,
  "success": true
}
```

Además:

```json
{
  "tools": {
    "ventas_total": 1,
    "ranking_clientes": 1
  }
}
```

Y, cuando sea posible:

```json
{
  "context": {
    "system_tokens": 0,
    "tool_schema_tokens": 0,
    "history_tokens": 0,
    "tool_output_tokens": 0
  }
}
```

## Métricas principales

- success rate;
- input tokens;
- cached tokens;
- output tokens;
- total tokens;
- tool calls;
- iterations;
- latency;
- retries;
- errores;
- coste;
- coste por tarea exitosa.

---

# 16. Benchmark de Zigurat

Crear un benchmark propio del dominio ERP.

Inicialmente:

## Nivel 1 — Simple

- ventas;
- deuda;
- margen;
- facturas vencidas.

## Nivel 2 — Multi-tool

- ventas + clientes;
- deuda + facturas;
- margen + ventas.

## Nivel 3 — Análisis

- identificación de problemas;
- análisis financiero;
- explicación de variaciones.

## Nivel 4 — Actions

- proponer acciones;
- confirmar;
- ejecutar.

## Nivel 5 — Long-horizon

- analizar;
- investigar;
- sintetizar;
- proponer acciones;
- producir informe.

---

# 17. Métricas del benchmark

Para cada modelo:

| Métrica | Descripción |
|---|---|
| Success rate | Porcentaje de tareas correctas |
| Tool calls | Número de tools ejecutadas |
| Iterations | Vueltas del agente |
| Input tokens | Tokens de entrada |
| Cached tokens | Tokens reutilizados |
| Output tokens | Tokens generados |
| Latency | Tiempo total |
| Cost | Coste total |
| Cost / success | Coste por tarea correcta |

La métrica económica principal debe ser:

> **Coste por tarea correctamente completada.**

No simplemente coste por millón de tokens.

---

# 18. Modelos a evaluar

El sistema debe permitir comparar proveedores sin modificar las tools.

Categorías:

- OpenAI;
- Anthropic;
- Google;
- DeepSeek;
- Qwen;
- GLM;
- Kimi;
- otros modelos open-weight.

El benchmark determinará qué modelo es mejor para cada tipo de tarea.

No se debe asumir que un único modelo es óptimo para todo.

---

# 19. Arquitectura de referencia

```text
                         ZIGURAT
                            |
                            v
                    +---------------+
                    | Agent Runtime |
                    +-------+-------+
                            |
              +-------------+-------------+
              |             |             |
              v             v             v
            State        Tool Registry  Model Router
              |             |             |
              |        +----+----+        |
              |        |    |   |         |
              |       ERP  SQL Actions    |
              |                            |
              |                 +----------+----------+
              |                 |          |          |
              |                 v          v          v
              |              OpenAI     OpenRouter  Local
              |                           |
              |                      Qwen/GLM/
              |                      DeepSeek/Kimi
              |
              +-------------+-------------+
                            |
                            v
                       PostgreSQL
                            |
                            v
                    Trace / Evaluation
```

---

# 20. Plan de implementación

## Fase 1 — Instrumentación

**No cambiar comportamiento.**

Implementar:

- métricas;
- tracing;
- tokens;
- tool calls;
- iteraciones;
- latencia;
- errores;
- coste.

### Resultado

Conocer el coste real del agente actual.

---

## Fase 2 — Separación arquitectónica

Crear:

```text
runtime
state
context
models
tool_registry
tracing
```

Manteniendo las tools existentes.

### Resultado

Misma funcionalidad, arquitectura desacoplada.

---

## Fase 3 — Dynamic Tool Registry

Reducir las tools disponibles por tarea.

### Resultado esperado

Menor contexto y mejor tool selection.

---

## Fase 4 — Parallel Tool Execution

Ejecutar lecturas independientes concurrentemente.

### Resultado esperado

Menor latencia.

---

## Fase 5 — Context Compaction

Introducir estado estructurado y reducción del historial.

### Resultado esperado

Mejor escalabilidad en tareas long-horizon.

---

## Fase 6 — Model Gateway

Separar completamente el runtime del proveedor.

### Resultado

Poder cambiar de modelo sin cambiar el agente.

---

## Fase 7 — Benchmark

Ejecutar las mismas tareas contra diferentes modelos.

### Resultado

Ranking específico de Zigurat.

---

## Fase 8 — Model Routing

Seleccionar automáticamente el modelo según:

- dificultad;
- coste;
- latencia;
- éxito histórico.

---

# 21. Criterios de éxito

La mejora se considera exitosa si:

### Eficiencia

- disminuye el número promedio de tool calls;
- disminuyen los tokens promedio;
- disminuye el coste por tarea;
- disminuye la latencia.

### Calidad

- no disminuye el success rate;
- mejora la selección de tools;
- disminuyen los errores de SQL;
- disminuyen los loops innecesarios.

### Escalabilidad

- las tareas largas no provocan crecimiento descontrolado del contexto;
- se pueden añadir tools sin aumentar proporcionalmente el contexto;
- se pueden añadir modelos sin modificar el runtime.

### Seguridad

- las acciones sensibles siguen requiriendo validación/confirmación;
- PostgreSQL mantiene las restricciones de seguridad;
- ningún modelo obtiene permisos de escritura directos no controlados.

---

# 22. Qué NO implementar inicialmente

Para mantener el proyecto controlable:

- no implementar multi-agent swarm;
- no introducir MCP como requisito;
- no self-hostear modelos inicialmente;
- no hacer fine-tuning;
- no introducir vector DB sin una necesidad demostrada;
- no reemplazar todas las tools existentes;
- no migrar completamente a un framework externo;
- no crear planners complejos antes de medir la necesidad.

---

# 23. Estado final esperado

El resultado debe ser:

```text
                 UNA APLICACIÓN
                       |
                 UN AGENT RUNTIME
                       |
        +--------------+--------------+
        |              |              |
       STATE          TOOLS          MODELS
        |              |              |
        |        ERP / SQL /       OpenAI
        |        Actions           Claude
        |                           Qwen
        |                           GLM
        |                           DeepSeek
        |                           Kimi
        |                           Open Models
        |
        +--------------+--------------+
                       |
                  TRACE / EVAL
                       |
                MODEL ROUTING
```

### Principio rector

> **El modelo es intercambiable. El conocimiento de negocio, las tools, la seguridad, el estado y la evaluación pertenecen a Zigurat.**

Ese es el objetivo arquitectónico de la evolución de `Agente_Facturas`.
