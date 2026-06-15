# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Proyecto

**Zigurat ERP — Agente Facturas**
Automatización de sincronización semanal de facturas electrónicas DTE del SII a PostgreSQL.
Empresa: Elaboradora y Comercializadora Vintage SPA (Zigurat Brewery).

---

## Comandos frecuentes

```bash
# Procesar XML de facturas (pipeline completo: parse → validate → sync)
/sync-facturas DTE_DDMMYYYY

# Procesar Notas de Crédito
/sync-nc NOMBRE_ARCHIVO

# Procesar facturas de compra
/sync-compras                     # Procesa XMLs en facturas-compras/ → actualiza precios + gastos_operativos

# Detectar y sincronizar XMLs pendientes
/monitoreo-facturas

# Consultar ventas en lenguaje natural
/consultar-ventas

# Pipeline de conciliación bancaria
/importar-transferencias          # 1. Importa Excel Itaú → movimientos_banco
/conciliar-banco                  # 2. Cruza transferencias con facturas
/flujo-caja                       # 3. Proyección 4 semanas
/agregar-gasto "desc" monto YYYY-MM-DD [proveedor] [categoría]

# Wiki de clientes (brain compilado estilo Karpathy)
/wiki-init                        # Genera todas las fichas de clientes desde cero
/perfil-cliente <nombre>          # Muestra perfil narrativo de un cliente
/wiki-lint                        # Audita consistencia wiki ↔ BD

# Costos de producción (capa B)
/actualizar-precio-insumo "Lupulo Citra" gr 9500 lupulo
/cargar-receta                    # Carga/actualiza receta + BOM desde JSON
/costos-sku                       # Tabla de costo unitario por SKU
/costos-sku --sku CREAM-330-C12   # Costo de un SKU específico
/costos-sku --receta "Cream Ale"  # Costos de todos los formatos de una cerveza

# Ejecutar scripts individuales (solo para debug, nunca en producción)
python scripts/parse_dte.py facturas/DTE_DDMMYYYY
python scripts/validate_changes.py changes.json
python scripts/sync_db.py changes.json
python scripts/wiki_update.py --ruts RUT1,RUT2 --origen "debug"
python scripts/wiki_update.py --todos --origen "debug"
python scripts/wiki_snapshot.py --todos            # refresh masivo de raw/clientes/
python scripts/wiki_lint.py
python scripts/actualizar_insumo.py "nombre" unidad precio cat
python scripts/cargar_receta.py recipe.json
python scripts/cargar_sku.py sku.json
python scripts/costo_sku.py [--sku COD | --receta NOMBRE]

# Migración de esquema (idempotente)
python scripts/migrate_flujo_caja.py
python scripts/migrate_costos_v2.py                # Migración esquema costos (capa B)
python scripts/migrate_gastos_operativos.py   # Crea tabla gastos_operativos
python scripts/migrate_costos_v3.py           # Corrige precios maestro_insumos + agrega insumos
python scripts/cargar_recetas_v2.py           # Recarga 4 recetas desde Recetas.xlsx
python scripts/sync_compras.py                # Procesa XMLs de compras (usar /sync-compras)

# Backup de la base de datos
python scripts/backup_db.py                   # Backup manual inmediato (la tarea corre sola a las 23:00)
```

---

## Arquitectura

### Pipeline DTE (núcleo del proyecto)

```
XML del SII (ISO-8859-1) → parse_dte.py → changes.json → validate_changes.py → sync_db.py → PostgreSQL
```

Los 3 scripts en `scripts/` son secuenciales y obligatorios. `sync_db.py` se bloquea si no existe el flag `.changes_validated` que deja `validate_changes.py`. Nunca ejecutar `sync_db.py` sin validación previa.

### Pipeline de conciliación bancaria

```
Excel Itaú (transferencias/) → import_transferencias.py → movimientos_banco
                                                              ↓
facturas pendientes (ventas sin fecha_pago) ← conciliar_banco.py → conciliaciones + ventas.fecha_pago
                                                              ↓
                                              flujo_caja.py → proyección 4 semanas
                                              (usa avg dias_pago por cliente + cuentas_por_pagar)
```

### Tablas PostgreSQL principales

