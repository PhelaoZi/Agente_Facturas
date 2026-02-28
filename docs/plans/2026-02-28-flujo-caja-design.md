# Diseño: Módulo de Flujo de Caja y Conciliación Bancaria

**Fecha:** 2026-02-28
**Empresa:** Elaboradora y Comercializadora Vintage SPA (Zigurat Brewery)
**Objetivo:** Proyectar el flujo de caja semanal cruzando facturas por cobrar con el historial de comportamiento de pago de clientes, y automatizar la conciliación entre transferencias bancarias y facturas emitidas.

---

## Contexto

- Todas las ventas son con crédito (no hay pago contado)
- Los clientes pagan entre 4 y 45 días después de emitida la factura
- El banco (Itaú Empresas) permite exportar transferencias recibidas en Excel
- En la BD ya existen 256 facturas con `fecha_pago` y `dias_pago` registrados (base histórica)
- Hay 282 facturas desde 2025 sin fecha_pago → ~$39.7M por cobrar
- Las tablas `movimientos_banco` y `conciliaciones` ya existen con la estructura correcta

---

## Modelo de Datos

### Nueva tabla: `cuentas_por_pagar`

```sql
CREATE TABLE cuentas_por_pagar (
    id                SERIAL PRIMARY KEY,
    descripcion       VARCHAR(255) NOT NULL,
    proveedor         VARCHAR(255),
    monto             NUMERIC NOT NULL,
    fecha_vencimiento DATE NOT NULL,
    recurrente        BOOLEAN DEFAULT FALSE,
    periodicidad      VARCHAR(20),    -- 'mensual', 'quincenal', 'semanal'
    pagado            BOOLEAN DEFAULT FALSE,
    fecha_pago        DATE,
    categoria         VARCHAR(50),    -- 'insumos', 'arriendo', 'servicios', etc.
    created_at        TIMESTAMPTZ DEFAULT NOW()
);
```

### Modificación a `movimientos_banco`

Agregar columna para deduplicar al importar:
```sql
ALTER TABLE movimientos_banco ADD COLUMN codigo_transferencia VARCHAR(20);
ALTER TABLE movimientos_banco ADD CONSTRAINT uq_codigo_transferencia UNIQUE (codigo_transferencia);
```

### Tablas existentes (sin cambios)

- `ventas`: ya tiene `fecha_pago` (date), `dias_pago` (int)
- `conciliaciones`: ya tiene `folio_venta`, `movimiento_banco_id`, `monto_aplicado`, `fecha_conciliacion`
- `movimientos_banco`: ya tiene `fecha`, `rut_emisor`, `nombre_emisor`, `monto_abono`, `conciliado`

---

## Scripts

### `scripts/import_transferencias.py`

**Entrada:** `transferencias/ConsultaTransferencia.xlsx`
**Formato Itaú:** fila 1-9 = cabecera del reporte, fila 10 = headers de columnas, fila 11+ = datos

**Lógica:**
1. Leer Excel con `openpyxl` (o `pandas`)
2. Parsear columnas:
   - `Fecha` → separar fecha (date) de hora
   - `Rut` → normalizar formato (agregar guión verificador si falta)
   - `Monto` → strip `$`, `.`, `,` → convertir a numeric
   - `Código transferencia` → string, clave de deduplicación
3. INSERT en `movimientos_banco` con `ON CONFLICT (codigo_transferencia) DO NOTHING`
4. Output: `N registros importados, N ya existían (duplicados omitidos)`

**Dependencias:** `pandas`, `psycopg2`

---

### `scripts/conciliar_banco.py`

**Entrada:** movimientos en `movimientos_banco` donde `conciliado = FALSE`

**Algoritmo de matching (por orden de prioridad):**

1. **Match exacto (alta confianza)**
   `monto_abono == monto_total` AND `rut_emisor == rut_cliente`
   → una transferencia cubre exactamente una factura

2. **Match múltiple (alta confianza)**
   `rut_emisor == rut_cliente` AND `monto_abono == SUM(monto_total de N facturas sin pagar)`
   → una transferencia cubre varias facturas del mismo cliente
   → tolerar diferencia ≤ $100 (redondeos)

3. **Sin match (excepción para revisión manual)**
   RUT coincide pero ninguna combinación de montos cuadra
   → mostrar en reporte como "requiere revisión"

