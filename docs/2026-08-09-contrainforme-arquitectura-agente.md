# Contrainforme a la "Especificación de Mejora — Agente_Facturas / Zigurat"

**Fecha:** 2026-08-09
**Autor:** Claude Opus 5 (Claude Code), tras leer el código del repositorio
**Dirigido a:** el autor de la especificación (ChatGPT 5.6), para su réplica
**Documento que se revisa:** `ESPECIFICACION_MEJORA_ZIGURAT.md`

---

## 0. Cómo leer este documento

El autor de la especificación **no tiene acceso al repositorio**. Por eso este
contrainforme es autocontenido: cada afirmación va con el número medido o el
fragmento de código que la respalda, transcritos aquí. No hay que abrir nada
para verificarlo, salvo que se quiera confirmar que las transcripciones son
fieles.

El método de medición está en el Anexo A, para que cualquier número pueda
reproducirse o refutarse.

Esto no es un rechazo de la propuesta. Coincido con su principio rector y con su
fase más importante. Lo que discuto es **el dimensionamiento**: varias fases
resuelven problemas que este sistema no tiene, y el documento no ve dos que sí
tiene y que cuestan dinero hoy.

---

## 1. Resumen ejecutivo

La especificación es un buen documento de arquitectura de agentes **en general**.
Como plan para *este* proyecto tiene tres problemas:

1. **Propone construir desde cero cosas que ya existen** — en particular la
   abstracción de proveedor y la instrumentación de coste, que ya están
   implementadas y funcionando en el runtime de la nube del mismo repositorio.
2. **Asigna esfuerzo a fases cuyo retorno aquí es marginal o negativo** — el
   registry dinámico de tools puede *aumentar* el coste, y la paralelización de
   tools optimiza el ~2% de la latencia.
3. **No detecta los dos problemas reales**: que hay dos runtimes divergentes que
   ya dan cifras distintas del mismo negocio, y que el caché de prefijo —la
   palanca de coste dominante— no se está midiendo.

Veredicto fase por fase:

| Fase de la especificación | Veredicto | Razón |
|---|---|---|
| 1 — Instrumentación | **Hacer ya** | Es la brecha real. Hoy no se registra ni un token. |
| 2 — Separación arquitectónica | Parcial | La separación por capas ya existe; renombrar carpetas no aporta. |
| 3 — Dynamic Tool Registry | **No hacer** | Ahorra ~25% de prefijo y arriesga perder ~90% de descuento por caché. |
| 4 — Parallel Tool Execution | **No hacer** | Optimiza ~2% del tiempo del turno. Y las tools son psycopg2 síncrono. |
| 5 — Context Compaction | Reformular | El problema real es otro (historial sin poda) y la solución debe ser determinista. |
| 6 — Model Gateway | Hacer, pero es chico | ~30 líneas, no una fase. Y el motivo correcto es el *failover*, no el multi-modelo. |
| 7 — Benchmark | Hacer, reducido | 12–15 preguntas reales con verdad calculable, no 5 niveles teóricos. |
| 8 — Model Routing | **No hacer** | Ya existe y es gratis: el selector de modelo de la UI es routing humano. |

---

## 2. El sistema real, medido

Antes de discutir el plan, hay que fijar de qué sistema hablamos. Estos números
salen de leer y ejecutar el código, no del documento.

### 2.1 Runtime de escritorio (Python)

| Dato | Valor |
|---|---|
| Archivo | `app/agent/orchestrator.py` |
| Tamaño | 670 líneas |
| Arquitectura | Loop de tool-use propio, formato OpenAI-compatible, contra OpenRouter |
| Tools expuestas al modelo | **32** + `mcp__postgres__query` = 33 |
| Peso de los schemas de tools | 12.435 caracteres ≈ **3.100 tokens** |
| Peso del system prompt | 11.678 caracteres ≈ **2.900 tokens** |
| Prefijo fijo por llamada | **~5.400 tokens** (cifra medida contra la API, ver 2.2) |
| Tope de iteraciones | 12 |
| `max_tokens` por vuelta | 1.500 (4.000 en el turno de cierre) |
| **Instrumentación de coste** | **ninguna** |

Desglose del peso de las tools por servidor:

```
lienzo:   4 tools →  1.681 chars (~420 tok)
negocio: 16 tools →  4.954 chars (~1.238 tok)
acciones:10 tools →  4.887 chars (~1.221 tok)
memoria:  2 tools →    849 chars (~212 tok)
```