| Tabla | Clave primaria | Propósito |
|-------|---------------|-----------|
| `ventas` | folio + tipo_documento | Facturas y NC emitidas |
| `clientes` | rut_cliente | Maestro de clientes (upsert) |
| `productos` | folio + tipo_documento + nombre | Líneas de detalle por factura |
| `movimientos_banco` | id (serial), unique en codigo_transferencia | Transferencias del Itaú |
| `conciliaciones` | folio_venta + movimiento_banco_id | Cruces banco↔factura |
| `cuentas_por_pagar` | id (serial) | Gastos programados para flujo de caja |

### Estructura de archivos

```
scripts/                    # Scripts Python del pipeline (NO mover ni renombrar)
  parse_dte.py              # Lee XML SII → changes.json
  validate_changes.py       # 12 validaciones sobre changes.json
  sync_db.py                # Inserta en PostgreSQL con transacción
  import_transferencias.py  # Excel Itaú → movimientos_banco
  conciliar_banco.py        # Cruza transferencias con facturas
  flujo_caja.py             # Proyección 4 semanas
  migrate_flujo_caja.py     # Migración de esquema (idempotente)
  wiki_update.py            # Genera/actualiza fichas en wiki/clientes/ (+ snapshot raw/)
  wiki_snapshot.py          # Refresh masivo de snapshots en raw/clientes/
  wiki_lint.py              # Audita consistencia wiki ↔ BD
facturas/                   # XMLs del SII (formato DTE_DDMMYYYY)
Notas de Credito/           # XMLs de Notas de Crédito
transferencias/             # Excel de transferencias Itaú
logs/                       # Logs de ejecución
raw/                        # Capa inmutable del patrón Karpathy (snapshots)
  clientes/                 # {rut}.json — sobrescribibles solo desde código
wiki/                       # Brain compilado de clientes (Markdown + Obsidian)
  index.md                  # Índice maestro corto con links a sub-índices
  log.md                    # Registro cronológico de operaciones
  clientes/                 # Ficha ejecutiva por cliente (.md)
  indices/                  # Sub-índices escalables: activos, morosos, incobrables
  conceptos/                # Páginas agregadas (top, morosos, inactivos)
    productos/              # Un concepto por producto principal
.claude/skills/             # Skills de Claude Code (12 activas)
  consultar-ventas/scripts/query_ventas.py  # Queries hardcodeadas
  monitoreo-facturas/scripts/detectar_pendientes.py
  reporte-semanal/scripts/reporte.py
  agregar-gasto/scripts/agregar_gasto.py
  wiki-init/                # Genera todas las fichas desde cero
  perfil-cliente/           # Consulta narrativa de ficha + BD
  wiki-lint/                # Audita consistencia
```

---

## Base de datos

| Parámetro | Valor |
|-----------|-------|
| Motor | PostgreSQL local |
| Puerto | 5432 |
| Base de datos | `dte_facturas_chile` |
| Usuario | `postgres` |
| Conexión | Credenciales en `.env`, cargadas por `_load_env()` en cada script |

MCP server configurado en `.mcp.json` para queries ad-hoc via `@modelcontextprotocol/server-postgres`.

---

## Estructura de facturación — CRÍTICO para cálculos de ingresos

Zigurat divide cada venta de barril en **dos líneas dentro de la misma factura**:

| Línea | Descripción | Precio ejemplo | Impuestos |
|-------|-------------|---------------|-----------|
| 1 | Producto (ej: "Barril 30L Cream Ale") | $20.000 neto | IVA 19% + Impuesto Adicional 20,5% (ILA) |
| 2 | "Logistica" | $35.370 neto | Solo IVA 19% |

**El precio real del barril es la SUMA de ambas líneas: $55.370 neto ($69.990 total con impuestos).**

Esta estructura se usa para optimizar la carga tributaria: el ILA (20,5%) solo aplica al ítem de cerveza, no a logística.

### Consecuencias para queries y cálculos:

- **Nunca usar `precio_unitario` de la tabla `productos` para estimar el precio de venta** — solo refleja una parte del precio real.
- **Para calcular ingresos reales por factura**: usar `COALESCE(monto_neto_ajustado, monto_neto)` de la tabla `ventas` — ya incluye ambas líneas sumadas.
- **Para calcular el precio real por barril**: dividir el neto de la factura por el número de barriles vendidos (contar solo el ítem de cerveza, no el de logística).
- **El ítem "Logistica" en `productos` NO es un servicio separado** — es parte del precio de la cerveza disfrazado para reducir ILA.
- **`impuesto_adicional` en `ventas`** = ILA (20,5%) aplicado solo sobre el valor neto del ítem cerveza.

