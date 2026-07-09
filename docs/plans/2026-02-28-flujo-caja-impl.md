# Flujo de Caja y Conciliación Bancaria — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Agregar al ERP Zigurat el módulo de conciliación bancaria + proyección de flujo de caja de 4 semanas.

**Architecture:** 3 scripts Python en `scripts/` + 4 skills en `.claude/skills/`. Los scripts siguen el patrón existente (`_load_env()` + `DB_CONFIG` + `psycopg2`). Los skills orquestan los scripts con instrucciones en lenguaje natural.

**Tech Stack:** Python 3, psycopg2, pandas, openpyxl, PostgreSQL 14+

---

## Prerequisitos

Verificar dependencias antes de empezar:

```bash
pip install pandas openpyxl
```

Verificar que ya está instalado psycopg2:
```bash
python -c "import psycopg2; print('ok')"
```

---

### Task 1: Migración de base de datos

**Files:**
- Create: `scripts/migrate_flujo_caja.py`

**Step 1: Crear el script de migración**

```python
#!/usr/bin/env python3
"""
migrate_flujo_caja.py - Zigurat ERP
Migración para el módulo de flujo de caja y conciliación bancaria.
Idempotente: se puede ejecutar múltiples veces sin efectos secundarios.

Uso:
    python scripts/migrate_flujo_caja.py
"""

import os
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta la librería psycopg2.")
    print("Instala con: pip install psycopg2-binary")
    sys.exit(1)


def _load_env():
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

MIGRATIONS = [
    # 1. Columna codigo_transferencia en movimientos_banco para deduplicar
    """
    ALTER TABLE movimientos_banco
        ADD COLUMN IF NOT EXISTS codigo_transferencia VARCHAR(30)
    """,
    # 2. Índice único parcial: solo aplica a filas donde codigo no es NULL
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_movimientos_codigo_transferencia
        ON movimientos_banco (codigo_transferencia)
        WHERE codigo_transferencia IS NOT NULL
    """,
    # 3. Nueva tabla cuentas_por_pagar
    """
    CREATE TABLE IF NOT EXISTS cuentas_por_pagar (
        id                SERIAL PRIMARY KEY,
        descripcion       VARCHAR(255) NOT NULL,
        proveedor         VARCHAR(255),
        monto             NUMERIC NOT NULL,
        fecha_vencimiento DATE NOT NULL,
        recurrente        BOOLEAN DEFAULT FALSE,
        periodicidad      VARCHAR(20),
        pagado            BOOLEAN DEFAULT FALSE,
        fecha_pago        DATE,
        categoria         VARCHAR(50),
        created_at        TIMESTAMPTZ DEFAULT NOW()
    )
    """,
]


def main():
    print("=" * 60)
    print("ZIGURAT ERP — Migración Flujo de Caja")
    print("=" * 60)
    print()

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    print(f"✓ Conectado a: {DB_CONFIG['dbname']}")
    print()

    try:
        with conn:
            cur = conn.cursor()
            for i, sql in enumerate(MIGRATIONS, 1):
                cur.execute(sql)
                print(f"  ✓ Migración {i}/{len(MIGRATIONS)} aplicada")

        print()
        print("✅ Migración completada exitosamente")
        print()
        print("Tablas/columnas creadas o ya existían:")
        print("  - movimientos_banco.codigo_transferencia (VARCHAR 30)")
        print("  - movimientos_banco: índice único en codigo_transferencia")
        print("  - cuentas_por_pagar (nueva tabla)")
    except psycopg2.Error as e:
        print(f"\nERROR en migración: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

**Step 2: Ejecutar la migración**

```bash
python scripts/migrate_flujo_caja.py
```

Salida esperada:
```
============================================================
ZIGURAT ERP — Migración Flujo de Caja
============================================================

✓ Conectado a: dte_facturas_chile

  ✓ Migración 1/3 aplicada
  ✓ Migración 2/3 aplicada
  ✓ Migración 3/3 aplicada

✅ Migración completada exitosamente
```

**Step 3: Verificar en PostgreSQL**

```sql
-- Verificar columna nueva
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'movimientos_banco'
  AND column_name = 'codigo_transferencia';

-- Verificar tabla nueva
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'cuentas_por_pagar'
ORDER BY ordinal_position;
```

---

### Task 2: Crear carpeta transferencias\

**Step 1: Crear la carpeta**

```bash
mkdir -p transferencias
```

**Step 2: Verificar estructura**

```bash
ls -la | grep transferencias
```

Salida esperada: directorio `transferencias` aparece en el listado.

---

### Task 3: Script import_transferencias.py

**Files:**
- Create: `scripts/import_transferencias.py`

El Excel del Itaú tiene este formato:
- Filas 1-9: cabecera del reporte (logo, fecha, nombre del titular)
- Fila 10: headers de columnas (fondo naranja en el archivo real)
- Fila 11+: datos

Columnas (fila 10): Fecha | Rut | Nombre | Banco origen | Cuenta destino | Monto | Estado | Código transferencia

**Step 1: Crear el script**

```python
#!/usr/bin/env python3
"""
import_transferencias.py - Zigurat ERP
Importa el archivo Excel de transferencias recibidas del Itaú Empresas
a la tabla movimientos_banco en PostgreSQL.

Formato Excel esperado (ConsultaTransferencia.xlsx):
  - Filas 1-9: cabecera del reporte Itaú (se ignoran)
  - Fila 10:   headers de columnas
  - Fila 11+:  datos de transferencias

Uso:
    python scripts/import_transferencias.py [ruta_al_excel]

    Si no se pasa ruta, busca el .xlsx más reciente en transferencias/
"""

import os
import re
import sys
from pathlib import Path
from datetime import datetime

try:
    import pandas as pd
except ImportError:
    print("ERROR: Falta la librería pandas.")
    print("Instala con: pip install pandas openpyxl")
    sys.exit(1)

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta la librería psycopg2.")
    sys.exit(1)


def _load_env():
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}


def normalizar_rut(rut_raw):
    """
    Normaliza un RUT al formato '12345678-9' o '12345678-K'.
    El banco exporta RUTs como '77126823-4' o '771268234' o '77126823K'.
    """
    if not rut_raw:
        return None
    s = str(rut_raw).strip().upper().replace(".", "")
    # Si ya tiene guión, retornar tal cual (sin puntos)
    if "-" in s:
        return s
    # Sin guión: último char es dígito verificador
    if len(s) >= 2:
        return f"{s[:-1]}-{s[-1]}"
    return s


