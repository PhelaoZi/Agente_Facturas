#!/usr/bin/env python3
"""
importar_pagos_excel.py - Zigurat ERP
Importa fechas de pago históricas desde el Excel de seguimiento de clientes
y actualiza ventas.fecha_pago + ventas.dias_pago en PostgreSQL.

Formato Excel esperado (una pestaña por cliente):
  - Fila 1: headers (RUT, RAZON SOCIAL, DOCUMENTO, ID, FECHA EMISION, MONTO, PAGO, FECHA PAGO, DIAS CREDITO)
  - Fila 2+: datos

Uso:
    # Cargar una pestaña específica
    python scripts/importar_pagos_excel.py "C:/ruta/al/Excel.xlsx" "Santa Cebada (2)"

    # Cargar todas las pestañas
    python scripts/importar_pagos_excel.py "C:/ruta/al/Excel.xlsx" --all
"""

import os
import sys
from pathlib import Path
from datetime import datetime, date

try:
    import pandas as pd
except ImportError:
    print("ERROR: Falta la librería pandas.")
    print("Instala con: pip install pandas openpyxl")
    sys.exit(1)

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: Falta la librería psycopg2.")
    sys.exit(1)


# ─── Carga de variables de entorno desde .env ─────────────────────────────────
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

# Nombres de columnas esperados en el Excel (toleramos variaciones de mayúsculas)
COL_FOLIO       = "id"
COL_FECHA_EMIS  = "fecha emision"
COL_FECHA_PAGO  = "fecha pago"


def parsear_fecha(valor):
    """Parsea fecha desde datetime, date o string en múltiples formatos."""
    if pd.isna(valor) if hasattr(pd, 'isna') else valor is None:
        return None
    if isinstance(valor, (datetime, date)):
        return valor.date() if isinstance(valor, datetime) else valor
    if hasattr(valor, 'date'):
        return valor.date()
    s = str(valor).strip().split(' ')[0]
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def normalizar_columnas(df):
    """Normaliza nombres de columnas a minúsculas sin espacios extra."""
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def validar_columnas(df, nombre_hoja):
    """Verifica que el DataFrame tenga las columnas mínimas necesarias."""
    requeridas = [COL_FOLIO, COL_FECHA_EMIS, COL_FECHA_PAGO]
    faltantes = [c for c in requeridas if c not in df.columns]
    if faltantes:
        print(f"  [!] Pestaña '{nombre_hoja}' omitida: faltan columnas {faltantes}")
        print(f"      Columnas encontradas: {list(df.columns)}")
        return False
    return True


def procesar_hoja(df, nombre_hoja, cur):
    """
    Procesa una pestaña del Excel y actualiza ventas en PostgreSQL.
    Retorna dict con contadores: actualizadas, pendientes, no_encontradas, errores.
    """
    df = normalizar_columnas(df)
    df = df.dropna(how='all').reset_index(drop=True)

    if not validar_columnas(df, nombre_hoja):
        return None

    actualizadas   = 0
    pendientes     = 0    # filas sin fecha_pago (factura aún sin cobrar)
    no_encontradas = 0    # folio no existe en ventas
    errores        = 0
    dias_pagados   = []   # para calcular promedio al final

    for _, row in df.iterrows():
        # Parsear folio
        folio_raw = row.get(COL_FOLIO)
        if pd.isna(folio_raw) if hasattr(pd, 'isna') else folio_raw is None:
            continue
        try:
            folio = int(folio_raw)
        except (ValueError, TypeError):
            errores += 1
            continue

        # Parsear fechas
        fecha_emision = parsear_fecha(row.get(COL_FECHA_EMIS))
        fecha_pago    = parsear_fecha(row.get(COL_FECHA_PAGO))

        # Sin fecha_pago = factura pendiente, se omite (el flujo de caja la usará después)
        if fecha_pago is None:
            pendientes += 1
            continue

        # Calcular días que tardó en pagar (desde emisión hasta pago)
        if fecha_emision:
            dias = (fecha_pago - fecha_emision).days
        else:
            dias = None

        # Verificar que el folio existe en ventas antes de actualizar
        cur.execute(
            "SELECT folio FROM ventas WHERE folio = %s AND tipo_documento != '61'",
            (folio,)
        )
        if cur.fetchone() is None:
            no_encontradas += 1
            continue

        # Actualizar fecha_pago y dias_pago
        try:
            cur.execute("""
                UPDATE ventas
                SET fecha_pago = %s,
                    dias_pago  = %s
                WHERE folio = %s
                  AND tipo_documento != '61'
            """, (fecha_pago, dias, folio))
            actualizadas += 1
            if dias is not None:
                dias_pagados.append(dias)
        except psycopg2.Error as e:
            print(f"  Error actualizando folio {folio}: {e}")
            errores += 1

    # Calcular promedio de días de pago del cliente
    promedio_dias = round(sum(dias_pagados) / len(dias_pagados)) if dias_pagados else None

    return {
        "hoja":          nombre_hoja,
        "actualizadas":  actualizadas,
        "pendientes":    pendientes,
        "no_encontradas": no_encontradas,
        "errores":       errores,
        "promedio_dias": promedio_dias,
    }