### Precios de venta por barril 30L (neto, confirmados por el productor)

| Cerveza | Ítem cerveza | Ítem logística | **Total neto** | Total con impuestos |
|---------|-------------|----------------|---------------|---------------------|
| Cream Ale | $20.000 | $35.370 | **$55.370** | $69.990 |
| Scotch Ale | $20.000 | $35.370 | **$55.370** | $69.990 |
| Stout Café/Cacao | $25.000 | $50.000 | **$75.000** | $94.375 |
| Paint it Black | $38.000 | $60.000 | **$98.000** | $124.410 |

### Estructura de costos de producción

- **Mano de obra:** $300.000/semana = $300.000/lote (1 lote/semana, costo de retiros del productor y socio)
- **Servicios variables:** $185.000/lote (agua, luz, gas)
- **Lote estándar:** 540 litros → 513 litros envasables (5% merma) → ~17 barriles de 30L

---

## Reglas críticas para queries SQL

Aplicar **siempre** al construir cualquier SQL sobre esta base de datos:

| Regla | Razón |
|-------|-------|
| `COALESCE(monto_total_ajustado, monto_total)` — nunca `monto_total` solo | Las NC actualizan `monto_total_ajustado`; ignorarlo infla totales |
| `COALESCE(monto_neto_ajustado, monto_neto)` — nunca `monto_neto` solo | Misma razón |
| `WHERE tipo_documento != '61'` en sumas de ventas | Las NC ya están descontadas en campos ajustados — incluirlas = doble conteo |
| `tipo_documento` es **texto** (`'33'`, `'61'`) | Comparar siempre con comillas |
| `folio` puede requerir `folio::integer` | Se almacena como texto |
| `COUNT(DISTINCT rut_cliente)` para contar clientes únicos | `COUNT(*)` cuenta facturas |
| `impuesto_adicional` (ILA) puede ser 0 | No es obligatorio > 0 en maquila/servicios |
| **Estado de pago: `fecha_pago IS NULL` = pendiente, `IS NOT NULL` = pagada** | **NUNCA usar JOIN a `conciliaciones` para esto** (ver sección "Estado de pago") |

### Query canónica — Ventas reales por cliente

```sql
SELECT c.razon_social, v.rut_cliente,
       SUM(COALESCE(v.monto_total_ajustado, v.monto_total)) AS total_real
FROM ventas v
JOIN clientes c ON c.rut_cliente = v.rut_cliente
WHERE v.tipo_documento != '61'
GROUP BY v.rut_cliente, c.razon_social
ORDER BY total_real DESC;
```

### Cuándo usar MCP vs /consultar-ventas

- **Consultas de negocio frecuentes** → siempre `/consultar-ventas` (usa query_ventas.py con queries probadas)
- **Consultas ad-hoc** → MCP está bien, pero verificar que el SQL cumpla las reglas de arriba
- **Si MCP da resultados raros** → agregar el comando a `query_ventas.py`

---

## Notas de Crédito — Modelo de datos

Las NC se guardan con **montos negativos** en `ventas`. Al sincronizar una NC, `sync_db.py` actualiza en la factura referenciada:
- `monto_neto_ajustado` = neto original − NC
- `monto_total_ajustado` = total original − NC

---

## Estado de pago de facturas — FUENTE DE VERDAD ÚNICA

**Una factura está pagada ⟺ `ventas.fecha_pago IS NOT NULL`. Punto.**

