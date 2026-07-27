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
    from _console import force_utf8          # ejecutado como script
except ImportError:
    from scripts._console import force_utf8  # importado como paquete (tests, dashboard)

force_utf8()

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
    "77103092-0": "Petainer Chile",       # barriles — sin mapeo de items por ahora
    "77755083-7": "Bucarest",             # principal proveedor de maltas base (30 días)
    "76518077-5": "MACC SPA",             # maltas, lúpulos, adjuntos, clarificantes
    "76069212-3": "Gourmet Select",       # adjuntos especiales (nibs cacao, etc.)
    "76013386-8": "Clean Ice SA",         # CO2
    "77380521-0": "Lúdico",               # café en grano para la Stout Café/Cacao
}

# RUT → (nombre legible, categoría). Sus documentos van a gastos_operativos.
PROVEEDORES_GASTOS = {
    "76052927-3": ("Autopista Nueva Vespucio Sur", "transporte"),
    "96945440-8": ("Autopista Central",             "transporte"),
    "76496130-7": ("Costanera Norte",               "transporte"),
    "77630439-5": ("Operadora Sistemas Electronicos", "transporte"),  # peajes Stgo-San Antonio
    "76989164-1": ("Bonta Publicidad",              "servicios"),     # etiquetas e impresión
    "97023000-9": ("Banco Itau Chile",              "servicios"),     # comisiones bancarias
    "76120331-2": ("Comercial CL",                  "combustible"),   # diésel
    # El gas de producción va en "servicios" porque el modelo de costos lo
    # agrupa con agua y luz ($185.000/lote de servicios variables), no con el
    # diésel del furgón.
    "76747198-K": ("Central Gas",                   "servicios"),
    "76676921-7": ("Quezada, Quiroga, Yagnam y Cía", "transporte"),   # mecánico del furgón
    "77983419-0": ("Comercializadora M.I.F.",       "marketing"),     # polerones con logo
    # Retail variado (ferretería, repuestos, seguridad): sin categoría propia
    # porque lo que se compra ahí cambia en cada factura.
    "76568660-1": ("Easy Retail",                   "otros"),
}

