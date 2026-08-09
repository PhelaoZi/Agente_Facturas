# Sacar el SDK de Anthropic del agente — diseño

**Fecha:** 2026-08-09
**Estado:** aprobado, pendiente de plan de implementación

---

## Por qué

`requirements.txt` declara `claude-agent-sdk==0.2.93` como "la dependencia más
sensible del proyecto". Pero el loop del agente **es propio desde el 2026-07-20**
(migración a OpenRouter). Del SDK solo quedan cuatro líneas idénticas:

```python
from claude_agent_sdk import create_sdk_mcp_server, tool
```

Es decir: se paga la dependencia más pesada y volátil del proyecto para usar un
decorador que genera un JSON Schema. Arrastra `mcp` y `jsonschema`; el import
tarda ~6 segundos, al punto de haber tenido que escribir `precalentar_sdk()` en
`dashboard.py` para esconderlos en el arranque.

**Pero la razón de fondo no es la dependencia: es un defecto que impone.**

### El defecto

El atajo `{"receta": str}` se traduce dentro del SDK así:

```python
return {
    "type": "object",
    "properties": properties,
    "required": list(properties.keys()),   # TODOS obligatorios
}
```

El atajo **no tiene forma de expresar "este parámetro es opcional"**. Todo lo que
se declara queda obligatorio. Resultado real, sacado del sistema:

```json
"description": "Total vendido. Opcional: rango desde/hasta (YYYY-MM-DD).",
"properties": { "desde": {...}, "hasta": {...} },
"required": ["desde", "hasta"]
```

La descripción dice "Opcional" y la regla dice "obligatorio". Gana la regla: es
un campo estructurado, y algunos proveedores lo validan.

Mientras tanto, el código de Python fue escrito para el otro caso:

```python
def total(cur, desde=None, hasta=None):
    if desde and hasta:
        ...WHERE v.fecha BETWEEN %s AND %s    # con fechas: el rango
    else:
        ...WHERE v.tipo_documento != 61       # sin fechas: TODO el histórico
```

Ese `else` está escrito, está probado y **hoy es inalcanzable desde el agente**.

**Consecuencia de negocio:** ante "¿cuánto hemos vendido en total?", el modelo
está obligado a inventar un rango de fechas, y devuelve un total parcial que
parece el total. No falla, no avisa: entrega una cifra más chica con confianza.
Lo mismo con `margenes`, que exige elegir una receta para ver todo el catálogo.

Son **17 de 33 tools** las que declaran obligatorio algo que el código trata como
opcional.

---

## Alcance

### Entra

1. **Reemplazar el SDK** por un decorador y un registro propios.
2. **Arreglar `required`** en las 17 tools afectadas.
3. **Cabecera de alcance** en las 9 tools cuyo filtro opcional cambia qué cubre
   la cifra.

Las tres tocan los mismos archivos y las mismas definiciones, y hay dependencia
real entre ellas: (1) habilita (2), y (2) obliga a (3) — al poder omitir el
filtro, la respuesta sin alcance explícito pasa de ser el caso raro al caso
normal.

### No entra

- Cambiar los nombres `mcp__servidor__tool`. Están escritos ~30 veces en el
  system prompt y posiblemente en las notas de memoria persistente del agente.
  Cambiarlos es reescribir el prompt y arriesgar referencias perdidas a cambio de
  estética.
- Simplificar el retorno `{"content": [{"type": "text", "text": ...}]}` a texto
  pelado. Es ceremonia heredada de MCP y ya no hace falta, pero toca el cuerpo de
  las 33 tools y sus tests. Mezclarlo aquí haría imposible saber si una falla
  vino de sacar el SDK o de cambiar el retorno. Queda para otro día.
- Tocar el system prompt más allá de la línea del alcance.
- Tocar el orquestador fuera del bloque de MCP.
- El runtime de la nube (`functions/`), que no usa el SDK.

---

## Diseño

### `app/agent/tools_base.py` (nuevo)

Dos piezas:

```python
def tool(nombre, descripcion, parametros, opcionales=()):
    """Declara una tool. `opcionales` son los parámetros que el modelo PUEDE
    omitir; el resto va en `required`.

    `parametros` acepta las dos formas que ya usa el proyecto:
      - atajo:  {"receta": str}          → se traduce a JSON Schema
      - schema: {"type": "object", ...}  → se usa tal cual (SCHEMA_GRAFICO)
    """


class Registro:
    """Un grupo de tools bajo un prefijo (negocio, lienzo, acciones, memoria).
    Reemplaza al servidor MCP in-process."""

    def schemas_openai(self) -> list[dict]:
        """Los dicts listos para el array `tools` de la API, con el prefijo
        `mcp__<prefijo>__` ya aplicado al nombre."""

    async def ejecutar(self, nombre_completo: str, args: dict) -> str:
        """Busca la tool por su nombre completo, llama al handler y devuelve el
        texto concatenado de su `content`."""
```

