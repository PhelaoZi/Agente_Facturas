#!/usr/bin/env python3
"""
detectar_pendientes.py — Zigurat ERP
Lista los XMLs en facturas-ventas/ que tienen folios no sincronizados con PostgreSQL.

Uso:
    python .claude/skills/monitoreo-facturas/scripts/detectar_pendientes.py

Output:
    Lista de archivos con su estado (sincronizado / pendiente).
    Línea especial al final: __PENDIENTES__:archivo1.xml,archivo2.xml
    (vacía si todo está sincronizado)
"""

import os
import re
import sys
from pathlib import Path

# ─── Cargar .env ──────────────────────────────────────────────────────────────
def _load_env():
    env_file = Path(__file__).parent.parent.parent.parent.parent / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2. Instala con: pip install psycopg2-binary")
    sys.exit(1)

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}


# ─── Extraer folios de un XML ─────────────────────────────────────────────────
def get_folios_from_xml(xml_path):
    """Extrae pares (folio, tipo_dte) de un XML del SII."""
    try:
        with open(xml_path, "r", encoding="latin-1") as f:
            content = f.read()
        docs = re.findall(r'<Documento ID=.*?</Documento>', content, re.DOTALL)
        folios = []
        for doc in docs:
            tipo  = re.search(r'<TipoDTE>(.*?)</TipoDTE>', doc)
            folio = re.search(r'<Folio>(.*?)</Folio>', doc)
            if tipo and folio:
                folios.append((int(folio.group(1).strip()), tipo.group(1).strip()))
        return folios
    except Exception as e:
        print(f"  [!] Error leyendo {xml_path.name}: {e}")
        return []


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    facturas_dir = Path("facturas-ventas")
    if not facturas_dir.exists():
        print("ERROR: No existe el directorio facturas-ventas/")
        sys.exit(1)

    xmls = sorted(facturas_dir.glob("*.xml"))
    if not xmls:
        print("No hay archivos XML en facturas-ventas/")
        print("__PENDIENTES__:")
        sys.exit(0)

    print("=" * 60)
    print("ZIGURAT ERP — Monitor de Facturas")
    print("=" * 60)
    print(f"\n  Verificando {len(xmls)} XML(s) en facturas-ventas/...\n")

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur  = conn.cursor()
    except Exception as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    pendientes = []

    for xml_path in xmls:
        folios = get_folios_from_xml(xml_path)
        if not folios:
            print(f"  [!] {xml_path.name}: no se pudo leer o no tiene documentos")
            continue

        # Consultar cuántos folios ya existen en la BD
        placeholders = ",".join(["(%s,%s)"] * len(folios))
        valores      = [v for par in folios for v in par]
        cur.execute(
            f"SELECT COUNT(*) FROM ventas "
            f"WHERE (folio::integer, tipo_documento::text) IN ({placeholders})",
            valores,
        )
        count_existing = cur.fetchone()[0]

        if count_existing < len(folios):
            faltantes = len(folios) - count_existing
            pendientes.append(xml_path.name)
            print(f"  [PENDIENTE] {xml_path.name}: {faltantes}/{len(folios)} folio(s) pendiente(s)")
        else:
            print(f"  [OK] {xml_path.name}: sincronizado ({len(folios)} folio(s))")

    conn.close()
    print()

    if pendientes:
        print(f"  Archivos pendientes de sincronizar: {len(pendientes)}")
        for p in pendientes:
            print(f"    - {p}")
        print()
        # Línea especial para que el skill pueda parsear los pendientes
        print("__PENDIENTES__:" + ",".join(pendientes))
    else:
        print("[OK] Todo sincronizado. No hay facturas nuevas.")
        print("__PENDIENTES__:")

    print("=" * 60)


if __name__ == "__main__":
    main()