def parsear_monto(valor):
    """
    Parsea montos que pueden venir como float, int, o string '$1.234.567,00'.
    Retorna float.
    """
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip()
    # Remover símbolo de moneda y espacios
    s = re.sub(r'[$\s]', '', s)
    # Formato chileno: '1.234.567,00' (punto=miles, coma=decimal)
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        s = s.replace(',', '.')
    elif '.' in s and s.count('.') > 1:
        s = s.replace('.', '')
    try:
        return float(s)
    except ValueError:
        return None


def parsear_fecha(valor):
    """
    Parsea fechas del Excel del Itaú.
    Formatos posibles: datetime object, '26/02/2026', '26/02/2026 - 21:44:28'
    """
    if isinstance(valor, datetime):
        return valor.date()
    if hasattr(valor, 'date'):
        return valor.date()
    s = str(valor).strip()
    # Remover parte de hora si existe
    s = s.split(' - ')[0].split(' ')[0]
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def encontrar_excel(ruta_arg=None):
    """
    Retorna la ruta al Excel a importar.
    Si se pasa argumento, usa ese. Sino, busca el .xlsx más reciente en transferencias/
    """
    if ruta_arg:
        p = Path(ruta_arg)
        if not p.exists():
            print(f"ERROR: No se encontró el archivo: {ruta_arg}")
            sys.exit(1)
        return p

    carpeta = Path("transferencias")
    if not carpeta.exists():
        print("ERROR: No existe la carpeta transferencias/")
        print("Crea la carpeta y deposita el Excel del Itaú ahí.")
        sys.exit(1)

    archivos = sorted(carpeta.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not archivos:
        print("ERROR: No hay archivos .xlsx en la carpeta transferencias/")
        sys.exit(1)

    return archivos[0]


def leer_excel(ruta):
    """
    Lee el Excel del Itaú. Los headers están en la fila 10 (índice 9 en 0-based).
    Retorna DataFrame con columnas normalizadas.
    """
    try:
        df = pd.read_excel(ruta, header=9, engine='openpyxl')
    except Exception as e:
        print(f"ERROR leyendo Excel: {e}")
        sys.exit(1)

    # Normalizar nombres de columnas (quitar espacios, minúsculas)
    df.columns = [str(c).strip().lower().replace(' ', '_') for c in df.columns]

    # Eliminar filas completamente vacías
    df = df.dropna(how='all').reset_index(drop=True)

    return df


def mapear_columnas(df):
    """
    Mapea los nombres de columnas del Excel a los campos internos.
    El Itaú puede variar ligeramente los nombres entre exportaciones.
    """
    mapa = {
        'fecha':                  'fecha',
        'rut':                    'rut',
        'nombre':                 'nombre',
        'banco_origen':           'banco_origen',
        'cuenta_destino':         'cuenta_destino',
        'monto':                  'monto',
        'estado':                 'estado',
        'código_transferencia':   'codigo_transferencia',
        'codigo_transferencia':   'codigo_transferencia',
        'cód._transferencia':     'codigo_transferencia',
        'cod._transferencia':     'codigo_transferencia',
        'código':                 'codigo_transferencia',
    }

    renombrar = {}
    for col in df.columns:
        col_clean = col.lower().strip()
        if col_clean in mapa:
            renombrar[col] = mapa[col_clean]

    df = df.rename(columns=renombrar)

    # Verificar columnas mínimas requeridas
    requeridas = ['fecha', 'rut', 'nombre', 'monto']
    faltantes = [c for c in requeridas if c not in df.columns]
    if faltantes:
        print(f"ERROR: El Excel no tiene las columnas esperadas.")
        print(f"  Faltantes: {faltantes}")
        print(f"  Columnas encontradas: {list(df.columns)}")
        sys.exit(1)

    return df


def importar(df, conn):
    """
    Inserta las filas del DataFrame en movimientos_banco.
    Retorna (insertados, omitidos).
    """
    insertados = 0
    omitidos = 0
    errores = 0

    with conn:
        cur = conn.cursor()
        for _, row in df.iterrows():
            fecha = parsear_fecha(row.get('fecha'))
            rut = normalizar_rut(row.get('rut'))
            nombre = str(row.get('nombre', '')).strip() or None
            monto = parsear_monto(row.get('monto'))
            codigo = str(row.get('codigo_transferencia', '')).strip() or None

            if not fecha or not monto:
                errores += 1
                continue

            try:
                cur.execute("""
                    INSERT INTO movimientos_banco
                        (fecha, rut_emisor, nombre_emisor, monto_abono, conciliado, codigo_transferencia)
                    VALUES (%s, %s, %s, %s, FALSE, %s)
                    ON CONFLICT (codigo_transferencia)
                        WHERE codigo_transferencia IS NOT NULL
                    DO NOTHING
                """, (fecha, rut, nombre, monto, codigo))

                if cur.rowcount == 1:
                    insertados += 1
                else:
                    omitidos += 1

            except psycopg2.Error as e:
                print(f"  ⚠️  Error insertando fila {row.to_dict()}: {e}")
                errores += 1

    return insertados, omitidos, errores


def main():
    ruta_arg = sys.argv[1] if len(sys.argv) > 1 else None

    print("=" * 60)
    print("ZIGURAT ERP — Importar Transferencias Itaú")
    print("=" * 60)
    print()

    ruta = encontrar_excel(ruta_arg)
    print(f"  Archivo: {ruta}")
    print()

    df = leer_excel(ruta)
    df = mapear_columnas(df)
    print(f"  Filas en Excel: {len(df)}")

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    print(f"  ✓ Conectado a: {DB_CONFIG['dbname']}")
    print()

    insertados, omitidos, errores = importar(df, conn)
    conn.close()

    print("=" * 60)
    print("RESULTADO")
    print("=" * 60)
    print(f"  ✓ Transferencias importadas: {insertados}")
    if omitidos:
        print(f"  ℹ️  Ya existían (omitidas):   {omitidos}")
    if errores:
        print(f"  ⚠️  Filas con errores:         {errores}")
    print()

    if insertados > 0:
        print("✅ Importación completada")
    elif omitidos > 0 and insertados == 0:
        print("ℹ️  Todas las transferencias ya estaban en la base de datos")
    print("=" * 60)


if __name__ == "__main__":
    main()
```

**Step 2: Probar con el Excel real del Itaú**

Copiar `ConsultaTransferencia.xlsx` a la carpeta `transferencias/`, luego:

```bash
python scripts/import_transferencias.py
```

Salida esperada:
```
============================================================
ZIGURAT ERP — Importar Transferencias Itaú
============================================================

  Archivo: transferencias/ConsultaTransferencia.xlsx

  Filas en Excel: 25
  ✓ Conectado a: dte_facturas_chile

============================================================
RESULTADO
============================================================
  ✓ Transferencias importadas: 25
✅ Importación completada
```

**Step 3: Verificar en BD**

```bash
python -c "
import psycopg2, os
from pathlib import Path

env = Path('.env')
for line in env.read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k,v = line.split('=',1)
        os.environ.setdefault(k.strip(), v.strip())

conn = psycopg2.connect(host='localhost', dbname='dte_facturas_chile', user='postgres', password=os.environ['DB_PASSWORD'])
cur = conn.cursor()
cur.execute('SELECT COUNT(*), MAX(fecha) FROM movimientos_banco WHERE conciliado = FALSE')
print(cur.fetchone())
conn.close()
"
```

---

### Task 4: Script conciliar_banco.py

**Files:**
- Create: `scripts/conciliar_banco.py`

**Step 1: Crear el script**

```python
#!/usr/bin/env python3
"""
conciliar_banco.py - Zigurat ERP
Concilia movimientos bancarios (transferencias recibidas) con facturas emitidas.

Algoritmo:
  1. Match exacto:   rut_emisor == rut_cliente AND monto_abono == monto_total_pendiente
  2. Match múltiple: rut_emisor == rut_cliente AND monto_abono == SUMA de N facturas
  3. Sin match:      monto no coincide con nada del RUT → reportar para revisión manual

Modo interactivo: muestra reporte completo y pide confirmación antes de guardar.

Uso:
    python scripts/conciliar_banco.py
    python scripts/conciliar_banco.py --auto    (no pide confirmación, útil para tests)
"""

import os
import sys
from pathlib import Path
from datetime import date
from itertools import combinations

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: Falta psycopg2.")
    sys.exit(1)


def _load_env():
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

TOLERANCIA = 200  # pesos de diferencia permitida por redondeos


def normalizar_rut(rut):
    """Normaliza RUT para comparación: '77126823-4' → '77126823-4' (sin puntos, con guión)."""
    if not rut:
        return None
    return str(rut).strip().upper().replace(".", "")


def monto_pendiente(factura):
    """Retorna el monto pendiente real de cobro (usando ajuste si existe)."""
    ajustado = factura.get('monto_total_ajustado')
    if ajustado is not None:
        return float(ajustado)
    return float(factura['monto_total'])


def encontrar_match(monto_transferencia, facturas_cliente):
    """
    Intenta encontrar un subconjunto de facturas cuya suma == monto_transferencia.
    Tolera diferencia <= TOLERANCIA.
    Retorna lista de folios que hacen match, o None si no encuentra.
    Limita búsqueda a combinaciones de hasta 6 facturas para evitar lentitud.
    """
    MAX_COMBO = 6

    for n in range(1, min(len(facturas_cliente) + 1, MAX_COMBO + 1)):
        for combo in combinations(facturas_cliente, n):
            suma = sum(monto_pendiente(f) for f in combo)
            if abs(suma - monto_transferencia) <= TOLERANCIA:
                return list(combo)

    return None


def obtener_movimientos_pendientes(cur):
    """Retorna movimientos bancarios no conciliados (monto_abono > 0)."""
    cur.execute("""
        SELECT id, fecha, rut_emisor, nombre_emisor, monto_abono, codigo_transferencia
        FROM movimientos_banco
        WHERE conciliado = FALSE
          AND monto_abono > 0
        ORDER BY fecha, rut_emisor
    """)
    return cur.fetchall()


def obtener_facturas_pendientes(cur):
    """Retorna facturas emitidas sin fecha_pago, agrupadas por rut_cliente."""
    cur.execute("""
        SELECT
            folio,
            fecha,
            rut_cliente,
            razon_social_receptor,
            COALESCE(monto_total_ajustado, monto_total) AS monto_total_ajustado,
            monto_total
        FROM ventas
        WHERE fecha_pago IS NULL
          AND tipo_documento != '61'
        ORDER BY rut_cliente, fecha
    """)
    rows = cur.fetchall()

    # Agrupar por RUT
    por_rut = {}
    for row in rows:
        rut = normalizar_rut(row['rut_cliente'])
        if rut not in por_rut:
            por_rut[rut] = []
        por_rut[rut].append(dict(row))

    return por_rut


def analizar(movimientos, facturas_por_rut):
    """
    Clasifica cada movimiento en:
      - exactos: match encontrado (1 o N facturas)
      - sin_match: RUT existe pero monto no cuadra
      - sin_cliente: RUT no tiene facturas pendientes
    """
    exactos = []      # [(movimiento, [facturas])]
    sin_match = []    # [(movimiento, [facturas_del_rut])]
    sin_cliente = []  # [movimiento]

    for mov in movimientos:
        rut = normalizar_rut(mov['rut_emisor'])
        facturas = facturas_por_rut.get(rut, [])

        if not facturas:
            sin_cliente.append(mov)
            continue

        match = encontrar_match(float(mov['monto_abono']), facturas)
        if match:
            exactos.append((dict(mov), match))
        else:
            sin_match.append((dict(mov), facturas))

    return exactos, sin_match, sin_cliente


def mostrar_reporte(exactos, sin_match, sin_cliente):
    """Imprime el reporte de conciliación."""
    sep = "=" * 70

    print()
    print(sep)
    print("REPORTE DE CONCILIACIÓN BANCARIA")
    print(sep)
    print()

    # Sección 1: Matches encontrados
    print(f"✅  MATCHES ENCONTRADOS ({len(exactos)})")
    print("-" * 70)
    if exactos:
        for mov, facturas in exactos:
            folios = ", ".join(str(f['folio']) for f in facturas)
            cliente = facturas[0]['razon_social_receptor']
            suma = sum(monto_pendiente(f) for f in facturas)
            print(f"  Transferencia {mov['fecha']} | {mov['nombre_emisor']}")
            print(f"    Monto recibido: ${float(mov['monto_abono']):,.0f}")
            print(f"    Cubre {len(facturas)} factura(s): folio(s) {folios}")
            print(f"    Cliente: {cliente}")
            if len(facturas) > 1:
                print(f"    Suma facturas: ${suma:,.0f}  (diff: ${abs(suma - float(mov['monto_abono'])):,.0f})")
            print()
    else:
        print("  (ninguno)")
        print()

    # Sección 2: Sin match por monto
    print(f"⚠️   SIN MATCH — REVISIÓN MANUAL ({len(sin_match)})")
    print("-" * 70)
    if sin_match:
        for mov, facturas in sin_match:
            folios_pendientes = [f['folio'] for f in facturas]
            montos_pendientes = [monto_pendiente(f) for f in facturas]
            print(f"  Transferencia {mov['fecha']} | {mov['nombre_emisor']}")
            print(f"    Monto recibido: ${float(mov['monto_abono']):,.0f}")
            print(f"    Facturas pendientes del cliente: {folios_pendientes}")
            print(f"    Montos pendientes:               {[f'${m:,.0f}' for m in montos_pendientes]}")
            print(f"    → Este movimiento debe conciliarse manualmente en la BD")
            print()
    else:
        print("  (ninguno)")
        print()

    # Sección 3: RUT sin facturas pendientes
    print(f"ℹ️   TRANSFERENCIAS SIN FACTURAS PENDIENTES ({len(sin_cliente)})")
    print("-" * 70)
    if sin_cliente:
        for mov in sin_cliente:
            print(f"  {mov['fecha']} | {mov['nombre_emisor']} | ${float(mov['monto_abono']):,.0f}")
    else:
        print("  (ninguno)")
    print()


def confirmar():
    """Pide confirmación al usuario. Retorna True si confirma."""
    while True:
        resp = input("¿Confirmar conciliación de los matches encontrados? [s/N]: ").strip().lower()
        if resp in ('s', 'si', 'sí', 'y', 'yes'):
            return True
        if resp in ('n', 'no', ''):
            return False
        print("  Responde 's' para confirmar o 'n' para cancelar.")


def aplicar_conciliacion(cur, exactos):
    """
    Aplica la conciliación:
    - Inserta en tabla conciliaciones
    - Actualiza ventas.fecha_pago y ventas.dias_pago
    - Marca movimientos_banco.conciliado = TRUE
    """
    for mov, facturas in exactos:
        for factura in facturas:
            # INSERT en conciliaciones
            cur.execute("""
                INSERT INTO conciliaciones (folio_venta, movimiento_banco_id, monto_aplicado, fecha_conciliacion)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT DO NOTHING
            """, (factura['folio'], mov['id'], monto_pendiente(factura)))

            # UPDATE ventas: fecha_pago y dias_pago
            cur.execute("""
                UPDATE ventas
                SET fecha_pago = %s,
                    dias_pago  = %s - fecha
                WHERE folio = %s
                  AND tipo_documento != '61'
            """, (mov['fecha'], mov['fecha'], factura['folio']))

        # Marcar movimiento como conciliado
        cur.execute("""
            UPDATE movimientos_banco
            SET conciliado = TRUE
            WHERE id = %s
        """, (mov['id'],))


def main():
    auto = '--auto' in sys.argv

    print("=" * 70)
    print("ZIGURAT ERP — Conciliación Bancaria")
    print("=" * 70)
    print()

    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    print(f"✓ Conectado a: {DB_CONFIG['dbname']}")

    try:
        with conn.cursor() as cur:
            movimientos = obtener_movimientos_pendientes(cur)
            facturas_por_rut = obtener_facturas_pendientes(cur)

        print(f"  Movimientos sin conciliar: {len(movimientos)}")
        print(f"  Clientes con facturas pendientes: {len(facturas_por_rut)}")

        if not movimientos:
            print()
            print("ℹ️  No hay movimientos pendientes de conciliar.")
            print("   Usa /importar-transferencias para cargar el Excel del banco.")
            return

        exactos, sin_match, sin_cliente = analizar(movimientos, facturas_por_rut)
        mostrar_reporte(exactos, sin_match, sin_cliente)

        if not exactos:
            print("ℹ️  No hay matches para conciliar automáticamente.")
            return

        print(f"Se van a conciliar {len(exactos)} transferencias")
        print(f"(Facturas a marcar como pagadas: {sum(len(f) for _, f in exactos)})")
        print()

        if auto or confirmar():
            with conn:
                with conn.cursor() as cur:
                    aplicar_conciliacion(cur, exactos)

            print()
            print("✅ Conciliación guardada exitosamente")
            print(f"   Transferencias conciliadas: {len(exactos)}")
            print(f"   Facturas marcadas como pagadas: {sum(len(f) for _, f in exactos)}")
        else:
            print()
            print("ℹ️  Conciliación cancelada. No se guardó nada.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

**Step 2: Probar la conciliación**

```bash
python scripts/conciliar_banco.py
```

El script debe:
1. Mostrar el reporte con matches / sin match / sin cliente
2. Pedir confirmación
3. Si confirmas con 's', guardar en BD

**Step 3: Verificar que las facturas quedaron marcadas**

```bash
python -c "
import psycopg2, os
from pathlib import Path
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k,v=line.split('=',1); os.environ.setdefault(k.strip(),v.strip())
conn = psycopg2.connect(host='localhost',dbname='dte_facturas_chile',user='postgres',password=os.environ['DB_PASSWORD'])
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM ventas WHERE fecha_pago IS NOT NULL')
print('Facturas con fecha_pago:', cur.fetchone()[0])
conn.close()
"
```

---

### Task 5: Script flujo_caja.py

**Files:**
- Create: `scripts/flujo_caja.py`

**Step 1: Crear el script**

```python
#!/usr/bin/env python3
"""
flujo_caja.py - Zigurat ERP
Proyecta el flujo de caja de las próximas 4 semanas.

Ingresos proyectados: facturas emitidas sin fecha_pago, proyectadas según
  el promedio histórico de días de pago del cliente.
Egresos proyectados: cuentas_por_pagar pendientes en el horizonte de 4 semanas.

Uso:
    python scripts/flujo_caja.py
    python scripts/flujo_caja.py --saldo-inicial 5000000
"""

import os
import sys
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: Falta psycopg2.")
    sys.exit(1)


def _load_env():
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

SEMANAS = 4
AVG_DIAS_GLOBAL = 30     # fallback si cliente tiene <3 facturas pagadas
MIN_FACTURAS_PARA_AVG = 3  # mínimo de facturas pagadas para usar promedio del cliente


def parsear_saldo_arg():
    """Retorna saldo inicial desde --saldo-inicial XXXX si fue pasado."""
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == '--saldo-inicial' and i < len(sys.argv):
            try:
                return float(sys.argv[i + 1].replace('.', '').replace(',', '.'))
            except (ValueError, IndexError):
                pass
    return None


def obtener_saldo_banco(cur):
    """Retorna el último saldo_diario registrado en movimientos_banco."""
    cur.execute("""
        SELECT saldo_diario, fecha
        FROM movimientos_banco
        WHERE saldo_diario IS NOT NULL
        ORDER BY fecha DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    if row:
        return float(row['saldo_diario']), row['fecha']
    return None, None


def obtener_avg_dias_por_cliente(cur):
    """
    Retorna dict {rut_cliente: avg_dias_pago} basado en las últimas 10 facturas
    pagadas de cada cliente.
    """
    cur.execute("""
        SELECT
            rut_cliente,
            AVG(dias_pago) as avg_dias,
            COUNT(*) as n
        FROM (
            SELECT rut_cliente, dias_pago,
                   ROW_NUMBER() OVER (PARTITION BY rut_cliente ORDER BY fecha DESC) AS rn
            FROM ventas
            WHERE fecha_pago IS NOT NULL
              AND dias_pago IS NOT NULL
              AND dias_pago > 0
              AND tipo_documento != '61'
        ) t
        WHERE rn <= 10
        GROUP BY rut_cliente
        HAVING COUNT(*) >= %s
    """, (MIN_FACTURAS_PARA_AVG,))

    return {row['rut_cliente']: float(row['avg_dias']) for row in cur.fetchall()}


def obtener_facturas_pendientes(cur):
    """Retorna facturas sin fecha_pago."""
    cur.execute("""
        SELECT
            folio,
            fecha,
            rut_cliente,
            razon_social_receptor,
            COALESCE(monto_total_ajustado, monto_total) AS monto
        FROM ventas
        WHERE fecha_pago IS NULL
          AND tipo_documento != '61'
        ORDER BY fecha
    """)
    return cur.fetchall()


def obtener_gastos_pendientes(cur, hasta):
    """Retorna cuentas_por_pagar pendientes hasta la fecha indicada."""
    cur.execute("""
        SELECT id, descripcion, proveedor, monto, fecha_vencimiento, categoria
        FROM cuentas_por_pagar
        WHERE pagado = FALSE
          AND fecha_vencimiento <= %s
        ORDER BY fecha_vencimiento
    """, (hasta,))
    return cur.fetchall()


def semana_de(d, inicio_periodo):
    """Retorna el número de semana (0-based) para una fecha dada."""
    delta = (d - inicio_periodo).days
    return delta // 7


def formatear_pesos(n):
    """Formatea número como pesos chilenos: $1.234.567"""
    return f"${int(n):,}".replace(",", ".")


def main():
    saldo_arg = parsear_saldo_arg()
    hoy = date.today()
    horizonte = hoy + timedelta(weeks=SEMANAS)

    print("=" * 70)
    print("ZIGURAT ERP — Proyección de Flujo de Caja")
    print("=" * 70)
    print()

    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    with conn:
        with conn.cursor() as cur:
            # Saldo inicial
            if saldo_arg is not None:
                saldo_inicial = saldo_arg
                saldo_fecha = hoy
                print(f"  Saldo inicial (manual): {formatear_pesos(saldo_inicial)}")
            else:
                saldo_inicial, saldo_fecha = obtener_saldo_banco(cur)
                if saldo_inicial is not None:
                    dias_viejo = (hoy - saldo_fecha).days
                    if dias_viejo > 7:
                        print(f"  ⚠️  El último saldo en BD es de hace {dias_viejo} días ({saldo_fecha})")
                        print(f"     Para mayor precisión usa: python scripts/flujo_caja.py --saldo-inicial MONTO")
                    print(f"  Saldo inicial (BD {saldo_fecha}): {formatear_pesos(saldo_inicial)}")
                else:
                    print("  ⚠️  No hay saldo bancario en la BD.")
                    print("     Usa: python scripts/flujo_caja.py --saldo-inicial MONTO")
                    saldo_inicial = 0
                    print(f"  Asumiendo saldo inicial: {formatear_pesos(saldo_inicial)}")

            print()

            # Datos
            avg_dias = obtener_avg_dias_por_cliente(cur)
            facturas = obtener_facturas_pendientes(cur)
            gastos = obtener_gastos_pendientes(cur, horizonte)

    conn.close()

    # Clasificar ingresos por semana
    ingresos_semana = defaultdict(list)
    ingresos_fuera = []

    for f in facturas:
        rut = f['rut_cliente']
        avg = avg_dias.get(rut, AVG_DIAS_GLOBAL)
        fecha_proyectada = f['fecha'] + timedelta(days=int(avg))

        if fecha_proyectada < hoy:
            # Ya debería haber pagado — lo ponemos en semana 0 (esta semana)
            fecha_proyectada = hoy

        if fecha_proyectada <= horizonte:
            sem = semana_de(fecha_proyectada, hoy)
            sem = max(0, min(sem, SEMANAS - 1))
            ingresos_semana[sem].append({
                'folio': f['folio'],
                'cliente': f['razon_social_receptor'],
                'monto': float(f['monto']),
                'fecha_emision': f['fecha'],
                'fecha_proyectada': fecha_proyectada,
                'avg_dias': avg,
            })
        else:
            ingresos_fuera.append(f)

    # Clasificar gastos por semana
    gastos_semana = defaultdict(list)
    for g in gastos:
        sem = semana_de(g['fecha_vencimiento'], hoy)
        sem = max(0, min(sem, SEMANAS - 1))
        gastos_semana[sem].append(g)

    # Mostrar proyección
    print(f"  Horizonte: {hoy.strftime('%d/%m/%Y')} → {horizonte.strftime('%d/%m/%Y')}")
    print(f"  Facturas por cobrar en ventana: {sum(len(v) for v in ingresos_semana.values())}")
    print(f"  Facturas fuera de ventana:      {len(ingresos_fuera)}")
    print(f"  Gastos pendientes en ventana:   {len(gastos)}")
    print()

    sep = "=" * 70
    print(sep)
    print(f"{'SEMANA':<20} {'INGRESOS':>15} {'EGRESOS':>15} {'SALDO':>15}")
    print("-" * 70)

    saldo_acum = saldo_inicial
    total_ingresos = 0
    total_egresos = 0

    detalles = []

    for sem in range(SEMANAS):
        inicio_sem = hoy + timedelta(weeks=sem)
        fin_sem = inicio_sem + timedelta(days=6)
        label = f"{inicio_sem.strftime('%d/%m')}-{fin_sem.strftime('%d/%m')}"

        ingresos = sum(i['monto'] for i in ingresos_semana.get(sem, []))
        egresos  = sum(float(g['monto']) for g in gastos_semana.get(sem, []))

        saldo_acum += ingresos - egresos
        total_ingresos += ingresos
        total_egresos  += egresos

        alerta = " ⚠️" if saldo_acum < 0 else ""
        print(f"  {label:<18} {formatear_pesos(ingresos):>15} {formatear_pesos(egresos):>15} {formatear_pesos(saldo_acum):>15}{alerta}")
        detalles.append((sem, label, ingresos_semana.get(sem, []), gastos_semana.get(sem, [])))

    print("-" * 70)
    print(f"  {'TOTAL':<18} {formatear_pesos(total_ingresos):>15} {formatear_pesos(total_egresos):>15}")
    print(sep)
    print()

    # Detalle de ingresos
    print("DETALLE INGRESOS PROYECTADOS")
    print("-" * 70)
    for sem, label, ingresos_list, _ in detalles:
        if ingresos_list:
            print(f"  Semana {sem+1} ({label}):")
            for i in ingresos_list:
                print(f"    Folio {i['folio']} | {i['cliente'][:35]:<35} | "
                      f"{formatear_pesos(i['monto']):>12} | "
                      f"pago ~{i['fecha_proyectada'].strftime('%d/%m')} "
                      f"(avg {int(i['avg_dias'])}d)")

    if ingresos_fuera:
        print()
        print(f"  Fuera de las 4 semanas ({len(ingresos_fuera)} facturas):")
        for f in ingresos_fuera[:5]:
            avg = avg_dias.get(f['rut_cliente'], AVG_DIAS_GLOBAL)
            proyectada = f['fecha'] + timedelta(days=int(avg))
            print(f"    Folio {f['folio']} | {str(f['razon_social_receptor'])[:35]:<35} | "
                  f"{formatear_pesos(float(f['monto'])):>12} | ~{proyectada.strftime('%d/%m/%Y')}")
        if len(ingresos_fuera) > 5:
            print(f"    ... y {len(ingresos_fuera)-5} más")

    print()

    # Detalle de egresos
    if any(gastos_semana.values()):
        print("DETALLE EGRESOS PROYECTADOS")
        print("-" * 70)
        for sem, label, _, gastos_list in detalles:
            if gastos_list:
                print(f"  Semana {sem+1} ({label}):")
                for g in gastos_list:
                    cat = f"[{g['categoria']}]" if g['categoria'] else ""
                    prov = g['proveedor'] or ""
                    print(f"    {g['descripcion'][:35]:<35} {prov[:20]:<20} "
                          f"{formatear_pesos(float(g['monto'])):>12} "
                          f"vence {g['fecha_vencimiento'].strftime('%d/%m')} {cat}")
        print()
    else:
        print("ℹ️  Sin gastos registrados en la ventana de 4 semanas.")
        print("   Usa /agregar-gasto para registrar cuentas por pagar.")
        print()

    print(sep)


if __name__ == "__main__":
    main()
```

**Step 2: Probar el script**

```bash
python scripts/flujo_caja.py
```

Si no hay saldo bancario en BD, agregar `--saldo-inicial`:

```bash
python scripts/flujo_caja.py --saldo-inicial 3000000
```

Verificar que:
- La tabla semanal se muestra con ingresos/egresos/saldo acumulado
- El detalle de ingresos lista las facturas con fecha proyectada
- Si el saldo proyectado cae a negativo, aparece el aviso ⚠️

---

### Task 6: Script agregar_gasto.py (para el skill)

**Files:**
- Create: `.claude/skills/agregar-gasto/scripts/agregar_gasto.py`

**Step 1: Crear carpeta del skill y su script**

```bash
mkdir -p .claude/skills/agregar-gasto/scripts
```

```python
#!/usr/bin/env python3
"""
agregar_gasto.py - Zigurat ERP
Agrega una cuenta por pagar a la base de datos.

Uso:
    python agregar_gasto.py "descripcion" monto YYYY-MM-DD [proveedor] [categoria]

Ejemplo:
    python agregar_gasto.py "Arriendo bodega marzo" 850000 2026-03-05 "Propietario SA" arriendo
"""

import os
import sys
from pathlib import Path
from datetime import datetime

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2.")
    sys.exit(1)


def _load_env():
    # Buscar .env subiendo desde la ubicación del script hasta encontrarlo
    p = Path(__file__).resolve()
    for _ in range(6):
        candidate = p.parent / ".env"
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return
        p = p.parent

_load_env()

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}


def main():
    if len(sys.argv) < 4:
        print("Uso: python agregar_gasto.py \"descripcion\" monto YYYY-MM-DD [proveedor] [categoria]")
        print("Ejemplo: python agregar_gasto.py \"Arriendo bodega\" 850000 2026-03-05 \"Prop SA\" arriendo")
        sys.exit(1)

    descripcion  = sys.argv[1]
    monto_raw    = sys.argv[2].replace('.', '').replace(',', '.')
    fecha_raw    = sys.argv[3]
    proveedor    = sys.argv[4] if len(sys.argv) > 4 else None
    categoria    = sys.argv[5] if len(sys.argv) > 5 else None

    try:
        monto = float(monto_raw)
    except ValueError:
        print(f"ERROR: Monto inválido: {sys.argv[2]}")
        sys.exit(1)

    try:
        fecha = datetime.strptime(fecha_raw, '%Y-%m-%d').date()
    except ValueError:
        print(f"ERROR: Fecha inválida: {fecha_raw}. Formato esperado: YYYY-MM-DD")
        sys.exit(1)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    with conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO cuentas_por_pagar
                (descripcion, proveedor, monto, fecha_vencimiento, categoria)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (descripcion, proveedor, monto, fecha, categoria))
        new_id = cur.fetchone()[0]

    conn.close()

    print(f"✅ Gasto registrado (id={new_id})")
    print(f"   Descripción:  {descripcion}")
    print(f"   Monto:        ${monto:,.0f}".replace(",", "."))
    print(f"   Vencimiento:  {fecha.strftime('%d/%m/%Y')}")
    if proveedor:
        print(f"   Proveedor:    {proveedor}")
    if categoria:
        print(f"   Categoría:    {categoria}")


if __name__ == "__main__":
    main()
```

**Step 2: Probar el script directamente**

```bash
python .claude/skills/agregar-gasto/scripts/agregar_gasto.py "Arriendo bodega marzo" 850000 2026-03-15 "Propietario SA" arriendo
```

Salida esperada:
```
✅ Gasto registrado (id=1)
   Descripción:  Arriendo bodega marzo
   Monto:        $850.000
   Vencimiento:  15/03/2026
   Proveedor:    Propietario SA
   Categoría:    arriendo
```

**Step 3: Verificar en BD**

```sql
SELECT * FROM cuentas_por_pagar;
```

---

### Task 7: Skill importar-transferencias

**Files:**
- Create: `.claude/skills/importar-transferencias/SKILL.md`

**Step 1: Crear el directorio y el SKILL.md**

```bash
mkdir -p .claude/skills/importar-transferencias
```

Contenido del archivo `.claude/skills/importar-transferencias/SKILL.md`:

```markdown
---
name: importar-transferencias
description: >
  Importa el archivo Excel de transferencias recibidas del Itaú Empresas a PostgreSQL.
  Usar cuando el usuario quiera cargar el Excel del banco, sincronizar transferencias,
  importar pagos recibidos, o actualizar los movimientos bancarios.
  Ejemplos: "importa el Excel del banco", "carga las transferencias", "hay pagos nuevos en el banco".
context: fork
disable-model-invocation: true
allowed-tools: Bash(python *)
---

# Importar Transferencias — Zigurat ERP

Importa el Excel de transferencias del Itaú desde la carpeta `transferencias\` a PostgreSQL.

## Reglas

- NUNCA pedir confirmación antes de ejecutar
- NUNCA continuar si el script falla
- El Excel debe estar en la carpeta `transferencias\` antes de ejecutar

## Paso 1 — Verificar que existe el Excel

```bash
python -c "
import glob, sys
archivos = glob.glob('transferencias/*.xlsx')
if not archivos:
    print('ERROR: No hay archivos .xlsx en transferencias/')
    print('Descarga el Excel del Itaú y déjalo en la carpeta transferencias/')
    sys.exit(1)
else:
    print(f'Archivo encontrado: {archivos[0]}')
"
```

Si falla: reportar error y detener.

## Paso 2 — Importar

```bash
python scripts/import_transferencias.py
```

Si falla: reportar error y detener.

## Paso 3 — Resumen

Mostrar al usuario el resultado del paso 2:
- Transferencias importadas
- Transferencias ya existían (omitidas)
- Sugerir ejecutar `/conciliar-banco` como siguiente paso
```

---

### Task 8: Skill conciliar-banco

**Files:**
- Create: `.claude/skills/conciliar-banco/SKILL.md`

**Step 1: Crear directorio y SKILL.md**

```bash
mkdir -p .claude/skills/conciliar-banco
```

Contenido de `.claude/skills/conciliar-banco/SKILL.md`:

```markdown
---
name: conciliar-banco
description: >
  Concilia las transferencias bancarias importadas con las facturas pendientes de cobro.
  Usar cuando el usuario quiera marcar facturas como pagadas, cruzar transferencias con facturas,
  actualizar fechas de pago, o saber qué facturas ya fueron cobradas.
  Ejemplos: "concilia el banco", "marca las facturas pagadas", "cruza las transferencias con facturas",
  "actualiza los pagos recibidos".
context: fork
disable-model-invocation: true
allowed-tools: Bash(python *)
---

# Conciliar Banco — Zigurat ERP

Cruza los movimientos bancarios sin conciliar con las facturas pendientes de cobro.
Muestra un reporte completo y pide confirmación antes de guardar.

## Reglas

- NUNCA saltarse la confirmación del usuario
- NUNCA continuar si el script falla con error de conexión
- Ejecutar DESPUÉS de `/importar-transferencias`

## Paso 1 — Ejecutar análisis

```bash
python scripts/conciliar_banco.py
```

El script se encarga de:
1. Analizar los movimientos sin conciliar
2. Mostrar el reporte completo (matches, sin match, sin cliente)
3. Pedir confirmación interactiva al usuario
4. Si confirma: guardar en BD

## Paso 2 — Siguiente paso sugerido

Después de completar, sugerir al usuario:
- `/flujo-caja` para ver la proyección actualizada
```

---

### Task 9: Skill flujo-caja

**Files:**
- Create: `.claude/skills/flujo-caja/SKILL.md`

**Step 1: Crear directorio y SKILL.md**

```bash
mkdir -p .claude/skills/flujo-caja
```

Contenido de `.claude/skills/flujo-caja/SKILL.md`:

```markdown
---
name: flujo-caja
description: >
  Proyecta el flujo de caja de las próximas 4 semanas basándose en facturas pendientes
  de cobro y gastos programados. Usar cuando el usuario quiera saber cuándo va a cobrar,
  proyectar ingresos, ver el flujo de caja, o saber si habrá problemas de liquidez.
  Ejemplos: "proyecta el flujo de caja", "cuánto voy a cobrar esta semana",
  "¿habrá plata para pagar el arriendo?", "muestra el flujo de caja", "proyección de pagos".
disable-model-invocation: false
allowed-tools: Bash(python *)
---

# Flujo de Caja — Zigurat ERP

Genera la proyección de flujo de caja de las próximas 4 semanas.

## Reglas

- NUNCA pedir confirmación antes de ejecutar
- Si el usuario menciona un saldo específico, pasar `--saldo-inicial MONTO`
- Presentar el output con análisis breve al final

## Paso 1 — Ejecutar proyección

Sin saldo manual:
```bash
python scripts/flujo_caja.py
```

Con saldo manual (si el usuario lo indica):
```bash
python scripts/flujo_caja.py --saldo-inicial MONTO
```

Si falla: reportar error y detener.

## Paso 2 — Presentar análisis

Después de mostrar el output del script, agregar un breve análisis:
- Semana con mayor ingreso proyectado
- Si hay alguna semana con saldo negativo proyectado (riesgo de liquidez)
- Clientes con facturas más atrasadas (emitidas hace >30 días sin pago)
- Recordar que `/agregar-gasto` permite registrar gastos para mejorar la precisión
```

---

### Task 10: Skill agregar-gasto

**Files:**
- Create: `.claude/skills/agregar-gasto/SKILL.md`

**Step 1: Crear directorio y SKILL.md**

```bash
mkdir -p .claude/skills/agregar-gasto
```

Contenido de `.claude/skills/agregar-gasto/SKILL.md`:

```markdown
---
name: agregar-gasto
description: >
  Registra una cuenta por pagar (gasto programado) en la base de datos.
  Usar cuando el usuario quiera agregar un gasto, registrar una cuenta por pagar,
  ingresar un pago futuro o una obligación de pago.
  Ejemplos: "agrega el arriendo de marzo", "registra el pago al proveedor",
  "anota el gasto de insumos", "tengo que pagar X el DD/MM".
argument-hint: '"descripcion" monto YYYY-MM-DD [proveedor] [categoria]'
disable-model-invocation: false
allowed-tools: Bash(python *)
---

# Agregar Gasto — Zigurat ERP

Registra una nueva cuenta por pagar en la tabla `cuentas_por_pagar`.

## Reglas

- NUNCA pedir confirmación antes de ejecutar
- Inferir los parámetros del mensaje del usuario
- Si faltan datos críticos (descripción, monto, fecha), preguntar

## Paso 1 — Interpretar y extraer parámetros

Del mensaje del usuario extraer:
- `descripcion`: qué es el gasto (requerido)
- `monto`: cuánto (requerido, en pesos chilenos, sin puntos)
- `fecha_vencimiento`: cuándo vence en formato YYYY-MM-DD (requerido)
- `proveedor`: a quién se paga (opcional)
- `categoria`: tipo — 'insumos', 'arriendo', 'servicios', 'remuneraciones', 'impuestos', 'otros' (opcional)

## Paso 2 — Ejecutar

```bash
python .claude/skills/agregar-gasto/scripts/agregar_gasto.py "DESCRIPCION" MONTO YYYY-MM-DD "PROVEEDOR" CATEGORIA
```

Omitir proveedor y/o categoria si no fueron dados.

## Paso 3 — Confirmar al usuario

Mostrar el resultado del script confirmando que el gasto fue registrado.
Sugerir `/flujo-caja` para ver el impacto en la proyección.
```

---

### Task 11: Actualizar CLAUDE.md

**Files:**
- Modify: `.claude/CLAUDE.md`

**Step 1: Agregar sección de nuevos skills al CLAUDE.md**

En la sección "Skills Disponibles (activas)" agregar los 4 nuevos skills:

```markdown
```
/importar-transferencias
```
Importa el Excel de transferencias del Itaú desde `transferencias\` a `movimientos_banco`.
Ejecutar antes de `/conciliar-banco`.

```
/conciliar-banco
```
Cruza los movimientos bancarios sin conciliar con facturas pendientes.
Auto-concilia matches exactos, reporta excepciones, pide confirmación final antes de guardar.

```
/flujo-caja
```
Proyección de flujo de caja de las próximas 4 semanas.
Combina facturas por cobrar (usando avg días de pago por cliente) con `cuentas_por_pagar`.

```
/agregar-gasto "descripcion" monto YYYY-MM-DD [proveedor] [categoria]
```
Registra una cuenta por pagar en la BD. Usar antes de `/flujo-caja` para mayor precisión.
Ejemplo: `/agregar-gasto "Arriendo bodega" 850000 2026-03-05`
```

También agregar al final una sección de **Workflow de conciliación**:

```markdown
## Workflow de Conciliación Bancaria y Flujo de Caja

```
1. Descargar ConsultaTransferencia.xlsx del Itaú → dejar en transferencias\
2. /importar-transferencias  →  importa nuevos movimientos a movimientos_banco
3. /conciliar-banco           →  cruza transferencias con facturas, confirmar → actualiza fecha_pago
4. /flujo-caja                →  proyección de las próximas 4 semanas
5. /agregar-gasto             →  opcional: registrar gastos futuros para mejorar proyección
```

## Tabla movimientos_banco — RUTs

Los RUTs en `movimientos_banco` (importados del banco) se almacenan con guión y sin puntos.
Ejemplo: `77126823-4`. Comparar siempre con los RUTs en `ventas` (mismo formato).
```

**Step 2: Verificar que el archivo quedó correcto**

Leer el CLAUDE.md actualizado para confirmar que los nuevos skills están listados correctamente.

---

### Task 12: Smoke test del flujo completo

**Step 1: Verificar que todos los scripts corren sin error de sintaxis**

```bash
python -m py_compile scripts/migrate_flujo_caja.py && echo "OK migrate"
python -m py_compile scripts/import_transferencias.py && echo "OK import"
python -m py_compile scripts/conciliar_banco.py && echo "OK conciliar"
python -m py_compile scripts/flujo_caja.py && echo "OK flujo_caja"
python -m py_compile .claude/skills/agregar-gasto/scripts/agregar_gasto.py && echo "OK agregar_gasto"
```

Todos deben mostrar "OK".

**Step 2: Verificar flujo completo**

```bash
# 1. Migración ya aplicada (Task 1)
# 2. Flujo de caja con datos actuales
python scripts/flujo_caja.py

# 3. Agregar un gasto de prueba
python .claude/skills/agregar-gasto/scripts/agregar_gasto.py "Prueba de gasto" 100000 2026-03-10 "Test" servicios

# 4. Verificar que aparece en flujo de caja
python scripts/flujo_caja.py
```

El gasto de prueba debe aparecer en la proyección.

**Step 3: Limpiar gasto de prueba**

```bash
python -c "
import psycopg2, os
from pathlib import Path
for line in Path('.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k,v=line.split('=',1); os.environ.setdefault(k.strip(),v.strip())
conn = psycopg2.connect(host='localhost',dbname='dte_facturas_chile',user='postgres',password=os.environ['DB_PASSWORD'])
with conn:
    conn.cursor().execute(\"DELETE FROM cuentas_por_pagar WHERE descripcion = 'Prueba de gasto'\")
conn.close()
print('Gasto de prueba eliminado')
"
```

---

## Resumen de archivos creados

```
scripts/
  migrate_flujo_caja.py       ← migración DB (idempotente)
  import_transferencias.py    ← importar Excel Itaú
  conciliar_banco.py          ← conciliación bancaria
  flujo_caja.py               ← proyección 4 semanas

transferencias/               ← carpeta nueva para Excels del banco

.claude/skills/
  importar-transferencias/
    SKILL.md
  conciliar-banco/
    SKILL.md
  flujo-caja/
    SKILL.md
  agregar-gasto/
    SKILL.md
    scripts/
      agregar_gasto.py

.claude/CLAUDE.md             ← actualizado con nuevos skills y workflow
docs/plans/
  2026-02-28-flujo-caja-design.md  (diseño — ya existe)
  2026-02-28-flujo-caja-impl.md    (este archivo)
```