**Conversión de tipos** (equivalente a la del SDK, reducida a lo que el proyecto
usa): `str` → `{"type": "string"}`, `int` → `{"type": "integer"}`, `float` →
`{"type": "number"}`, `bool` → `{"type": "boolean"}`. Cualquier lista o
estructura se declara con JSON Schema explícito, como ya se hace en
`SCHEMA_GRAFICO` y `SCHEMA_TABLA`.

**`required`** = las claves de `properties` menos las de `opcionales`.

**Decisión de diseño — por qué `opcionales=()` y no tipos:** se descartó
`str | None` porque se lee como "puede ser nulo", no como "puedes omitirlo".
`opcionales=` es explícito, se puede buscar con grep, y hace el test trivial.

### Los cuatro `build_*_server()`

Mantienen su firma y siguen devolviendo `(algo, tool_names)`. Lo que devuelven
pasa a ser un `Registro` en vez de un `McpSdkServerConfig`. Los llamadores que
hoy hacen `cfg["instance"]` pasan a recibir el `Registro` directamente.

### El orquestador

Los dos bloques de MCP se reemplazan. Armado de la lista de tools:

```python
# antes: ~28 líneas recorriendo request_handlers[ListToolsRequest]
# después:
openai_tools = [s for r in registros for s in r.schemas_openai()]
openai_tools.append(SCHEMA_POSTGRES_QUERY)
```

Ejecución de una tool. El orquestador arma **un solo índice** `{nombre_completo:
registro}` al construir los registros, y despacha por ahí — sin necesidad de
saber a qué servidor pertenece cada tool:

```python
# antes: ~40 líneas armando CallToolRequest y desarmando res_call.root.content
# después:
contenido = await indice[nombre_tool].ejecutar(nombre_tool, args)
```

Ese índice reemplaza al `mcp_tools_map` actual, que guardaba por tool el
servidor, el nombre corto y el handler del protocolo.

El manejo de errores se conserva: una tool que falla devuelve su mensaje de error
como texto, no voltea el turno.

**Nota:** el orquestador hoy ignora el flag `is_error` de los resultados (solo
extrae el texto). Se mantiene ese comportamiento; cambiarlo es otro trabajo.

---

## Tabla de `required` — las 33 tools

Criterio aplicado: **se marca opcional solo lo que el código ya trata como
opcional** — usa `.get()` con default, o tolera `None`. Donde el código hace
`args["x"]` (revienta si falta), queda obligatorio. No se decide qué *debería*
ser opcional: se hace que el schema diga lo que el código ya hace.

### negocio (16)

| Tool | Obligatorios | Opcionales | Cambia |
|---|---|---|---|
| `deuda_total` | — | — | no |
| `deuda_cliente` | `nombre` | — | no |
| `ranking_deudores` | — | `limite` | **sí** |
| `facturas_vencidas` | — | `dias` | **sí** |
| `ventas_total` | — | `desde`, `hasta` | **sí** |
| `ranking_clientes` | — | `limite` | **sí** |
| `ventas_cliente` | `nombre` | — | no |
| `ventas_producto` | `nombre` | — | no |
| `flujo_caja` | — | `saldo_inicial` | **sí** |
| `costos_sku` | — | `receta` | **sí** |
| `margenes` | — | `receta` | **sí** |
| `margen_periodo` | `desde`, `hasta` | — | no |
| `margen_cliente` | `cliente` | `receta` | **sí** |
| `listar_gastos` | — | `filtro` | **sí** |
| `clientes_en_riesgo` | — | — | no |
| `listar_seguimiento` | — | `estado` | **sí** |

### lienzo (5)

| Tool | Obligatorios | Opcionales | Cambia |
|---|---|---|---|
| `publicar_kpi` | `etiqueta`, `valor` | `delta` | **sí** |
| `publicar_grafico` | `titulo`, `chart_type`, `x`, `y` | — | no (JSON Schema explícito) |
| `publicar_tabla` | `titulo`, `columnas`, `filas` | — | no (JSON Schema explícito) |
| `publicar_informe` | `titulo`, `markdown` | — | no |
| `publicar_consulta` | `ref`, `titulo` | — | no |