Esta es la única definición válida de estado de cobro. Existe porque dos
instancias distintas del agente dieron respuestas contradictorias a "¿qué
facturas debe el cliente X?": una miró `fecha_pago`, otra hizo `LEFT JOIN
conciliaciones`. Ambas estaban mal.

| Campo | Rol | Regla |
|-------|-----|-------|
| `ventas.fecha_pago` | **Fuente de verdad** del estado de cobro | `NULL` = pendiente; con fecha = pagada |
| tabla `conciliaciones` | **Solo evidencia** bancaria de respaldo | Incompleta por diseño — NO usar para estado de pago |

**Por qué `conciliaciones` NO sirve como fuente de verdad:** los pagos
importados desde el Excel de seguimiento (`importar_pagos_excel.py`) escriben
`fecha_pago` pero **no** generan fila en `conciliaciones`. Determinar deuda con
un JOIN a `conciliaciones` cuenta esos pagos legítimos como deuda e infla el
saldo de casi todos los clientes.

**Invariante que debe cumplirse siempre:** toda factura con conciliación
bancaria debe tener `fecha_pago`. Es decir, `conciliaciones ⟹ fecha_pago`.
Auditar con `python scripts/lint_estado_pago.py` (debe reportar 0
inconsistencias). La corrigió `migrate_backfill_fecha_pago.py` (160 facturas de
una carga masiva del 2026-01-25 que insertó conciliaciones sin `fecha_pago`).

### Cómo consultar deuda — siempre así

```bash
/consultar-ventas → pendientes --nombre "VDT SPA"   # deuda de un cliente (nombre o RUT)
/consultar-ventas → pendientes                       # deuda total (213 facturas)
```

```sql
-- Query canónica de facturas pendientes de cobro
SELECT v.folio, v.fecha, c.razon_social,
       COALESCE(v.monto_total_ajustado, v.monto_total) AS total
FROM ventas v
JOIN clientes c ON c.rut_cliente = v.rut_cliente
WHERE v.tipo_documento != 61
  AND v.fecha_pago IS NULL
  AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
