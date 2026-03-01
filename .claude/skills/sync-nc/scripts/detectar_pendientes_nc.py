#!/usr/bin/env python3
"""
detectar_pendientes_nc.py — Zigurat ERP
Detecta XMLs en 'Notas de Credito/' que aún no están sincronizados en la BD.
Imprime __PENDIENTES__:archivo1.xml,archivo2.xml para que el skill los procese.
"""
import os
import re
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2. Instala con: pip install psycopg2-binary")
    sys.exit(1)


def _load_env():
    """Carga variables de entorno desde .env en la raíz del proyecto."""
    # parents[4] = Agente_Facturas/ (raíz del proyecto)
    env_file = Path(__file__).parents[4] / ".env"
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

# Carpeta de Notas de Crédito (relativa a la raíz del proyecto)
NC_DIR = Path("Notas de Credito")


def get_folios_nc_from_xml(xml_path):
    """
    Extrae pares (folio, tipo_dte) del XML solo para NCs (tipo 61).
    Usa regex para evitar depender del namespace del SII.
    """
    try:
        content = xml_path.read_text(encoding="iso-8859-1")
        folios = re.findall(r"<Folio>(\d+)</Folio>", content)
        tipos = re.findall(r"<TipoDTE>(\d+)</TipoDTE>", content)
        pares = list(zip(folios, tipos))
        return [(f, t) for f, t in pares if t == "61"]
    except Exception as e:
        print(f"  ADVERTENCIA: No se pudo leer {xml_path.name}: {e}")
        return []


def main():
    if not NC_DIR.exists():
        print(f"ERROR: No se encontro la carpeta '{NC_DIR}'")
        sys.exit(1)

    xmls = sorted(NC_DIR.glob("*.xml"))
    if not xmls:
        print("No hay archivos XML en 'Notas de Credito/'.")
        print("__PENDIENTES__:")
        return

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    cur = conn.cursor()
    pendientes = []

    print(f"Verificando {len(xmls)} archivo(s) en 'Notas de Credito/'...\n")

    for xml_path in xmls:
        pares_nc = get_folios_nc_from_xml(xml_path)
        if not pares_nc:
            print(f"  SKIP: {xml_path.name} (sin NCs tipo 61)")
            continue

        # Verificar cuantos folios ya están en la BD
        placeholders = ",".join(["(%s,%s)"] * len(pares_nc))
        valores = [v for par in pares_nc for v in par]

        cur.execute(
            f"SELECT COUNT(*) FROM ventas "
            f"WHERE (folio::integer, tipo_documento::text) IN ({placeholders})",
            valores,
        )
        encontrados = cur.fetchone()[0]
        faltantes = len(pares_nc) - encontrados

        if faltantes > 0:
            pendientes.append(xml_path.name)
            print(f"  PENDIENTE: {xml_path.name} ({faltantes}/{len(pares_nc)} NCs sin sincronizar)")
        else:
            print(f"  OK: {xml_path.name} ({len(pares_nc)} NCs ya sincronizadas)")

    conn.close()
    print()

    if pendientes:
        print(f"{len(pendientes)} archivo(s) con NCs pendientes de sincronizar.")
        print(f"__PENDIENTES__:{','.join(pendientes)}")
    else:
        print("Todo sincronizado. No hay XMLs pendientes en 'Notas de Credito/'.")
        print("__PENDIENTES__:")


if __name__ == "__main__":
    main()