`publicar_consulta` solo se registra cuando el orquestador pasa un
`ResultadosSQL`. Por eso el catálogo es de 33 tools en producción y de 32 al
instanciar el lienzo sin ese argumento.

### acciones (10)

| Tool | Obligatorios | Opcionales | Cambia |
|---|---|---|---|
| `proponer_gasto` | `descripcion`, `monto`, `fecha` | `proveedor`, `categoria` | **sí** |
| `proponer_borrar_gasto` | `id` | — | no |
| `proponer_marcar_gasto_pagado` | `id` | `fecha` | **sí** |
| `proponer_editar_gasto` | `id` | `descripcion`, `monto`, `fecha`, `proveedor`, `categoria` | **sí** |
| `proponer_agregar_seguimiento` | `rut_cliente`, `motivo` | `cliente`, `prioridad`, `senales` | **sí** |
| `proponer_marcar_seguimiento` | `id`, `estado` | — | no |
| `proponer_marcar_factura_pagada` | `folio` | `fecha` | **sí** |
| `proponer_corregir_fecha_pago` | `folio`, `fecha` | — | no |
| `proponer_marcar_cliente_incobrable` | `cliente` | — | no |
| `proponer_reactivar_cliente` | `cliente` | — | no |

Respaldo de los dos casos que podrían discutirse:

- `proponer_gasto`: `validar_gasto(descripcion, monto, fecha, proveedor=None,
  categoria=None)` — los dos últimos ya tienen default.
- `proponer_agregar_seguimiento`: `validar_agregar` solo levanta `ValueError`
  por `rut_cliente` y `motivo` vacíos. `prioridad` recibe default `"media"` en la
  propia tool y `cliente` es solo para mostrar en la tarjeta.
- `proponer_corregir_fecha_pago` mantiene `fecha` **obligatoria** a propósito: la
  descripción dice "la fecha correcta (obligatoria)". Corregir una fecha sin
  decir a cuál no tiene sentido.

### memoria (2)

| Tool | Obligatorios | Opcionales | Cambia |
|---|---|---|---|
| `guardar_nota` | `titulo`, `contenido` | `tipo` | **sí** |
| `leer_nota` | `nombre` | — | no |

**Total: 17 cambian, 16 quedan igual.**

Los dos de mayor efecto sobre el comportamiento del agente:

- **`proponer_editar_gasto`**: hoy la descripción dice literalmente "Pasa solo
  los campos a cambiar" y el schema exige los 6. El modelo tiene que mandar todo
  para cambiar una fecha.
- **`ventas_total`**: hoy el agente no puede pedir el total histórico.

---

## Cabeceras de alcance

Nueve tools van a declarar con qué filtro respondieron. **La cabecera la arma
Python con los argumentos que de verdad recibió** — no el modelo. Si se le
pidiera al modelo por prompt, algún día se le olvida; si la escribe el código, no
puede mentir. Y como queda en el resultado de la tool, el modelo la tiene delante
al redactar.

> **Corrección durante la implementación.** Este documento prometía cabeceras
> del tipo `(de 18 clientes con deuda)`. No se pudo: el `LIMIT` lo aplica el SQL
> de `top_deudores` y `ranking`, así que la tool **no conoce el total** y
> saberlo exigiría una segunda consulta en cada llamada. Se usó lo que sí es
> gratis y exacto: si el ranking llenó el cupo, avisa que puede haber más; si no
> lo llenó, dice que son todos. Cumple el objetivo (que el usuario sepa que está
> viendo una punta) sin costo extra. Lo mismo con los filtros de catálogo:
> se nombra el filtro aplicado y el total devuelto, no la proporción.

| Tool | Sin filtro | Con filtro |
|---|---|---|
| `ventas_total` | `Ventas (todo el histórico, sin filtro de fecha): …` | ya lo hace bien |
| `ranking_deudores` | `Top deudores (se muestran los N mayores, puede haber más) …` | igual |
| `ranking_clientes` | `Top clientes por ventas (son todos los que hay) …` | igual |
| `facturas_vencidas` | `Facturas pendientes con más de N días: …` | igual |
| `costos_sku` | `Costos de todo el catálogo (N SKU): …` | `Costos filtrados por receta "X" (N de M SKU): …` |
| `margenes` | `Márgenes de todo el catálogo (N SKU): …` | `Márgenes filtrados por receta "X" (N de M SKU): …` |
| `margen_cliente` | `Márgenes al precio de <cliente> (todo el catálogo): …` | `… filtrados por receta "X"` |
| `listar_gastos` | `Todos los gastos pendientes (N): …` | `Gastos que calzan con "X" (N de M): …` |
| `listar_seguimiento` | `Seguimientos en estado "pendiente" (N): …` | `… en estado "X" (N): …` |

