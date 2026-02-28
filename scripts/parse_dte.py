#!/usr/bin/env python3
"""
parse_dte.py - Zigurat ERP
Lee un archivo XML del SII (DTE) y genera un changes.json
con el plan de inserción para PostgreSQL.

Uso:
    python parse_dte.py <archivo.xml>
    python parse_dte.py DTE_DOWN763080122026-02-22.xml

Genera:
    changes.json → plan de inserción listo para validate_changes.py
"""

import re
import json
import sys
from pathlib import Path
from datetime import datetime


# ─── Tipos de documento DTE ───────────────────────────────────────────────────
TIPOS_DTE = {
    "33": "Factura Afecta",
    "34": "Factura No Afecta",
    "39": "Boleta Afecta",
    "41": "Boleta No Afecta",
    "61": "Nota de Crédito",
    "52": "Guía de Despacho",
}

# ─── Formas de pago ───────────────────────────────────────────────────────────
FORMAS_PAGO = {
    "1": "Contado",
    "2": "Crédito",
    "3": "Sin Costo",
}


def limpiar(valor):
    """Limpia un valor XML: decodifica entidades HTML y espacios."""
    if valor is None:
        return None
    return (valor
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .strip())


def extraer(patron, texto, default=None):
    """Extrae el primer match de un patrón en un texto."""
    match = re.search(patron, texto, re.DOTALL)
    return limpiar(match.group(1)) if match else default


def extraer_int(patron, texto, default=0):
    """Extrae un valor entero."""
    valor = extraer(patron, texto)
    try:
        return int(valor) if valor else default
    except (ValueError, TypeError):
        return default


def extraer_float(patron, texto, default=0.0):
    """Extrae un valor float."""
    valor = extraer(patron, texto)
    try:
        return float(valor) if valor else default
    except (ValueError, TypeError):
        return default


def parsear_documento(doc_xml):
    """
    Parsea un bloque <Documento> del XML y retorna un dict
    con la estructura lista para insertar en PostgreSQL.
    """

    # ── Cabecera ──────────────────────────────────────────────────────────────
    tipo_dte  = extraer(r'<TipoDTE>(.*?)</TipoDTE>', doc_xml)
    folio     = extraer_int(r'<Folio>(.*?)</Folio>', doc_xml)
    fecha     = extraer(r'<FchEmis>(.*?)</FchEmis>', doc_xml)
    fma_pago  = extraer(r'<FmaPago>(.*?)</FmaPago>', doc_xml)

    # ── Receptor (cliente) ────────────────────────────────────────────────────
    rut_recep   = extraer(r'<RUTRecep>(.*?)</RUTRecep>', doc_xml)
    razon_recep = extraer(r'<RznSocRecep>(.*?)</RznSocRecep>', doc_xml)
    dir_recep   = extraer(r'<DirRecep>(.*?)</DirRecep>', doc_xml)
    cmna_recep  = extraer(r'<CmnaRecep>(.*?)</CmnaRecep>', doc_xml)

    # ── Totales ───────────────────────────────────────────────────────────────
    mnt_neto    = extraer_int(r'<MntNeto>(.*?)</MntNeto>', doc_xml)
    tasa_iva    = extraer_float(r'<TasaIVA>(.*?)</TasaIVA>', doc_xml, 19.0)
    iva         = extraer_int(r'<IVA>(.*?)</IVA>', doc_xml)
    imp_adic    = extraer_int(r'<MontoImp>(.*?)</MontoImp>', doc_xml, 0)
    mnt_total   = extraer_int(r'<MntTotal>(.*?)</MntTotal>', doc_xml)

    # Las Notas de Crédito reducen ventas: se guardan con montos negativos
    # para que cualquier SUM sobre la tabla dé el resultado correcto.
    if tipo_dte == "61":
        mnt_neto  = -abs(mnt_neto)
        iva       = -abs(iva)
        imp_adic  = -abs(imp_adic)
        mnt_total = -abs(mnt_total)

    # ── Referencia (solo Notas de Crédito) ────────────────────────────────────
    folio_ref   = extraer_int(r'<FolioRef>(.*?)</FolioRef>', doc_xml, None)
    tipo_ref    = extraer(r'<TpoDocRef>(.*?)</TpoDocRef>', doc_xml)
    razon_ref   = extraer(r'<RazonRef>(.*?)</RazonRef>', doc_xml)

    # ── Detalle (líneas de productos) ─────────────────────────────────────────
    detalles_xml = re.findall(r'<Detalle>(.*?)</Detalle>', doc_xml, re.DOTALL)
    productos = []
    for det in detalles_xml:
        nombre   = extraer(r'<NmbItem>(.*?)</NmbItem>', det)
        cantidad = extraer_float(r'<QtyItem>(.*?)</QtyItem>', det, 1.0)
        precio   = extraer_float(r'<PrcItem>(.*?)</PrcItem>', det, 0.0)
        total    = extraer_int(r'<MontoItem>(.*?)</MontoItem>', det, 0)

        productos.append({
            "nombre_producto": nombre,
            "cantidad":        cantidad,
            "precio_unitario": precio,
            "total_linea":     total,
        })

    return {
        # ── Para tabla: ventas ─────────────────────────────────────────────
        "venta": {
            "folio":                     folio,
            "tipo_documento":            tipo_dte,
            "tipo_documento_nombre":     TIPOS_DTE.get(tipo_dte, f"Tipo {tipo_dte}"),
            "fecha":                     fecha,
            "rut_cliente":               rut_recep,
            "monto_neto":                mnt_neto,
            "iva":                       iva,
            "impuesto_adicional":        imp_adic,
            "monto_total":               mnt_total,
            "forma_pago":                FORMAS_PAGO.get(fma_pago, fma_pago),
            # Referencia (solo para Notas de Crédito)
            "folio_referencia":          folio_ref,
            "tipo_documento_referencia": tipo_ref,
            "razon_referencia":          razon_ref,
        },

        # ── Para tabla: clientes ───────────────────────────────────────────
        "cliente": {
            "rut_cliente":  rut_recep,
            "razon_social": razon_recep,
            "direccion":    dir_recep,
            "comuna":       cmna_recep,
        },

        # ── Para tabla: productos ──────────────────────────────────────────
        "productos": productos,
    }


