#!/usr/bin/env python3
"""
sync_compras.py — Zigurat ERP
Procesa XMLs DTE en facturas-compras/:
  - Proveedor insumos → actualiza precio_neto_unitario en maestro_insumos
  - Proveedor gasto   → inserta en gastos_operativos
  - Proveedor desconocido → warning, se omite

Idempotente: registra archivos procesados en facturas-compras/.procesados.json.

Uso: python scripts/sync_compras.py
"""
import os, sys, json, re
from pathlib import Path
import xml.etree.ElementTree as ET

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2. Instala con: pip install psycopg2-binary")
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

CARPETA = Path(__file__).parent.parent / "facturas-compras"
LOG_PROCESADOS = CARPETA / ".procesados.json"

# RUT → nombre legible. Sus ítems se mapean a maestro_insumos.
PROVEEDORES_INSUMOS = {
    "76045387-0": "Mundo Cervecero",
    "76448126-7": "Almacén Cervecero",
    "77103092-0": "Petainer Chile",    # barriles — sin mapeo de items por ahora
}

# RUT → (nombre legible, categoría). Sus documentos van a gastos_operativos.
PROVEEDORES_GASTOS = {
    "76052927-3": ("Autopista Nueva Vespucio Sur", "transporte"),
}

# Substring del NmbItem (lowercase) → (nombre en maestro_insumos, unidades_por_paquete)
# precio_neto_unitario = PrcItem / unidades_por_paquete
ITEM_MAP = {
    "malta chocolate":        ("Malta Chocolate",                       1),
    "malta caraaroma":        ("Malta Cara Aroma",                      1),
    "fermoale ay4":           ("Levadura AY4",                        500),
    "lupulo100gr magnum":     ("Lupulo Magnum",                       100),
    "polyclar brewbrite":     ("Clarificante Polyclar coccion",        100),
    "polyclar10":             ("Clarificante Polyclar 10 maduracion",  100),
}