# Substring del NmbItem (lowercase) → (nombre en maestro_insumos, unidades_por_paquete)
# precio_neto_unitario = PrcItem / unidades_por_paquete
ITEM_MAP = {
    # ── Almacén Cervecero ──────────────────────────────────────────────────────
    "fermoale ay4":           ("Levadura AY4",                        500),   # saco 500gr
    "lev500gr us-05":         ("Levadura US-05",                      500),   # saco 500gr
    "pinnacle american ale":  ("Levadura Pinnacle",                   500),   # saco 500gr
    "lupulo100gr magnum":     ("Lupulo Magnum",                       100),   # bolsa 100gr
    "lupulo1kg magnum":       ("Lupulo Magnum",                      1000),   # bolsa 1kg
    "polyclar brewbrite":     ("Clarificante Polyclar coccion",        100),
    "polyclar10":             ("Clarificante Polyclar 10 maduracion",  100),
    "spindasol sb3 1 kilo":   ("Clarificante SB3 maduracion",        1000),   # 1L = 1000ml
    "spindasol sb3 10k":      ("Clarificante SB3 maduracion",       10000),   # 10L = 10000ml
    "endozym aldc":           ("Endozym ALDC",                          1),   # unidad (1L aprox)
    # maltas Almacén (sacos 25kg, "MD S" = molido doble saco)
    "aroma 150 md s":         ("Malta Arome",                         25),
    "cara 50 md s":           ("Malta Cara 50",                       25),
    "biscuit 50 md s":        ("Malta Biscuit",                       25),

    # ── Mundo Cervecero (precios por kg directamente) ─────────────────────────
    "malta chocolate":        ("Malta Chocolate",                        1),
    "malta caraaroma":        ("Malta Cara Aroma",                       1),
    "trigo malteado claro":   ("Trigo Malteado claro",                   1),
    "trigo malteado best malz": ("Trigo Malteado claro",                 1),
    "dextrosa":               ("Dextrosa",                               1),
    "cara ruby":              ("Malta Cara Ruby",                        1),

    # ── Bucarest (bolsas de 25 kg, PrcItem = precio por bolsa) ───────────────
    "malta uma - pilsen":     ("Malta Pilsen",                          25),
    "malta uma - pale ale":   ("Malta Pale Ale",                        25),
    "malta uma - munich":     ("Malta Munich",                          25),
    "malta uma - caradex":    ("Malta Caradex",                         25),
    "malta uma - extra pale": ("Malta Extra Pale Ale",                  25),

    # ── MACC SPA ──────────────────────────────────────────────────────────────
    "pale ale patagonia malt 25":              ("Malta Pale Ale",        25),
    "munich patagonia malt 25":                ("Malta Munich",          25),
    "chocolate malt - crisp":                  ("Malta Chocolate",        1),
    "carafa especial tipo 2 weyermann 10 kilo":("Malta Carafa 2",        10),
    "carafa especial tipo 2 weyermann 1 kilo": ("Malta Carafa 2",         1),
    "roast barley":                            ("Cebada Tostada",         1),
    "roasted barley":                          ("Cebada Tostada",         1),  # la marca da igual
    "cara 50 ebc":                             ("Malta Cara 50",         10),  # saco Castle Malting
    "avena 10 kilos":                          ("Avena",                 10),
    "avena 1 kilo":                            ("Avena",                  1),
    "hojuelas de cebada 10":                   ("Hojuela de Cebada",     10),
    "hojuelas de cebada 1 kilo":               ("Hojuela de Cebada",      1),
    "lupulo hallertau magnum 500":             ("Lupulo Magnum",         500),
    "lupulo el dorado 1 kilo":                 ("Lupulo El Dorado",     1000),
    "lupulo citra 1 kilo":                     ("Lupulo Citra",         1000),
    "cola de pescado 100":                     ("Cola de Pescado",       100),
    "cola de pescado 50":                      ("Cola de Pescado",        50),

    # ── Gourmet Select ────────────────────────────────────────────────────────
    "grue de cacao":          ("Cacao",                                   1),

    # ── Clean Ice SA ──────────────────────────────────────────────────────────
    "co2 anhidrido":          ("CO2",                                     1),

    # ── Lúdico (café en grano de la Stout Café/Cacao) ─────────────────────────
    # Claves sin tilde a propósito: el NmbItem llega como "Café grano ..." y el
    # match es por substring, así no depende de cómo venga escrito el acento.
    # El formato de 500 g divide por 0,5 para dejar siempre el precio por kilo.
    "grano bolivia 1kg":      ("Café",                                    1),
    "grano bolivia 500g":     ("Café",                                  0.5),

    # ── Limpieza (Mundo Cervecero y Almacén Cervecero) ────────────────────────
    "base peracetico":        ("Desinfectante Peracetico",                 1),   # precio por litro
    "detergente alcalino 5":  ("Detergente Alcalino",                      5),   # bidón 5 kg
    "detergente alcalino 6":  ("Detergente Alcalino",                      6),   # bidón 6 kg
    "detergente alcalino 23": ("Detergente Alcalino",                     23),   # bidón 23 kg

    # ── Envasado — botellas (sacos de 70 u 80 unidades) ──────────────────────
    "saco  70 botella baviera":         ("Botella 330ml Baviera",          70),  # Mundo Cervecero
    "saco 70 botella baviera":          ("Botella 330ml Baviera",          70),
    "saco 70 botellas genericas":       ("Botella 330ml",                  70),  # Mundo Cervecero
    "botellas 330cc generica saco 80":  ("Botella 330ml",                  80),  # Almacén Cervecero
    "botellas 330cc baviera saco 80":   ("Botella 330ml Baviera",          80),  # Almacén Cervecero

    # ── Envasado — tapas ──────────────────────────────────────────────────────
    "tapa corona":            ("Tapa Corona",                               1),  # precio por unidad
    "tapa protectora barril": ("Tapa Barril",                               1),  # precio por unidad

    # ── Envasado — cajas ──────────────────────────────────────────────────────
    "caja sola para 12 botellas": ("Caja 12 botellas",                      1),
    "caja sola / botella 330cc":  ("Caja 24 botellas",                      1),

    # ── Envasado — barriles PET ───────────────────────────────────────────────
    "petainer keg 30l":           ("Barril PET 30L",   1),   # Petainer Chile
    "barril plastico one way 30l": ("Barril PET 30L",  1),   # MACC SPA

    # ── Envasado — cajas MACC ─────────────────────────────────────────────────
    "caja sin separadores para 24": ("Caja 24 botellas", 1),

    # ── Clarificantes adicionales ─────────────────────────────────────────────
    "super f 500 ml":         ("Clarificante Super F", 500),  # 500ml → precio/ml

    # ── Limpieza adicional ────────────────────────────────────────────────────
    "desincrustante concentrado": ("Desincrustante",   1),    # precio por litro
}


