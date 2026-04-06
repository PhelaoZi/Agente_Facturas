---
name: consultar-ventas
description: >
  Responde preguntas sobre ventas de Zigurat Brewery consultando PostgreSQL.
  Usala cuando el usuario pregunte sobre ventas, clientes, montos, facturas,
  rankings o periodos. Ejemplos de activacion: "cuanto vendio Marina?",
  "top 5 clientes", "ventas de enero", "total vendido esta semana",
  "busca la factura de Primos", "que cliente compro mas", "resumen de ventas".
  Interpreta la pregunta, genera SQL seguro (solo SELECT), ejecuta y muestra resultados.
disable-model-invocation: false
allowed-tools: Bash(python *)
---

# Consultar Ventas — Zigurat ERP

Traduce preguntas en lenguaje natural a comandos del script `query_ventas.py` y presenta resultados.

## Comandos disponibles

```
python .claude/skills/consultar-ventas/scripts/query_ventas.py <tipo> [opciones]

ranking   [--limit N]                              Top N clientes (default 10)
cliente   --nombre "NOMBRE"                        Ventas de un cliente
periodo   --desde YYYY-MM-DD --hasta YYYY-MM-DD    Ventas por rango de fechas (agrupado por cliente)
listado   --desde YYYY-MM-DD --hasta YYYY-MM-DD    Facturas individuales por rango (folio por folio)
facturas  --nombre "NOMBRE"                        Facturas de un cliente
total                                              Total global vendido
producto  --nombre "NOMBRE"                        Buscar por producto
detalle   --folio FOLIO                            Detalle de una factura
resumen                                            Estadisticas generales
```

## Paso 1 — Interpretar y ejecutar

Analiza la pregunta del usuario y elige el comando apropiado:

- "cuanto vendio [nombre]?" → `cliente --nombre "nombre"`
- "top N clientes" → `ranking --limit N`
- "ventas de [mes]" → `periodo --desde YYYY-MM-DD --hasta YYYY-MM-DD`
- "todas las facturas de [mes]" → `listado --desde YYYY-MM-DD --hasta YYYY-MM-DD`
- "dame los folios de [mes]" → `listado --desde YYYY-MM-DD --hasta YYYY-MM-DD`
- "listado de facturas de [periodo]" → `listado --desde YYYY-MM-DD --hasta YYYY-MM-DD`
- "que facturas emiti en [mes]" → `listado --desde YYYY-MM-DD --hasta YYYY-MM-DD`
- "facturas de [nombre]" → `facturas --nombre "nombre"`
- "total vendido" → `total`
- "busca [producto]" → `producto --nombre "producto"`
- "factura [folio]" → `detalle --folio FOLIO`
- "resumen" → `resumen`

**Regla clave:** Cuando el usuario pida facturas individuales (folios, listado, "todas las facturas de X mes") usar `listado`. Cuando pida totales o resumen por cliente de un periodo, usar `periodo`.

Ejecuta el comando de inmediato. NUNCA construyas SQL manualmente — SIEMPRE usa el script.

## Paso 2 — Presentar

Muestra el output del script tal cual. Si el usuario pide mas detalle o un corte diferente, ejecuta otro comando.

Si falla la conexion: reportar el error y detener.
Si retorna 0 resultados: informar al usuario.

## Reglas

- NUNCA escribas SQL ni uses `python -c` — SIEMPRE usa query_ventas.py
- NUNCA pidas confirmacion antes de ejecutar
- SIEMPRE presenta el output del script directamente al usuario