def _load_procesados():
    if LOG_PROCESADOS.exists():
        with open(LOG_PROCESADOS, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def _save_procesados(procesados):
    with open(LOG_PROCESADOS, "w", encoding="utf-8") as f:
        json.dump(sorted(procesados), f, indent=2, ensure_ascii=False)


def parse_xml(filepath):
    """Parsea DTE XML (ISO-8859-1). Devuelve dict con datos del documento."""
    with open(filepath, "rb") as f:
        raw = f.read()
    # Normalizar encoding para que ET pueda parsear sin error
    content = raw.replace(b'encoding="ISO-8859-1"', b'encoding="UTF-8"')
    content = content.replace(b"encoding='ISO-8859-1'", b"encoding='UTF-8'")
    content_str = content.decode("iso-8859-1")
    # Eliminar declaración de namespace de Signature para simplificar búsquedas
    content_clean = re.sub(r' xmlns="[^"]+"', "", content_str)

    root = ET.fromstring(content_clean.encode("utf-8"))
    doc = root.find(".//Documento")
    if doc is None:
        raise ValueError(f"No se encontró <Documento> en {filepath.name}")

    enc     = doc.find("Encabezado")
    id_doc  = enc.find("IdDoc")
    emisor  = enc.find("Emisor")
    totales = enc.find("Totales")

    monto_neto  = int(float(totales.findtext("MntNeto")  or 0))
    monto_total = int(float(totales.findtext("MntTotal") or 0))

    items = []
    for det in doc.findall("Detalle"):
        nombre = (det.findtext("NmbItem") or "").strip()
        qty    = float(det.findtext("QtyItem")    or 1)
        precio = float(det.findtext("PrcItem")    or 0)
        monto  = int(float(det.findtext("MontoItem") or 0))
        items.append({"nombre": nombre, "qty": qty, "precio_unitario": precio, "monto": monto})

    return {
        "tipo_dte":    id_doc.findtext("TipoDTE"),
        "folio":       id_doc.findtext("Folio"),
        "fecha":       id_doc.findtext("FchEmis"),
        "rut_emisor":  (emisor.findtext("RUTEmisor") or "").strip(),
        "razon_social": (emisor.findtext("RznSoc")   or "").strip(),
        "monto_neto":  monto_neto,
        "monto_total": monto_total,
        "items":       items,
    }


def procesar_insumos(dte, cur):
    """Actualiza precios en maestro_insumos según los items del DTE."""
    actualizados = []
    no_mapeados  = []
    for item in dte["items"]:
        nombre_lower = item["nombre"].lower()
        match = next(
            ((k, v) for k, v in ITEM_MAP.items() if k in nombre_lower), None
        )
        if not match:
            no_mapeados.append(item["nombre"])
            continue
        _, (nombre_bd, unidades_paquete) = match
        precio_por_unidad = round(item["precio_unitario"] / unidades_paquete, 4)
        cur.execute(
            "UPDATE maestro_insumos SET precio_neto_unitario = %s WHERE nombre = %s",
            (precio_por_unidad, nombre_bd)
        )
        if cur.rowcount:
            actualizados.append(f"{nombre_bd} -> ${precio_por_unidad:.4f}/unidad")
    return actualizados, no_mapeados


def procesar_gasto(dte, categoria, cur):
    """Inserta el DTE como gasto operativo. Retorna True si fue insertado."""
    descripcion = dte["items"][0]["nombre"] if dte["items"] else dte["razon_social"]
    cur.execute(
        """
        INSERT INTO gastos_operativos
            (folio, tipo_documento, fecha_emision, rut_emisor,
             razon_social_emisor, descripcion, monto_neto, monto_total, categoria)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (folio, rut_emisor) DO NOTHING
        """,
        (dte["folio"], dte["tipo_dte"], dte["fecha"], dte["rut_emisor"],
         dte["razon_social"], descripcion,
         dte["monto_neto"], dte["monto_total"], categoria)
    )
    return cur.rowcount > 0


def main():
    xmls = sorted(CARPETA.glob("*.xml"))
    if not xmls:
        print(f"No hay XMLs en {CARPETA}/")
        return

    procesados = _load_procesados()
    pendientes = [x for x in xmls if x.name not in procesados]

    print(f"XMLs en carpeta: {len(xmls)} | Procesados: {len(procesados)} | Pendientes: {len(pendientes)}")

    if not pendientes:
        print("Todo al día — nada nuevo que procesar.")
        return

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    nuevos_procesados = set()
    try:
        for xml_path in pendientes:
            print(f"\n-> {xml_path.name}")
            try:
                dte = parse_xml(xml_path)
            except Exception as e:
                print(f"  ERROR al parsear: {e}")
                continue

            rut = dte["rut_emisor"]
            print(f"  Emisor: {dte['razon_social']} ({rut}) | Folio {dte['folio']} | ${dte['monto_total']:,}")

            with conn:
                cur = conn.cursor()
                if rut in PROVEEDORES_GASTOS:
                    _, categoria = PROVEEDORES_GASTOS[rut]
                    insertado = procesar_gasto(dte, categoria, cur)
                    estado = "insertado" if insertado else "ya existía"
                    print(f"  Gasto operativo [{categoria}]: {estado}")
                elif rut in PROVEEDORES_INSUMOS:
                    actualizados, no_mapeados = procesar_insumos(dte, cur)
                    for msg in actualizados:
                        print(f"  Precio: {msg}")
                    for nombre in no_mapeados:
                        print(f"  Sin mapeo (omitido): {nombre}")
                    if not actualizados and not no_mapeados:
                        print(f"  Sin ítems reconocidos para {PROVEEDORES_INSUMOS[rut]}")
                else:
                    print(f"  AVISO: RUT {rut} sin clasificar — omitido")
                    print(f"  Para procesar: agregar a PROVEEDORES_INSUMOS o PROVEEDORES_GASTOS en sync_compras.py")
                    continue

            nuevos_procesados.add(xml_path.name)

        _save_procesados(procesados | nuevos_procesados)
        print(f"\nSync completo. Procesados nuevos: {len(nuevos_procesados)}")
        if nuevos_procesados:
            print("Verifica costos con: python scripts/costo_sku.py")

    except psycopg2.Error as e:
        print(f"ERROR DB: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