def mostrar_resumen(resultados):
    """Imprime tabla resumen de todas las hojas procesadas."""
    sep = "=" * 65

    print()
    print(sep)
    print("RESUMEN DE IMPORTACIÓN")
    print(sep)
    print(f"  {'Cliente':<30} {'Pagos':>6} {'Pend.':>6} {'Prom. días':>10}")
    print(f"  {'-'*30} {'-'*6} {'-'*6} {'-'*10}")

    total_act = total_pend = total_nf = total_err = 0

    for r in resultados:
        if r is None:
            continue
        prom = f"{r['promedio_dias']} días" if r['promedio_dias'] is not None else "—"
        print(f"  {r['hoja']:<30} {r['actualizadas']:>6} {r['pendientes']:>6} {prom:>10}")
        total_act  += r['actualizadas']
        total_pend += r['pendientes']
        total_nf   += r['no_encontradas']
        total_err  += r['errores']

    print(f"  {'-'*30} {'-'*6} {'-'*6} {'-'*10}")
    print(f"  {'TOTAL':<30} {total_act:>6} {total_pend:>6}")
    print()

    if total_nf > 0:
        print(f"  [!] {total_nf} folio(s) no encontrados en ventas (puede ser historial pre-sistema)")
    if total_err > 0:
        print(f"  [!] {total_err} fila(s) con errores de parseo")
    print()
    print(f"  Facturas marcadas como pagadas: {total_act}")
    print(f"  Facturas pendientes (sin fecha_pago, para flujo de caja): {total_pend}")
    print(sep)


def main():
    args = sys.argv[1:]

    if len(args) < 2:
        print("Uso:")
        print('  python scripts/importar_pagos_excel.py "ruta/Excel.xlsx" "Nombre Pestaña"')
        print('  python scripts/importar_pagos_excel.py "ruta/Excel.xlsx" --all')
        sys.exit(1)

    ruta_excel = Path(args[0])
    modo       = args[1]

    if not ruta_excel.exists():
        print(f"ERROR: No se encontró el archivo: {ruta_excel}")
        sys.exit(1)

    print("=" * 65)
    print("ZIGURAT ERP - Importar Pagos desde Excel")
    print("=" * 65)
    print(f"  Archivo: {ruta_excel.name}")

    # Leer pestañas disponibles
    try:
        xl = pd.ExcelFile(ruta_excel, engine='openpyxl')
    except Exception as e:
        print(f"ERROR leyendo Excel: {e}")
        sys.exit(1)

    if modo == "--all":
        hojas = xl.sheet_names
    else:
        if modo not in xl.sheet_names:
            print(f"ERROR: Pestaña '{modo}' no existe en el Excel.")
            print(f"  Pestañas disponibles: {xl.sheet_names}")
            sys.exit(1)
        hojas = [modo]

    print(f"  Pestañas a procesar: {hojas}")
    print()

    # Conectar a PostgreSQL
    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    print(f"  Conectado a: {DB_CONFIG['dbname']}")

    resultados = []

    try:
        with conn:
            with conn.cursor() as cur:
                for nombre_hoja in hojas:
                    try:
                        df = xl.parse(nombre_hoja)
                    except Exception as e:
                        print(f"  ERROR leyendo pestaña '{nombre_hoja}': {e}")
                        continue

                    resultado = procesar_hoja(df, nombre_hoja, cur)
                    resultados.append(resultado)

    finally:
        conn.close()

    mostrar_resumen(resultados)


if __name__ == "__main__":
    main()
