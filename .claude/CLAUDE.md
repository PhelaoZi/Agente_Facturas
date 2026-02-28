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

# Detectar y sincronizar XMLs pendientes
/monitoreo-facturas

# Consultar ventas en lenguaje natural
/consultar-ventas

# Pipeline de conciliación bancaria
/importar-transferencias          # 1. Importa Excel Itaú → movimientos_banco
/conciliar-banco                  # 2. Cruza transferencias con facturas
/flujo-caja                       # 3. Proyección 4 semanas
/agregar-gasto "desc" monto YYYY-MM-DD [proveedor] [categoría]

# Ejecutar scripts individuales (solo para debug, nunca en producción)
python scripts/parse_dte.py facturas/DTE_DDMMYYYY
python scripts/validate_changes.py changes.json
python scripts/sync_db.py changes.json

# Migración de esquema (idempotente)
python scripts/migrate_flujo_caja.py
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
facturas/                   # XMLs del SII (formato DTE_DDMMYYYY)
Notas de Credito/           # XMLs de Notas de Crédito
transferencias/             # Excel de transferencias Itaú
logs/                       # Logs de ejecución
.claude/skills/             # Skills de Claude Code (9 activas)
  consultar-ventas/scripts/query_ventas.py  # Queries hardcodeadas
  monitoreo-facturas/scripts/detectar_pendientes.py
  reporte-semanal/scripts/reporte.py
  agregar-gasto/scripts/agregar_gasto.py
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
