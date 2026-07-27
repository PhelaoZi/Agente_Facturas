#!/usr/bin/env python3
"""
migrate_precio_fecha_dte.py — Zigurat ERP

Agrega `maestro_insumos.precio_fecha_dte`: la fecha de emisión de la factura
que fijó el precio vigente de cada insumo.

Sin esta columna, el precio de un insumo lo decide *el último archivo
procesado*, no *la factura más nueva*. Cargar la descarga masiva en orden
cronológico lo esconde, pero basta reimportar una factura vieja sola para que
pise el precio actual con uno de hace meses, en silencio y sin dejar rastro.
Con la fecha guardada, `procesar_insumos` solo actualiza si la factura es igual
o más nueva que la que fijó el precio.

El backfill sale de los XMLs ya archivados en facturas-compras/: por cada
insumo busca la factura más reciente que lo trae. Los que no aparecen en ningún
XML quedan en NULL (edición manual o carga anterior), y para esos el primer
UPDATE que llegue manda.

Idempotente. Uso: python scripts/migrate_precio_fecha_dte.py
"""
import os
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2. Instala con: pip install psycopg2-binary")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts import sync_compras


def main():
    try:
        conn = psycopg2.connect(**sync_compras.DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    ALTER TABLE maestro_insumos
                    ADD COLUMN IF NOT EXISTS precio_fecha_dte DATE
                """)
                print("Columna precio_fecha_dte lista.")

                # Fecha de la factura más reciente que trae cada insumo.
                fechas = {}
                xmls = sorted(sync_compras.CARPETA.glob("*.xml"))
                for xml_path in xmls:
                    try:
                        dtes = sync_compras.parse_xml(xml_path)
                    except Exception as e:
                        print(f"   Aviso: no se pudo leer {xml_path.name}: {e}")
                        continue
                    for dte in dtes:
                        if dte["rut_emisor"] not in sync_compras.PROVEEDORES_INSUMOS:
                            continue
                        for item in dte["items"]:
                            nombre_lower = item["nombre"].lower()
                            match = next(
                                (v for k, v in sync_compras.ITEM_MAP.items() if k in nombre_lower),
                                None)
                            if not match:
                                continue
                            nombre_bd = match[0]
                            if dte["fecha"] > fechas.get(nombre_bd, ""):
                                fechas[nombre_bd] = dte["fecha"]

                print(f"Revisados {len(xmls)} XML(s): {len(fechas)} insumo(s) con fecha conocida.")

                llenados = 0
                for nombre_bd, fecha in sorted(fechas.items()):
                    cur.execute(
                        """UPDATE maestro_insumos SET precio_fecha_dte = %s
                           WHERE nombre = %s AND precio_fecha_dte IS NULL""",
                        (fecha, nombre_bd))
                    if cur.rowcount:
                        llenados += 1
                        print(f"   {nombre_bd:<34} <- factura del {fecha}")

                cur.execute(
                    "SELECT COUNT(*) FROM maestro_insumos WHERE precio_fecha_dte IS NULL AND activo")
                sin_fecha = cur.fetchone()[0]

                print(f"OK — {llenados} insumo(s) con fecha nueva, "
                      f"{sin_fecha} activo(s) siguen en NULL (el primer UPDATE manda).")

    except psycopg2.Error as e:
        print(f"ERROR DB: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
