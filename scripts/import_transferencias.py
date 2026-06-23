#!/usr/bin/env python3
"""
import_transferencias.py - Zigurat ERP
Importa el archivo Excel de transferencias recibidas del Itau Empresas
a la tabla movimientos_banco en PostgreSQL.

Formato Excel esperado (ConsultaTransferencia.xlsx):
  - Filas 1-9: cabecera del reporte Itau (se ignoran)
  - Fila 10:   headers de columnas
  - Fila 11+:  datos de transferencias

Uso:
    python scripts/import_transferencias.py [ruta_al_excel]

    Si no se pasa ruta, busca el .xlsx mas reciente en transferencias/
"""

import os
import re
import sys
from pathlib import Path
from datetime import datetime

try:
    import pandas as pd
except ImportError:
    print("ERROR: Falta la libreria pandas.")
    print("Instala con: pip install pandas openpyxl")
    sys.exit(1)

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta la libreria psycopg2.")
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
    # Si ya tiene guion, retornar tal cual (sin puntos)
    if "-" in s:
        return s
    # Sin guion: ultimo char es digito verificador
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
    # Remover simbolo de moneda y espacios
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
    Parsea fechas del Excel del Itau.
    Formatos posibles: datetime object, '26/02/2026', '26/02/2026 - 21:44:28'
    """
    if isinstance(valor, datetime):
        return valor.date()
    if hasattr(valor, 'date'):
        return valor.date()
    s = str(valor).strip()
    # Remover parte de hora si existe (acepta '... - HH:MM' y '... HH:MM')
    s = s.split(' - ')[0].split(' ')[0]
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parsear_codigo(valor):
    """
    Limpia el codigo de transferencia. Los .xls leidos con xlrd traen el codigo
    como float ('435992178.0'); hay que dejarlo como entero limpio para que el
    dedup por codigo_transferencia calce con lo ya cargado.
    """
    if valor is None:
        return None
    s = str(valor).strip()
    if s in ('', 'nan', 'None'):
        return None
    if s.endswith('.0'):
        s = s[:-2]
    elif '.' in s:
        try:
            s = str(int(float(s)))
        except ValueError:
            pass
    return s or None


def encontrar_excels(ruta_arg=None):
    """
    Retorna la LISTA de archivos a importar.
    Si se pasa argumento, usa ese unico archivo. Sino, procesa TODOS los
    .xls/.xlsx de transferencias/ (los meses exportados del Itau), ordenados
    por fecha. El dedup por codigo hace seguro reprocesar meses solapados.
    """
    if ruta_arg:
        p = Path(ruta_arg)
        if not p.exists():
            print(f"ERROR: No se encontro el archivo: {ruta_arg}")
            sys.exit(1)
        return [p]

    carpeta = Path("transferencias")
    if not carpeta.exists():
        print("ERROR: No existe la carpeta transferencias/")
        print("Crea la carpeta y deposita los Excel del Itau ahi.")
        sys.exit(1)

    archivos = sorted([*carpeta.glob("*.xlsx"), *carpeta.glob("*.xls")],
                      key=lambda p: p.stat().st_mtime)
    if not archivos:
        print("ERROR: No hay archivos .xls/.xlsx en la carpeta transferencias/")
        sys.exit(1)

    return archivos


def leer_excel(ruta):
    """
    Lee el Excel del Itau. Los headers estan en la fila 10 (indice 9 en 0-based).
    Elige el motor segun la extension: xlrd para .xls (formato viejo del portal),
    openpyxl para .xlsx. Retorna DataFrame con columnas normalizadas.
    """
    engine = 'xlrd' if Path(ruta).suffix.lower() == '.xls' else 'openpyxl'
    try:
        df = pd.read_excel(ruta, header=9, engine=engine)
    except Exception as e:
        print(f"ERROR leyendo Excel: {e}")
        sys.exit(1)

    # Normalizar nombres de columnas (quitar espacios, minusculas)
    df.columns = [str(c).strip().lower().replace(' ', '_') for c in df.columns]

    # Eliminar filas completamente vacias
    df = df.dropna(how='all').reset_index(drop=True)

    return df


def mapear_columnas(df):
    """
    Mapea los nombres de columnas del Excel a los campos internos.
    El Itau puede variar ligeramente los nombres entre exportaciones.
    """
    mapa = {
        'fecha':                 'fecha',
        'rut':                   'rut',
        'nombre':                'nombre',
        'banco_origen':          'banco_origen',
        'cuenta_destino':        'cuenta_destino',
        'monto':                 'monto',
        'estado':                'estado',
        'codigo_transferencia':  'codigo_transferencia',
        'cod._transferencia':    'codigo_transferencia',
        'codigo':                'codigo_transferencia',
    }

    # Handle accent variations
    def norm(s):
        return (s.lower().strip()
                .replace('\u00f3', 'o')
                .replace('\u00e9', 'e')
                .replace('\u00ed', 'i')
                .replace('\u00e1', 'a')
                .replace('\u00fa', 'u'))

    renombrar = {}
    for col in df.columns:
        col_norm = norm(col)
        # Try exact match first
        if col_norm in mapa:
            renombrar[col] = mapa[col_norm]
        # Try substring match for codigo_transferencia
        elif 'transferencia' in col_norm or 'codigo' in col_norm or 'cod' in col_norm:
            renombrar[col] = 'codigo_transferencia'

    df = df.rename(columns=renombrar)

    # Verificar columnas minimas requeridas
    requeridas = ['fecha', 'rut', 'nombre', 'monto']
    faltantes = [c for c in requeridas if c not in df.columns]
    if faltantes:
        print(f"ERROR: El Excel no tiene las columnas esperadas.")
        print(f"  Faltantes: {faltantes}")
        print(f"  Columnas encontradas: {list(df.columns)}")
        sys.exit(1)

    return df


def _rut_digitos(rut):
    """Deja solo digitos y K (mayuscula) para comparar RUTs sin importar formato."""
    return re.sub(r'[^0-9K]', '', (rut or '').upper())


def importar(df, conn):
    """
    Carga las filas del DataFrame en movimientos_banco evitando duplicados.

    Para cada transferencia:
      1. Si ya existe una fila con ese codigo -> se omite (ya cargada).
      2. Si existe una fila SIN codigo que calza (fecha + monto + RUT) -> se le
         rellena el codigo (enriquece las 730 filas viejas que se cargaron sin el).
      3. Si no calza con nada -> se inserta como nueva.

    Esto es necesario porque la carga historica guardo el RUT sin normalizar y
    sin codigo, por lo que el dedup por codigo solo no detecta los solapamientos.

    Retorna (insertados, enriquecidos, omitidos, errores).
    """
    insertados = enriquecidos = omitidos = errores = 0

    with conn:
        cur = conn.cursor()
        for _, row in df.iterrows():
            fecha = parsear_fecha(row.get('fecha'))
            rut = normalizar_rut(row.get('rut'))
            nombre = str(row.get('nombre', '')).strip() or None
            monto = parsear_monto(row.get('monto'))
            codigo = parsear_codigo(row.get('codigo_transferencia')) if 'codigo_transferencia' in row.index else None

            if not fecha or not monto:
                errores += 1
                continue

            try:
                # 1. ¿Ya existe exactamente este codigo?
                if codigo:
                    cur.execute("SELECT 1 FROM movimientos_banco WHERE codigo_transferencia = %s LIMIT 1", (codigo,))
                    if cur.fetchone():
                        omitidos += 1
                        continue

                # 2. ¿Hay una fila vieja sin codigo que calce (fecha+monto+RUT)?
                cur.execute("""
                    SELECT id FROM movimientos_banco
                    WHERE fecha = %s AND monto_abono = %s
                      AND regexp_replace(upper(COALESCE(rut_emisor, '')), '[^0-9K]', '', 'g') = %s
                      AND codigo_transferencia IS NULL
                    ORDER BY id LIMIT 1
                """, (fecha, monto, _rut_digitos(rut)))
                match = cur.fetchone()

                if match:
                    cur.execute(
                        "UPDATE movimientos_banco SET codigo_transferencia = %s WHERE id = %s",
                        (codigo, match[0]))
                    enriquecidos += 1
                else:
                    cur.execute("""
                        INSERT INTO movimientos_banco
                            (fecha, rut_emisor, nombre_emisor, monto_abono, conciliado, codigo_transferencia)
                        VALUES (%s, %s, %s, %s, FALSE, %s)
                    """, (fecha, rut, nombre, monto, codigo))
                    insertados += 1

            except psycopg2.Error as e:
                print(f"  Error procesando fila: {e}")
                errores += 1

    return insertados, enriquecidos, omitidos, errores


def main():
    ruta_arg = sys.argv[1] if len(sys.argv) > 1 else None

    print("=" * 60)
    print("ZIGURAT ERP - Importar Transferencias Itau")
    print("=" * 60)
    print()

    archivos = encontrar_excels(ruta_arg)
    print(f"  Archivos a procesar: {len(archivos)}")

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    print(f"  Conectado a: {DB_CONFIG['dbname']}")
    print()

    tot_ins = tot_enr = tot_omi = tot_err = 0
    for ruta in archivos:
        df = leer_excel(ruta)
        df = mapear_columnas(df)
        ins, enr, omi, err = importar(df, conn)
        tot_ins += ins
        tot_enr += enr
        tot_omi += omi
        tot_err += err
        print(f"  {ruta.name:38s} filas:{len(df):4d}  nuevas:{ins:4d}  enriquecidas:{enr:4d}  ya:{omi:4d}")
    conn.close()

    print()
    print("=" * 60)
    print("RESULTADO")
    print("=" * 60)
    print(f"  Transferencias nuevas insertadas: {tot_ins}")
    print(f"  Filas viejas con codigo rellenado: {tot_enr}")
    if tot_omi:
        print(f"  Ya estaban completas (omitidas):   {tot_omi}")
    if tot_err:
        print(f"  Filas de pie/vacias (ignoradas):   {tot_err}")
    print("=" * 60)


if __name__ == "__main__":
    main()