ORDER BY v.fecha;
```

> Nota: en esta BD `tipo_documento` y `folio` son **integer** (no texto). El
> casteo `folio::integer` o comparar con `'61'` funciona igual, pero `!= 61`
> sin comillas es lo correcto.

---

## Hooks y protecciones

- **PreToolUse hook** en Edit/Write: bloquea ediciones a `changes.json` (archivo temporal generado por `parse_dte.py`)
- **Flag `.changes_validated`**: creado por `validate_changes.py`, requerido por `sync_db.py`, borrado tras sync exitoso

---

## Workflow de conciliación bancaria

```
1. Descargar ConsultaTransferencia.xlsx del Itaú → transferencias/
2. /importar-transferencias  →  movimientos_banco
3. /conciliar-banco          →  cruza transferencias con facturas, confirmar → fecha_pago
4. /flujo-caja               →  proyección 4 semanas (usa avg dias_pago + cuentas_por_pagar)
5. /agregar-gasto            →  registrar gastos futuros para mejorar proyección
```

RUTs en `movimientos_banco` se normalizan al formato `77126823-4` (con guión, sin puntos).

---

## Wiki de clientes (Karpathy LLM Wiki)

Brain compilado en Markdown que funciona como alternativa a RAG: cada cliente
tiene una ficha ejecutiva (~30 líneas) con métricas, patrón de pago, y notas
del agente. Las fichas se consultan con `/perfil-cliente` y son compatibles
con Obsidian (graph view, backlinks).

### Flujo de actualización

```
1. /wiki-init                 → genera TODAS las fichas desde BD (una sola vez)
2. Cada sync/conciliación     → actualiza solo los RUTs afectados (auto, no-bloqueante)
3. /perfil-cliente <nombre>   → lee ficha + complementa con BD en tiempo real
4. /wiki-lint                 → audita: fichas faltantes, huérfanas o desactualizadas
```

### Regeneración vs preservación

`wiki_update.py` regenera completamente cada ficha excepto la sección
**"Notas del agente"**, que es append-only. Los eventos notables (facturas
vencidas >30 días, multi-pagos en misma transferencia, cliente inactivo >60
días) se detectan automáticamente con `detectar_eventos()` y se anexan como
viñetas con fecha.

### Capa raw/ — snapshots inmutables (fuente de verdad histórica)

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

### Integración en skills existentes

Las skills `/sync-facturas`, `/sync-nc`, `/monitoreo-facturas` y
`/conciliar-banco` llaman a `wiki_update.py --ruts` como **último paso
no-bloqueante**: si falla solo muestra warning, no rompe el pipeline de datos.

### Estructura de ficha

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

### Conceptos y sub-índices

Además de las fichas por cliente, `wiki_update.py` regenera:
- `wiki/conceptos/clientes-top.md` — top 10 por ventas
- `wiki/conceptos/clientes-morosos.md` — vencidas >30 días
- `wiki/conceptos/clientes-inactivos.md` — >60 días sin compra
- `wiki/conceptos/productos/<slug>.md` — un archivo por producto con sus top 10 compradores
- `wiki/indices/{activos,morosos,incobrables}.md` — sub-índices escalables

El `index.md` principal es un resumen corto que enlaza a los sub-índices y conceptos.
Preparado para escalar a 500+ clientes sin saturar contexto.

---

## Costos de producción (capa B)

Calcula el costo unitario real de cada SKU vendible (cerveza × formato)
combinando insumos de líquido + envasado + mano de obra + servicios
variables del lote.

### Tablas

| Tabla | Propósito |
|-------|-----------|
| `maestro_insumos` | Catálogo de insumos con `categoria` (malta, lupulo, levadura, adjunto, clarificante, envase, tapa, etiqueta, caja) y `precio_neto_unitario`. |
| `recetas` | Una fila por cerveza, con `costo_mano_obra_lote`, `costo_servicios_lote` y `merma_porcentaje`. |
| `receta_detalle` | BOM de líquido por receta. |
| `formatos` | Catálogo plano: Botella 330ml / Barril 30L acero / Barril 30L PET. |
| `sku` | Una fila por (receta, formato, unidades_caja). Caja 12 y caja 24 son SKUs distintos. |
| `sku_envasado` | BOM de envasado por SKU. Vacío para barriles retornables. |

### Vista

`vista_costo_sku` entrega `costo_liquido_unitario`, `costo_envasado_unitario`
y `costo_total_unitario` por cada SKU activo. **Nunca calcular costo a mano** — consultar siempre la vista.

### Flujo de uso

```
/actualizar-precio-insumo  → mantiene maestro_insumos
/cargar-receta             → mantiene recetas + receta_detalle
/cargar-sku (CLI)          → mantiene sku + sku_envasado
/costos-sku                → consulta vista_costo_sku
```

### Parámetros estándar (lote 540 L, 4 lotes/mes)

- Mano de obra: $300.000/lote (retiros tuyo + socio).
- Servicios variables (agua/luz/gas): $185.000/lote.
- Merma de envasado: 5%.

Editables por receta.

### Lo que NO hace esta capa

- No descuenta inventario al producir.
- No registra órdenes de producción.
- No prorratea costos fijos / overhead (capa C).
- No procesa DTEs recibidos (sub-proyecto aparte).

---

## Backup de la base de datos

Backup diario automatizado (Tarea Programada de Windows "Zigurat - Backup BD",
23:00, corre al encender si el notebook estaba apagado):

- **Script:** `scripts/backup_db.py` — pg_dump formato custom comprimido,
  verificado con `pg_restore --list` antes de quedar firme.
- **Destino:** `C:\Users\cdela\OneDrive\Backups\zigurat-db\` (OneDrive lo sube
  a la nube). `_estado.json` ahí mismo registra el último intento y último OK.
- **Retención:** 60 días de dumps diarios + el primer dump de cada mes para
  siempre.
- **Log:** `logs/backup_db.log`.
- **Restaurar:** procedimiento completo en el docstring de `backup_db.py`
  (createdb + pg_restore; selectivo por tabla con `-t`).
- **Reinstalar la tarea** (cambio de hora o de ruta del proyecto):
  `powershell -ExecutionPolicy Bypass -File scripts\instalar_tarea_backup.ps1`.

El spec completo está en `docs/superpowers/specs/2026-06-11-backup-bd-design.md`.

---

## Convenciones del proyecto

- XMLs del SII van en `facturas/` con nombre `DTE_DDMMYYYY`
- XMLs de NC van en `Notas de Credito/`
- Encoding XML: ISO-8859-1 (latin-1)
- `changes.json` es temporal — no editarlo manualmente
- Todos los scripts cargan `.env` con `_load_env()` (no usan python-dotenv)
- Transacciones: `sync_db.py` usa `with conn:` para commit automático o rollback completo

---

## Dependencias

```
Python 3.x
psycopg2-binary
pandas
openpyxl
```