**Flujo con confirmación:**
1. Ejecutar análisis → mostrar tabla con los 3 grupos
2. Preguntar `¿Confirmar conciliación? [s/N]`
3. Si confirma:
   - INSERT en `conciliaciones` (folio_venta, movimiento_banco_id, monto_aplicado)
   - UPDATE `ventas SET fecha_pago = fecha_transferencia, dias_pago = fecha_transferencia - fecha`
   - UPDATE `movimientos_banco SET conciliado = TRUE`
4. Output: resumen de facturas conciliadas y pendientes

---

### `scripts/flujo_caja.py`

**Horizonte:** 4 semanas desde hoy

**Saldo inicial:**
- Consultar último `saldo_diario` en `movimientos_banco` (fecha más reciente)
- Si no existe o está desactualizado (>7 días), solicitar al usuario el saldo actual

**Ingresos proyectados (cuentas por cobrar):**
- Para cada factura sin `fecha_pago` (`tipo_documento != '61'`):
  - Calcular `avg_dias` del cliente (últimas 10 facturas pagadas)
  - Si cliente sin historial suficiente (<3 facturas): usar promedio global (actualmente ~30 días)
  - `fecha_pago_proyectada = fecha_emision + avg_dias`
  - Agrupar por semana

**Egresos proyectados (cuentas por pagar):**
- `SELECT * FROM cuentas_por_pagar WHERE pagado = FALSE AND fecha_vencimiento <= hoy + 28 días`

**Output (tabla semanal):**
```
PROYECCIÓN FLUJO DE CAJA — 4 SEMANAS
Saldo inicial (hoy): $X.XXX.XXX

Semana       | Ingresos esperados | Egresos esperados | Saldo proyectado
-------------|-------------------|-------------------|------------------
01/03-07/03  | $X.XXX.XXX        | $XXX.XXX          | $X.XXX.XXX
08/03-14/03  | $X.XXX.XXX        | $XXX.XXX          | $X.XXX.XXX
...

DETALLE INGRESOS:
  - Cliente A (folio 4XXX, emitida DD/MM): $XXX.XXX → proyectado DD/MM (avg: N días)
  ...

DETALLE EGRESOS:
  - Arriendo oficina (vence DD/MM): $XXX.XXX
  ...
```

---

## Skills

### `/importar-transferencias`
**Archivo:** `.claude/skills/importar-transferencias/SKILL.md`
**Acción:** Llama a `scripts/import_transferencias.py`
**Precondición:** Debe existir al menos un `.xlsx` en `transferencias\`

### `/conciliar-banco`
**Archivo:** `.claude/skills/conciliar-banco/SKILL.md`
**Acción:** Llama a `scripts/conciliar_banco.py`
**Precondición:** Haber importado transferencias primero (`/importar-transferencias`)

### `/flujo-caja`
**Archivo:** `.claude/skills/flujo-caja/SKILL.md`
**Acción:** Llama a `scripts/flujo_caja.py`
**Muestra:** Proyección semana a semana con detalle de facturas

### `/agregar-gasto`
**Archivo:** `.claude/skills/agregar-gasto/SKILL.md`
**Uso:** `/agregar-gasto DESCRIPCION MONTO FECHA_VENCIMIENTO [PROVEEDOR] [CATEGORIA]`
**Acción:** INSERT directo en `cuentas_por_pagar`
**Ejemplo:** `/agregar-gasto "Arriendo bodega" 850000 2026-03-05 "Propietario SA" arriendo`

---

## Workflow Operacional Semanal

```
1. Descargar ConsultaTransferencia.xlsx del Itaú → dejar en transferencias\
2. /importar-transferencias  →  importa nuevos movimientos a movimientos_banco
3. /conciliar-banco           →  auto-match + reporte + confirmar → actualiza fecha_pago
4. /flujo-caja                →  proyección próximas 4 semanas
```

---

## Carpeta nueva

```
Agente_Facturas/
├── transferencias/          ← NUEVA: dejar aquí los Excel del banco
│   └── ConsultaTransferencia.xlsx
├── facturas/                (existente)
├── Notas de Credito/        (existente)
├── scripts/
│   ├── import_transferencias.py   ← NUEVO
│   ├── conciliar_banco.py         ← NUEVO
│   ├── flujo_caja.py              ← NUEVO
│   └── ... (existentes)
└── .claude/skills/
    ├── importar-transferencias/   ← NUEVO
    ├── conciliar-banco/           ← NUEVO
    ├── flujo-caja/                ← NUEVO
    └── agregar-gasto/             ← NUEVO
```

---

## Dependencias adicionales

```
pandas
openpyxl
```

(psycopg2 ya está instalado)