Cuando el resultado es una lista larga que se publica en el lienzo, la cabecera
va **antes** del resumen, sin alterar el mecanismo de `tabla_o_resumen()`.

No se tocan las que ya declaran su alcance: `flujo_caja` (dice el saldo inicial),
`margen_periodo` (dice el rango), `deuda_cliente` y `ventas_cliente` (nombran al
cliente).

**System prompt:** se agrega una línea — cuando el resultado de una herramienta
traiga un alcance explícito, repetirlo en la respuesta. El prompt refuerza; el
código es la fuente de verdad.

---

## Lo que se borra

| Qué | Dónde | Efecto |
|---|---|---|
| `precalentar_sdk()` + su llamada en `main()` | `app/dashboard.py` | **el dashboard abre ~6s antes** |
| Sus 2 tests | `tests/test_dashboard_seguridad.py` | |
| Armado de tools vía `ListToolsRequest` (~28 líneas) | `app/agent/orchestrator.py` | |
| Ejecución vía `CallToolRequest` (~40 líneas) | `app/agent/orchestrator.py` | |
| `claude-agent-sdk==0.2.93` | `requirements.txt` | se van `mcp`, `jsonschema`, `fastmcp` |
| `import` de `mcp.types` | `tests/test_orchestrator.py`, `tests/test_publish_tools.py`, `tests/test_tools_negocio.py` | los tests quedan más cortos |

---

## Tests

### Nuevos

1. **`required` declarado = required real.** Para las 33 tools, que los
   obligatorios del schema sean exactamente los parámetros menos los
   `opcionales`. Fija por escrito la tabla de arriba: si alguien agrega una tool
   con el atajo y se olvida de marcar lo opcional, el test lo dice.

2. **Ninguna tool revienta sin sus opcionales.** Llamar cada tool omitiendo
   *todos* sus parámetros opcionales y verificar que no lanza `KeyError`. Es la
   red que atrapa un error de criterio en la tabla. Las tools que tocan la BD se
   ejercitan con el cursor falso que ya usan los tests actuales.

3. **Los schemas siguen siendo válidos para todos los proveedores.** Adaptar
   `test_todo_array_declara_items` al registro nuevo: todo parámetro de tipo
   `array` debe declarar `items`, o Google rechaza la petición entera con HTTP
   400 (visto el 2026-08-02).

### Adaptados

Los que hoy invocan tools armando objetos MCP pasan a llamar
`registro.ejecutar(nombre, args)`. El cambio los acorta.

### Verificación final

`python -m pytest -q` en verde, y una pasada manual por el chat del dashboard con
al menos: una pregunta de ventas sin período, un `margenes` sin receta, y una
edición de gasto de un solo campo.

---

## Riesgos

| Riesgo | Control |
|---|---|
| Marcar opcional algo que el código sí necesita → `KeyError` en producción | Test 2, más el criterio de solo marcar lo que ya usa `.get()` |
| El eco de alcance cambia cómo redacta el modelo | Es el objetivo, pero se revisa en uso real antes de commitear |
| Otra parte del proyecto usa `mcp` o `claude-agent-sdk` | Verificar con grep antes de tocar `requirements.txt`; si aparece un uso, se documenta y se decide aparte |
| Se rompe una acción de escritura | Ninguna acción escribe: todas siguen siendo propuestas con confirmación humana y validación determinista. Ese mecanismo no se toca |
| Un proveedor rechaza el schema nuevo | Test 3 y la pasada manual por el chat |

---

## Criterios de aceptación

1. `grep -rn "claude_agent_sdk\|mcp\.types" app/ tests/ scripts/` no devuelve
   nada.
2. `claude-agent-sdk` no está en `requirements.txt`, y el chat del dashboard
   funciona con el paquete desinstalado.
3. `python -m pytest -q` en verde, incluidos los tres tests nuevos.
4. El schema de las 33 tools coincide con la tabla de este documento.
5. Las 9 tools con filtro opcional declaran su alcance, con filtro y sin él.
6. El dashboard abre sin el retardo del import del SDK.
7. Ninguna acción de escritura cambió su camino: siguen siendo propuesta →
   confirmación → endpoint determinista.