def _load_procesados():
    if LOG_PROCESADOS.exists():
        with open(LOG_PROCESADOS, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def _save_procesados(procesados):
    with open(LOG_PROCESADOS, "w", encoding="utf-8") as f:
        json.dump(sorted(procesados), f, indent=2, ensure_ascii=False)


def _parsear_documento(doc):
    """Extrae datos de un elemento <Documento> ET. Retorna dict o None si inválido."""
    enc = doc.find("Encabezado")
    if enc is None:
        return None
    id_doc  = enc.find("IdDoc")
    emisor  = enc.find("Emisor")
    totales = enc.find("Totales")
    if id_doc is None or emisor is None or totales is None:
        return None

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


def parse_contenido(raw, nombre="(sin nombre)"):
    """Parsea los bytes de un DTE XML (ISO-8859-1). Retorna lista de dicts.

    Función pura (no lee disco): la comparten la CLI y el importador del
    dashboard. Un archivo puede traer varios <Documento> (descarga masiva SII).
    """
    content = raw.replace(b'encoding="ISO-8859-1"', b'encoding="UTF-8"')
    content = content.replace(b"encoding='ISO-8859-1'", b"encoding='UTF-8'")
    content_str = content.decode("iso-8859-1")
    content_clean = re.sub(r' xmlns="[^"]+"', "", content_str)

    root = ET.fromstring(content_clean.encode("utf-8"))
    documentos = root.findall(".//Documento")
    if not documentos:
        raise ValueError(f"No se encontró <Documento> en {nombre}")

    dtes = []
    for doc in documentos:
        datos = _parsear_documento(doc)
        if datos:
            dtes.append(datos)
    return dtes


def parse_xml(filepath):
    """Parsea DTE XML (ISO-8859-1). Retorna lista de dicts — uno por documento."""
    with open(filepath, "rb") as f:
        raw = f.read()
    return parse_contenido(raw, filepath.name)


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
                dtes = parse_xml(xml_path)
            except Exception as e:
                print(f"  ERROR al parsear: {e}")
                continue

            # Un archivo puede tener múltiples documentos (descarga masiva SII)
            if len(dtes) > 1:
                print(f"  [{len(dtes)} documentos en este archivo]")

            archivo_con_error = False
            for dte in dtes:
                rut = dte["rut_emisor"]
                print(f"  Folio {dte['folio']} | {dte['razon_social']} ({rut}) | ${dte['monto_total']:,}")

                with conn:
                    cur = conn.cursor()
                    if rut in PROVEEDORES_GASTOS:
                        _, categoria = PROVEEDORES_GASTOS[rut]
                        insertado = procesar_gasto(dte, categoria, cur)
                        estado = "insertado" if insertado else "ya existía"
                        print(f"    Gasto [{categoria}]: {estado}")
                    elif rut in PROVEEDORES_INSUMOS:
                        actualizados, no_mapeados = procesar_insumos(dte, cur)
                        for msg in actualizados:
                            print(f"    Precio: {msg}")
                        for nombre in no_mapeados:
                            print(f"    Sin mapeo (omitido): {nombre}")
                        if not actualizados and not no_mapeados:
                            print(f"    Sin ítems reconocidos para {PROVEEDORES_INSUMOS[rut]}")
                    else:
                        print(f"    AVISO: RUT {rut} sin clasificar — omitido")
                        print(f"    Para procesar: agregar a PROVEEDORES_INSUMOS o PROVEEDORES_GASTOS")
                        archivo_con_error = True

            if not archivo_con_error:
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