def parsear_xml(ruta_xml):
    """
    Lee el archivo XML del SII y retorna lista de documentos parseados.
    """
    # Intentar con el nombre exacto, luego con variantes de extensión
    ruta = Path(ruta_xml)
    if not ruta.exists():
        candidatos = [
            Path(ruta_xml + ".xml"),
            Path(str(ruta_xml).replace(".xml.xml", ".xml")),
        ]
        ruta = next((r for r in candidatos if r.exists()), None)
        if not ruta:
            print(f"ERROR: No se encontró el archivo: {ruta_xml}")
            sys.exit(1)

    # El SII usa encoding ISO-8859-1
    with open(ruta, "r", encoding="latin-1") as f:
        content = f.read()

    # Extraer todos los bloques <Documento>
    docs_xml = re.findall(r'<Documento ID=.*?</Documento>', content, re.DOTALL)

    if not docs_xml:
        print("ERROR: No se encontraron documentos DTE en el archivo.")
        sys.exit(1)

    print(f"✓ Archivo leído: {ruta.name}")
    print(f"✓ Documentos encontrados: {len(docs_xml)}")
    print()

    documentos = []
    for doc_xml in docs_xml:
        doc = parsear_documento(doc_xml)
        documentos.append(doc)

        # Mostrar resumen en pantalla
        v = doc["venta"]
        ref = f" → anula folio {v['folio_referencia']}" if v.get("folio_referencia") else ""
        print(f"  [{v['tipo_documento_nombre']}] "
              f"Folio:{v['folio']} | "
              f"Fecha:{v['fecha']} | "
              f"Cliente:{doc['cliente']['razon_social']} | "
              f"Total:${v['monto_total']:,}{ref}")

    return documentos


def generar_changes_json(documentos, ruta_xml):
    """
    Genera el archivo changes.json con el plan de inserción.
    """
    ahora = datetime.now().isoformat()

    changes = {
        "metadata": {
            "archivo_origen":   Path(ruta_xml).name,
            "fecha_proceso":    ahora,
            "total_documentos": len(documentos),
            "total_clientes":   len({d["cliente"]["rut_cliente"] for d in documentos}),
            "total_productos":  sum(len(d["productos"]) for d in documentos),
        },
        "documentos": documentos,
    }

    # Resumen financiero
    total_neto  = sum(d["venta"]["monto_neto"]  for d in documentos)
    total_iva   = sum(d["venta"]["iva"]          for d in documentos)
    total_imp   = sum(d["venta"]["impuesto_adicional"] for d in documentos)
    total_final = sum(d["venta"]["monto_total"]  for d in documentos)

    changes["resumen_financiero"] = {
        "total_monto_neto":         total_neto,
        "total_iva":                total_iva,
        "total_impuesto_adicional": total_imp,
        "total_monto_total":        total_final,
    }

    # Guardar
    output = Path("changes.json")
    with open(output, "w", encoding="utf-8") as f:
        json.dump(changes, f, ensure_ascii=False, indent=2)

    return changes


def main():
    if len(sys.argv) < 2:
        print("Uso: python parse_dte.py <archivo.xml>")
        print("Ejemplo: python parse_dte.py DTE_DOWN763080122026-02-22.xml")
        sys.exit(1)

    ruta_xml = sys.argv[1]

    print("=" * 60)
    print("ZIGURAT ERP — Parser DTE")
    print("=" * 60)
    print()

    # Parsear XML
    documentos = parsear_xml(ruta_xml)

    # Generar changes.json
    changes = generar_changes_json(documentos, ruta_xml)

    # Resumen final
    m = changes["metadata"]
    r = changes["resumen_financiero"]

    print()
    print("=" * 60)
    print("RESUMEN")
    print("=" * 60)
    print(f"  Documentos procesados: {m['total_documentos']}")
    print(f"  Clientes únicos:       {m['total_clientes']}")
    print(f"  Líneas de productos:   {m['total_productos']}")
    print()
    print(f"  Monto neto total:      ${r['total_monto_neto']:,}")
    print(f"  IVA total:             ${r['total_iva']:,}")
    print(f"  Impuesto adicional:    ${r['total_impuesto_adicional']:,}")
    print(f"  TOTAL:                 ${r['total_monto_total']:,}")
    print()
    print(f"✓ Plan generado: changes.json")
    print(f"  Siguiente paso: python validate_changes.py changes.json")
    print()


if __name__ == "__main__":
    main()
