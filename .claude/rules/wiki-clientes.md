---
paths:
  - "scripts/wiki_*.py"
  - "wiki/**"
  - "raw/**"
---

# Wiki de clientes (Karpathy LLM Wiki)

Brain compilado en Markdown que funciona como alternativa a RAG: cada cliente
tiene una ficha ejecutiva (~30 líneas) con métricas, patrón de pago, y notas
del agente. Las fichas se consultan con `/perfil-cliente` y son compatibles
con Obsidian (graph view, backlinks).

## Flujo de actualización

```
1. /wiki-init                 → genera TODAS las fichas desde BD (una sola vez)
2. Cada sync/conciliación     → actualiza solo los RUTs afectados (auto, no-bloqueante)
3. /perfil-cliente <nombre>   → lee ficha + complementa con BD en tiempo real
4. /wiki-lint                 → audita: fichas faltantes, huérfanas o desactualizadas
```

## Regeneración vs preservación

`wiki_update.py` regenera completamente cada ficha excepto la sección
**"Notas del agente"**, que es append-only. Los eventos notables (facturas
vencidas >30 días, multi-pagos en misma transferencia, cliente inactivo >60
días) se detectan automáticamente con `detectar_eventos()` y se anexan como
viñetas con fecha.

## Capa raw/ — snapshots inmutables (fuente de verdad histórica)

Siguiendo el patrón Karpathy, `raw/clientes/<rut>.json` contiene un snapshot
de los datos crudos del cliente cada vez que se regenera su ficha. Estos
archivos son **sobrescribibles solo desde código** (`wiki_update.py` o
`wiki_snapshot.py`) y **nunca se editan a mano**. Commiteables a git para
obtener `git diff` del estado del negocio entre ingestas.

`detectar_cambios_snapshot()` compara el snapshot anterior con los datos
actuales y emite eventos adicionales: cambio de estado, facturas nuevas,
caída en total vendido (posible NC no registrada), o aumento significativo
de deuda pendiente. Estos eventos se anexan a "Notas del agente" como los
demás.

Refresh masivo independiente del pipeline: `python scripts/wiki_snapshot.py --todos`.

## Integración en skills existentes

Las skills `/sync-facturas`, `/sync-nc`, `/monitoreo-facturas` y
`/conciliar-banco` llaman a `wiki_update.py --ruts` como **último paso
no-bloqueante**: si falla solo muestra warning, no rompe el pipeline de datos.

## Estructura de ficha

Cada `wiki/clientes/<slug>.md` tiene:
- Frontmatter YAML: `rut`, `razon_social`, `estado`, `ultima_actualizacion`
- Métricas: total facturado, ticket promedio, nº facturas, primera/última venta
- Estado de cuenta: pendiente, al día, vencido
- Patrón de pago: días promedio, comportamiento descriptivo
- Relacionados: `[[wikilinks]]` a 5 clientes que comparten el producto principal
- Inconsistencias: contra-argumentos detectados (incobrable con ventas recientes,
  notas contradictorias con BD, cambio de patrón de compra, etc.). "Ninguna detectada"
  si no hay problemas.
- Notas del agente: append-only, preserva observaciones entre regeneraciones

## Conceptos y sub-índices

Además de las fichas por cliente, `wiki_update.py` regenera:
- `wiki/conceptos/clientes-top.md` — top 10 por ventas
- `wiki/conceptos/clientes-morosos.md` — vencidas >30 días
- `wiki/conceptos/clientes-inactivos.md` — >60 días sin compra
- `wiki/conceptos/productos/<slug>.md` — un archivo por producto con sus top 10 compradores
- `wiki/indices/{activos,morosos,incobrables}.md` — sub-índices escalables

El `index.md` principal es un resumen corto que enlaza a los sub-índices y conceptos.
Preparado para escalar a 500+ clientes sin saturar contexto.