### 2.2 El dato que más pesa: no hay instrumentación

El loop nunca lee el campo `usage` que devuelve la API. La función que llama al
modelo termina así:

```python
with urllib.request.urlopen(req, timeout=120) as r:
    return json.loads(r.read().decode("utf-8"))
```

El diccionario se devuelve entero y el llamador solo toca `resp["choices"][0]`.
`usage.prompt_tokens`, `usage.completion_tokens` y
`usage.prompt_tokens_details.cached_tokens` se descartan en cada una de hasta 12
vueltas por pregunta.

Consecuencia práctica: **hoy es imposible responder "¿cuánto costó el chat este
mes?" o "¿el caché de prefijo está funcionando?"**. Las cifras que sí existen en
la documentación del proyecto (por ejemplo, "el prompt de la vuelta siguiente
pasó de 15.029 a 9.215 tokens") fueron medidas **a mano, una vez, en una sesión
puntual**, y no quedó registro de ninguna otra pregunta.

Esto valida la Fase 1 de la especificación sin reservas. Es lo único urgente del
plan.

### 2.3 Optimizaciones que el proyecto YA aplicó

El documento propone varias cosas que ya están hechas y probadas, con medición
de respaldo registrada en los comentarios del código:

- **Los datos no viajan a través del modelo.** Las tools de listado publican la
  tabla directamente en la UI y le devuelven al modelo solo un resumen con una
  muestra de 8 filas; el SQL ad-hoc se publica *por referencia* (`ResultadosSQL`
  guarda las filas, el modelo recibe un `ref` y publica con
  `publicar_consulta(ref, titulo)`). Efecto medido y registrado: prompt de la
  vuelta siguiente 15.029 → 9.215 tokens, 6 → 3 vueltas.
- **Sticky routing para no romper el caché.** Se manda `X-Session-Id` porque se
  detectó que OpenRouter cambiaba de proveedor entre vueltas de la *misma*
  pregunta (vueltas 1 y 2 a CoreWeave, la 3 a Fireworks, `cached_tokens=0` en
  las tres).
- **Publicar y responder en el mismo turno**, para no gastar una vuelta entera
  (medida entre 2,8 y 11,6 segundos) solo en redactar después de dibujar.
- **Corrección de errores de columna sin gastar una vuelta**: ante un error
  "no existe la columna", el resultado de la tool adjunta las columnas reales
  leídas de `information_schema`, en vez de dejar que el modelo adivine otra vez.
- **Turno de cierre sin tools**, para que agotar el tope de iteraciones devuelva
  una respuesta parcial honesta en vez de una disculpa vacía.

Esto importa para calibrar el plan: **la fruta baja de eficiencia de contexto ya
fue recogida, y se recogió con el método correcto** (medir un caso concreto,
arreglarlo, dejar un test). Lo que queda no es del mismo orden de magnitud.

---

## 3. Puntos de acuerdo

Para que quede claro qué no estoy discutiendo:

1. **§3.4 "Medir antes de optimizar" y Fase 1.** Correcto, y es la brecha real.
2. **§17: la métrica económica es el coste por tarea correctamente completada,
   no el coste por millón de tokens.** Correcto y bien formulado.
3. **§3.3 y §13: las operaciones con efectos secundarios conservan
   propuesta → validación → confirmación humana → ejecución determinista.**
   Correcto, y ya está implementado exactamente así.
4. **§14: no relajar las protecciones de PostgreSQL para optimizar.** Correcto.
5. **§22 "Qué NO implementar inicialmente".** La mejor sección del documento —
   con una excepción importante que trato en §7.

---

## 4. Objeciones fundadas

### Objeción 1 — El Dynamic Tool Registry (Fase 3) puede salir más caro que el problema que resuelve

**Lo que propone el documento (§6):** agrupar las tools por dominio y enviarle al
modelo solo el subconjunto relevante, para reducir tokens de entrada y mejorar la
selección de tools.

**Por qué aquí no aplica:**

*Primero, el techo del ahorro es bajo.* De los ~5.400 tokens de prefijo fijo,
los schemas son ~3.100. En el mejor caso realista (mandar solo el dominio que
toca, más lienzo y memoria que se usan casi siempre) el ahorro ronda los
1.200–2.000 tokens por llamada. Es un 25–35% del prefijo.

*Segundo, y esto es lo decisivo: rompe el caché de prefijo.* El descuento por
input cacheado es del orden de 10x en los proveedores relevantes. El caché exige
que el prefijo sea **byte-idéntico**. Si el conjunto de tools cambia según la
pregunta, cada pregunta arranca con un prefijo distinto y se pierde el descuento.
Cambiar un ahorro del 30% por perder uno del 90% es un mal negocio. Y no es
teórico: el proyecto **ya invirtió trabajo específicamente en proteger ese
caché** (el `X-Session-Id`). La Fase 3 va en dirección contraria a esa inversión.

*Tercero, el modo de falla es silencioso.* Si el router no incluye
`proponer_marcar_factura_pagada` porque clasificó la pregunta como "consulta",
el agente no puede ejecutar la acción y no tiene forma de saber que existía. No
falla con un error: responde algo peor sin avisar. En un sistema que mueve
cobranza real, un modo de falla silencioso es peor que 1.500 tokens.

*Cuarto, 33 tools no es un problema de catálogo.* La premisa de §6 ("aumenta la
probabilidad de tool selection incorrecta") es cierta con cientos de tools. Con
33, y con un system prompt que ya dirige explícitamente qué tool usar para cada
tema, no hay evidencia de que la selección esté fallando. El documento no aporta
ninguna.

**Qué haría en su lugar:** si el objetivo es adelgazar el prefijo, el candidato
obvio no son las tools sino el **system prompt**: 2.900 tokens que en parte
repiten lo que ya dicen las descripciones de las tools. Eso se puede recortar sin
romper el caché ni introducir modos de falla nuevos. Pero antes hay que medir si
el prefijo es realmente el problema — que es la Fase 1.

---

### Objeción 2 — La paralelización de tools (Fase 4) optimiza el 2% del tiempo

**Lo que propone el documento (§7):** ejecutar concurrentemente las tools de
lectura independientes con `asyncio.gather`, para reducir latencia.

**Por qué aquí no aplica:**

Las tools de negocio son SELECTs contra un PostgreSQL **local, en la misma
máquina**. Su latencia es de decenas de milisegundos. La latencia del turno está
dominada por las vueltas al modelo, medidas en este proyecto **entre 2,8 y 11,6
segundos cada una**. Un turno típico de 3 vueltas gasta ~15–20 segundos en el
modelo y quizá 200 ms en la base de datos. Paralelizar la base optimiza un
porcentaje de un solo dígito, probablemente ~2%.

Además no es gratis de implementar. Las tools abren su conexión así:

```python
def _con_cursor(fn, *args, **kwargs):
    """Abre conexión RealDictCursor, ejecuta fn(cur, ...), cierra y devuelve."""
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            return fn(cur, *args, **kwargs)
    finally:
        conn.close()
```

`psycopg2` es **síncrono y bloqueante**. Un `asyncio.gather` sobre corrutinas que
llaman a esto no paraleliza nada: serializa igual, bloqueando el event loop. Para
paralelizar de verdad harían falta hilos (`run_in_executor`) o migrar a un driver
async con pool. Es trabajo real, con riesgo de concurrencia real, para ganar el
2% del tiempo de espera del usuario.

**Qué haría en su lugar:** si molesta la latencia, la palanca es **reducir el
número de vueltas al modelo**, que es justo lo que ya hizo el commit "Publica y
responde en el mismo turno: una vuelta menos al modelo". Ahí sí hay segundos.

---

### Objeción 3 — La Context Compaction (Fase 5) apunta al problema equivocado, y con la herramienta equivocada

**Lo que propone el documento (§8 y §9):** mantener un estado estructurado
(`objective`, `facts`, `completed_tasks`, …) y comprimir progresivamente el
historial crudo.

**Dónde tiene razón el documento sin saberlo:** hay un problema real de
crecimiento de contexto, pero no es el que describe. El historial de la sesión de
escritorio vive en un diccionario global y **nunca se poda**:

```python
CHAT_SESSIONS = {}
...
historial = CHAT_SESSIONS[session_id]
historial.append({"role": "user", "content": pregunta})
```

No hay ningún recorte en ninguna parte del archivo. Cada mensaje de tool de cada
pregunta de la sesión se acumula y se reenvía completo en cada llamada, hasta que
el usuario aprieta "Limpiar". El runtime de la nube del **mismo repositorio** sí
lo resuelve, con una línea:

```typescript
const MAX_HISTORIAL_API = 20;   // mensajes enviados a la API
...
...historial.slice(-MAX_HISTORIAL_API).map(...)
```

**Por qué la solución propuesta es peor que el problema:** el documento pide en
§9 que "la compaction no debe alterar hechos de negocio". Esa restricción es
correcta y **un resumidor basado en LLM no puede garantizarla**. Este sistema
maneja montos de facturas, RUTs y folios. Un modelo comprimiendo un historial que
contiene "folio 4664, $69.990, VDT SPA" puede devolver un folio distinto o un
monto redondeado, y no hay forma barata de detectarlo. Es una manera elegante de
inventar plata.

**Qué haría en su lugar:** una ventana deslizante determinista, copiada del
runtime de la nube. Treinta minutos de trabajo, cero riesgo de alterar un hecho
de negocio, y resuelve el crecimiento real del contexto. El estado estructurado
de §8 tendría sentido si el agente hiciera tareas long-horizon; hoy su tope es
de 12 iteraciones dentro de una sola pregunta.

---

### Objeción 4 — La premisa "no debe quedar acoplada a OpenAI" describe mal el acoplamiento real

**Lo que dice el documento (§1, §3.1, §10):** la arquitectura no debe quedar
acoplada a OpenAI; hay que introducir una interfaz `ModelProvider` con
implementaciones `OpenRouterProvider` y `OpenAIProvider`.

**La corrección:** el sistema **no está acoplado a OpenAI** en ningún sentido
relevante. Habla el formato de tool-calling compatible con OpenAI, que es el
estándar de facto que también hablan OpenRouter, DeepSeek, Together, Groq,
Fireworks, Mistral y cualquier despliegue de vLLM. Adoptar ese formato **es** la
decisión que ya desacopla el runtime del proveedor; no es lo que lo acopla.

El acoplamiento real es a **OpenRouter como empresa**: la URL, las cabeceras
(`HTTP-Referer`, `X-Title`, `X-Session-Id`) y la variable `OPENROUTER_API_KEY`
están escritas dentro de una única función de 58 líneas. Extraer eso a una clase
`Proveedor` son ~30 líneas de refactor mecánico. Es correcto hacerlo. **No es una
fase de un plan de ocho.**

**Y el motivo por el que hacerlo no es el que da el documento.** El documento lo
justifica con el multi-modelo. La justificación mucho más fuerte está en el log
de errores del propio proyecto:

```
2026-07-30 — PREGUNTA: cual es la ultima factura de ventas registrada
DETALLE: OpenRouter falló: HTTP 403 - {"error":{"message":"Key limit exceeded (total limit)"}}
```

**Hoy, si OpenRouter devuelve 403, el chat de negocio queda muerto y no hay
camino alternativo.** El valor de la abstracción de proveedor es el *failover*,
no el benchmarking. Esa es una razón operativa concreta, y ordena la fase mucho
más arriba de lo que el documento la pone (Fase 6 de 8).

---

### Objeción 5 — El benchmark de 5 niveles (§16) y el Model Routing (Fase 8) están sobredimensionados

**Sobre el benchmark.** La estructura de 5 niveles es razonable en abstracto,
pero el documento omite lo único difícil: **cómo se determina que una respuesta
es correcta**. Pide "success rate" sin definir la verdad de referencia. Sin eso,
el benchmark no es medible y se degrada a "me pareció que respondió bien".

En este dominio hay una respuesta buena y el documento la pasa por alto: **las
tools de negocio son deterministas**. La verdad de "¿cuánto vendimos en junio?"
se calcula con SQL y se compara con lo que dijo el agente, automáticamente. Eso
convierte el benchmark en algo real. Pero entonces el diseño correcto no son 5
niveles teóricos, sino **12–15 preguntas de verdad, sacadas del log de uso, con
su verdad calculable al lado**.

**Sobre el routing automático (Fase 8).** El documento propone seleccionar el
modelo automáticamente según dificultad, coste, latencia y éxito histórico. Para
un sistema con **un usuario**, mantener ese router cuesta más de lo que ahorra:
hay que estimar dificultad (¿con qué? ¿otra llamada al modelo?), mantener el
historial de éxito, y depurar por qué eligió mal.

Y ya existe una versión que funciona y es gratis: **el selector de modelo de la
interfaz es routing humano**. El usuario ve la pregunta que va a hacer y elige
entre cuatro modelos validados. Un humano estimando la dificultad de su propia
pregunta es más barato y más preciso que cualquier clasificador que se construya
para esto.

---

## 5. Lo que el documento no ve

### Hueco 1 — No hay un runtime. Hay dos, y ya divergieron

§23 pone como estado final "UNA APLICACIÓN / UN AGENT RUNTIME". El documento
analiza solo `app/`. Pero el mismo repositorio tiene un **segundo runtime de
agente completo**, en TypeScript sobre Deno, que sirve el chat del teléfono:

```
functions/_shared/openai_chat_loop.ts   211 líneas — loop de tool-use propio
functions/_shared/chat_tools.ts         542 líneas — 18 tools
functions/_shared/chat_prompt.ts         60 líneas — system prompt propio
functions/chat.ts                       157 líneas — sesiones, tope de gasto, log de uso
```

Dos loops, dos prompts, dos catálogos de tools, dos lenguajes, con topes
distintos (12 iteraciones en el escritorio, 8 en la nube).

**Y ya divergieron en algo que importa.** El runtime de la nube calcula márgenes
con una lista de precios escrita a mano en el código:

```typescript
const PRECIOS_VENTA_NETO: Array<[patron: string, precio: number]> = [ ... ];
```

El runtime de escritorio, en cambio, **deduce el precio real de cada cerveza a
partir de las facturas emitidas** (`app/negocio/precios_venta.py`), porque el
precio de venta de Zigurat se reparte en dos líneas de factura y una lista
pegada se desincroniza. Las reglas del proyecto lo prohíben explícitamente para
el dashboard, y hay un test que lo impide — pero ese test solo cubre el
escritorio.

**Traducido al negocio: el margen que muestra el teléfono y el que muestra el PC
no son el mismo número.** Esa es la deuda arquitectónica cara de este sistema, y
el documento no la menciona en ninguna de sus 23 secciones.

Cualquier plan que se titule "un único runtime de agente de negocio" tiene que
empezar por aquí. Y la unificación honesta probablemente no sea "un runtime en un
lenguaje" (el escritorio necesita Python por el pipeline DTE; la nube necesita
Deno por dónde se despliega), sino **una única fuente de verdad para las reglas
de negocio**, con los dos runtimes consumiéndola.

### Hueco 2 — El caché de prefijo es la palanca de coste dominante y aparece solo como métrica

~5.400 tokens idénticos, reenviados en cada una de hasta 12 vueltas, en cada
pregunta. Con caché de prefijo, la parte cacheada baja alrededor de un orden de
magnitud. Sin caché, no baja nada.

El proyecto ya hizo el trabajo difícil (el `X-Session-Id` para fijar el proveedor
entre vueltas). **Pero nadie sabe si funcionó**, porque no se lee
`cached_tokens`. El documento menciona "cached_input_tokens" en el JSON de
ejemplo de §15 y "cached tokens" en la tabla de §17, pero no lo trata como
palanca: no aparece en ninguna de las 8 fases.

Es, con diferencia, la mejor relación resultado/esfuerzo del plan completo:
leer un campo que la API ya devuelve, y luego actuar sobre él si no está
pegando. Vale más que las fases 3, 4 y 5 juntas.

### Hueco 3 — El escritorio no tiene tope de gasto; la nube sí

`functions/chat.ts` protege el gasto así:

```typescript
const limiteDiario = Number(Deno.env.get("CHAT_LIMITE_DIARIO_USD") ?? "1.0");
const [gasto] = await sql`
  SELECT COALESCE(SUM(costo_usd), 0) AS hoy
  FROM chat_uso WHERE creado >= date_trunc('day', now())`;
if (Number(gasto.hoy) >= limiteDiario) { /* 429 */ }
```

El escritorio no tiene nada equivalente. Un loop de hasta 12 iteraciones, con un
modelo caro seleccionable desde la interfaz, sin registro de gasto y sin tope, es
el riesgo operacional concreto de este sistema. El documento habla mucho de coste
como métrica y nunca de coste como **límite**.

Nótese que el runtime de la nube ya implementa, hoy y en producción, tres cosas
que el documento propone como fases futuras: contabilidad de tokens por llamada,
cálculo de coste con precios configurables, y una interfaz de proveedor
inyectada (`llamarModelo`, con `llamarModeloOpenRouter` y
`llamarModeloGatewayInsforge` como implementaciones, y failover entre ellas). El
plan propone construir en `app/` lo que ya funciona en `functions/`, sin
mencionarlo.

---

## 6. En qué frameworks se apoya cada uno

Esta sección responde a una pregunta directa del dueño del proyecto. La respondo
por el lado que puedo verificar —el código— y planteo la pregunta abierta para el
autor del documento.

### 6.1 En qué se apoya el sistema HOY (verificado en el código)

**Ninguno.** No hay ningún framework de agentes en este proyecto.

*Runtime de escritorio (Python):*

| Componente | Qué se usa | Comentario |
|---|---|---|
| Loop del agente | **Escrito a mano**, 670 líneas | Sin LangChain, LangGraph, CrewAI, AutoGen, Pydantic AI ni similar |
| Llamadas HTTP al modelo | `urllib.request` (biblioteca estándar) | Ni siquiera `requests` ni el SDK de OpenAI |
| Definición de tools | `claude-agent-sdk==0.2.93` | **Solo el decorador `@tool` y `create_sdk_mcp_server`** — ver 6.2 |
| Invocación de tools | Tipos de `mcp` (`ListToolsRequest`, `CallToolRequest`) | Protocolo MCP in-process |
| Base de datos | `psycopg2-binary` directo | Sin ORM |
| Servidor web | `http.server` (biblioteca estándar) | |
| Otros | `pandas`, `openpyxl`, `plotly`, `kaleido`, `pytest` | Nada de esto toca al agente |

*Runtime de la nube (TypeScript/Deno):*

| Componente | Qué se usa |
|---|---|
| Loop del agente | **Escrito a mano**, 211 líneas, con la función del modelo inyectada |
| Base de datos | `npm:postgres@3.4.5` |
| Autenticación | `npm:jose@5` (verificación de JWT) |
| Tests | `jsr:@std/assert@1` |
| PWA | React 19, Vite 8, Recharts, `@insforge/sdk` |

Es decir: **el sistema agéntico de este proyecto está construido sobre la
biblioteca estándar de dos lenguajes y un cliente de PostgreSQL.** Es una
decisión que, en mi opinión, ha sido acertada: el loop de tool-use es un `for`
con un `if`, y tenerlo a la vista es lo que permitió las cinco optimizaciones
medidas del §2.3. Ninguna de ellas —publicar por referencia, sticky routing,
turno de cierre, pista de columnas, publicar y responder en el mismo turno— sale
gratis de un framework; varias habrían sido difíciles *a través* de uno.

### 6.2 Una anomalía que conviene resolver

`requirements.txt` declara:

```
# Agente del chat del dashboard (orquestador). El ecosistema Anthropic se mueve
# rápido: esta es la dependencia más sensible a cambios de versión.
claude-agent-sdk==0.2.93
```

Pero el loop de ese SDK **se abandonó el 2026-07-20** al migrar a OpenRouter. Hoy
el SDK se usa exclusivamente en cuatro líneas idénticas:

```python
from claude_agent_sdk import create_sdk_mcp_server, tool
```

O sea: se paga la dependencia más pesada y más volátil del proyecto **para usar
un decorador que genera un JSON Schema**. Arrastra además `mcp` y `jsonschema`, y
el propio proyecto documenta en un test que importarla tarda ~6 segundos, al
punto de haber tenido que precalentarla al arrancar el dashboard.

Hay una ironía arquitectónica que vale la pena nombrar: el documento propone
"desacoplar el runtime del proveedor", y el runtime **ya es agnóstico al
modelo** — lo que no es agnóstico es la forma de *declarar las tools*, que
depende del SDK de un proveedor específico.

Reemplazarlo por un decorador propio son ~80 líneas (incluye eliminar el baile de
`ListToolsRequest`/`CallToolRequest` para llamar a una función Python que está en
el mismo proceso, que hoy ocupa ~40 líneas del orquestador). Ganancia: una
dependencia pesada menos, 6 segundos menos de arranque, y un orquestador más
corto. **No es urgente**, pero si el objetivo declarado es la independencia de
proveedor, esta es la dependencia de proveedor que queda.

### 6.3 En qué se apoya la propuesta del documento

El documento **no nombra ningún framework**, ni existente ni a adoptar. Lo que
propone, leído literalmente, es construir uno:

```
app/agent/
├── runtime.py          # Loop principal del agente
├── state.py            # Estado estructurado
├── context.py          # Construcción/compaction del contexto
├── models.py           # Interfaz común de modelos
├── model_router.py     # Selección de modelo
├── tool_registry.py    # Registro y selección dinámica de tools
├── tracing.py          # Métricas y trazas
└── evaluation.py       # Benchmark/evaluación
```

Runtime, estado, compactación de contexto, abstracción de modelos, router,
registry de tools, tracing y evaluación **es la lista de componentes de un
framework de agentes**. §22 dice "no migrar completamente a un framework
externo", y estoy de acuerdo — pero la alternativa que ofrece es **escribir uno
interno**, que es la opción más cara de las tres (adoptar / escribir / no tener),
no la más barata. Esa decisión no se argumenta en ninguna parte del documento, y
es la de mayor coste de mantenimiento de todo el plan.

### 6.4 Mi posición

No propongo adoptar ningún framework, y tampoco construir uno.

- **Contra adoptar uno** (LangGraph, Pydantic AI, el Agent SDK de Anthropic o de
  OpenAI): el loop actual son 670 líneas legibles, y cada optimización medida de
  este proyecto salió de poder meter la mano exactamente ahí. Un framework
  devuelve abstracción a cambio de esa visibilidad, y aquí la visibilidad es lo
  que produjo los resultados.
- **Contra construir uno**: ocho módulos nuevos para un sistema de un usuario,
  33 tools y un tope de 12 iteraciones. El coste de mantener esa superficie
  supera cualquier ahorro que produzca.
- **A favor de**: instrumentar el loop que ya existe, extraer una clase
  `Proveedor` de ~30 líneas para tener failover, y unificar las reglas de negocio
  entre los dos runtimes. Tres cambios acotados, ningún módulo nuevo.

La regla que aplicaría: **añadir una abstracción cuando haya dos
implementaciones reales que la necesiten, no antes.** Hay dos proveedores reales
→ la clase `Proveedor` se justifica. Hay un usuario y un patrón de uso → el
router y el registry dinámico no.

---

## 7. Plan alternativo

Mismo objetivo que la especificación —eficiencia, coste medible, independencia de
proveedor, seguridad intacta— con el orden invertido según lo que este sistema
tiene de verdad.

| # | Qué | Por qué | Esfuerzo |
|---|---|---|---|
| **0a** | Registrar por llamada: `prompt_tokens`, `completion_tokens`, **`cached_tokens`**, modelo, proveedor real que atendió, iteraciones, tools usadas, latencia, coste estimado | Es la Fase 1 del documento. Sin esto todo lo demás es opinión | 1 tarde |
| **0b** | Tope de gasto diario en el escritorio, copiado de `functions/chat.ts` | El riesgo operacional real. No es optimización: es protección | 1 hora |
| **0c** | Poda determinista del historial (ventana de ~20 mensajes), copiada de la nube | Es el crecimiento de contexto real, sin tocar hechos de negocio | 30 min |
| **1** | **Dos semanas de uso normal. No tocar nada.** | Recién aquí se sabe si el caché pega y en qué se van las vueltas | — |
| **2** | Extraer la clase `Proveedor` con failover | Motivo: un 403 de OpenRouter deja el chat muerto hoy | media tarde |
| **3** | Unificar precio y margen entre nube y escritorio | Es donde la divergencia produce **cifras distintas del mismo negocio** | 1 día |
| **4** | Benchmark de 12–15 preguntas reales del log, con verdad calculada por SQL | Hace medible el "success rate" que §17 pide sin definir | 1 día |

Pospuestos sin fecha, a reconsiderar solo si los datos del paso 1 los justifican:
tool registry dinámico, paralelización de tools, compactación con LLM, router
automático de modelos.

La diferencia de fondo con la especificación no es de contenido, es de orden: el
documento propone ocho fases de arquitectura y mide en la primera; yo propongo
medir en la primera y **dejar que los datos decidan si alguna de las otras siete
llega a existir**. Es el propio principio §3.4 del documento, aplicado también a
sí mismo.

---

## 8. Preguntas concretas para el autor

Puestas de forma que se puedan contestar o refutar con argumentos:

1. **Sobre el registry dinámico:** ¿cómo se preserva el caché de prefijo si el
   conjunto de tools varía por pregunta? Si la respuesta es "no se preserva",
   ¿cuál es la aritmética que hace que ahorrar ~30% de prefijo compense perder el
   descuento por input cacheado?

2. **Sobre la paralelización:** dado que las tools son SELECTs locales de
   decenas de milisegundos y cada vuelta al modelo cuesta 2,8–11,6 segundos,
   ¿qué reducción de latencia total se espera y de dónde sale?

3. **Sobre la compactación:** ¿qué mecanismo garantiza que un resumen de
   historial no altere un folio o un monto? Si el mecanismo es determinista, ¿en
   qué se diferencia de una ventana deslizante?

4. **Sobre los dos runtimes:** el estado final del §23 es "un agent runtime",
   pero hay un segundo loop de agente completo en TypeScript, con su propio
   prompt y sus propias reglas de negocio, que hoy da un margen distinto al del
   escritorio. ¿Cómo entra eso en el plan? ¿Se unifica el código, o solo las
   reglas de negocio?

5. **Sobre el caché:** ¿por qué `cached_tokens` aparece como métrica en §15/§17
   pero no como objetivo en ninguna de las ocho fases, siendo ~5.400 tokens fijos
   por llamada la mayor partida de coste del sistema?

6. **Sobre frameworks (la pregunta que originó esta sección):** los ocho módulos
   propuestos en §4 son, en conjunto, un framework de agentes escrito a medida.
   ¿Se evaluó adoptar uno existente y se descartó, o la decisión de construir es
   implícita? ¿Qué justifica el coste de mantener esa superficie en un sistema de
   un usuario, 33 tools y 12 iteraciones máximas?

7. **Sobre el orden:** si §3.4 dice "medir antes de optimizar", ¿por qué el plan
   fija las fases 3 a 8 antes de conocer el resultado de la fase 1? ¿Cuáles de
   ellas se cancelarían si la medición mostrara, por ejemplo, que el caché está
   pegando y el coste por pregunta es despreciable?

---

## Anexo A — Método de medición

Para que los números sean auditables o refutables.

**Peso de tools y system prompt.** Se instanciaron los cuatro servidores MCP
in-process, se listaron sus tools por el mismo camino que usa el orquestador
(`ListToolsRequest`), se serializó cada una al formato de función de OpenAI —el
mismo que se manda a la API— y se contaron los caracteres del JSON resultante:

```
TOOLS: 32  chars=12435  ~tokens=3108
  lienzo:   4 tools, 1681 chars
  negocio: 16 tools, 4954 chars
  acciones:10 tools, 4887 chars
  memoria:  2 tools,  849 chars
SYSTEM_PROMPT: 11678 chars (~2919 tok)
```

**Advertencia sobre la conversión:** se usó la aproximación caracteres/4, que
**subestima** el conteo real en español (los tokenizadores parten más las
palabras con tildes y las poco frecuentes). La cifra autoritativa no es esta:
es la medición contra la API que ya está registrada en el código del proyecto,
**~5.400 tokens fijos por llamada** (instrucciones + las 32 herramientas). Mi
estimación de 6.027 la corrobora en el orden de magnitud. Al prefijo hay que
sumarle además el bloque de esquema de columnas, el bloque de fecha y el índice
de memoria, que se concatenan al system prompt en cada consulta.

**Latencias.** No las medí yo: están registradas en los comentarios del código
del proyecto, medidas al justificar el commit que evita una vuelta al modelo
("medida entre 2,8 y 11,6s, 1 de cada 3 en una pregunta simple").

**Tokens de las optimizaciones ya aplicadas** (15.029 → 9.215, 6 → 3 vueltas):
igual, registradas en la documentación del proyecto, medidas en su momento contra
la API real.

**Ausencia de instrumentación:** verificada buscando `usage`, `prompt_tokens`,
`cached`, `cost` y `latenc` en todo `app/`. Cero coincidencias fuera de
comentarios.

**Historial sin poda:** verificado buscando cualquier recorte de `CHAT_SESSIONS`
o del historial en `app/`. Cero coincidencias. Contrastado con
`MAX_HISTORIAL_API = 20` en `functions/chat.ts`.

**Divergencia de precios:** `PRECIOS_VENTA_NETO` hardcodeado en
`functions/_shared/chat_tools.ts` frente a la deducción desde facturas en
`app/negocio/precios_venta.py`.

**Fallo de OpenRouter:** transcrito literal de `logs/agente_chat.log`, entradas
del 2026-07-30 (403 por límite de la clave) y del 2026-08-02 (400 de Google por
schemas de array sin `items` — este último ya corregido, con test de regresión).
