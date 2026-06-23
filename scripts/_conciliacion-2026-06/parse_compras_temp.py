#!/usr/bin/env python3
import re
import sys
import json
from pathlib import Path

def limpiar(valor):
    if valor is None:
        return None
    return (valor
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .strip())

def extraer(patron, texto, default=None):
    match = re.search(patron, texto, re.DOTALL)
    return limpiar(match.group(1)) if match else default

def extraer_int(patron, texto, default=0):
    valor = extraer(patron, texto)
    try:
        return int(valor) if valor else default
    except (ValueError, TypeError):
        return default

def extraer_float(patron, texto, default=0.0):
    valor = extraer(patron, texto)
    try:
        return float(valor) if valor else default
    except (ValueError, TypeError):
        return default

def parsear_dte_compras(ruta_xml):
    ruta = Path(ruta_xml)
    if not ruta.exists():
        print(f"No existe {ruta_xml}")
        return []

    with open(ruta, "r", encoding="latin-1") as f:
        content = f.read()

    docs_xml = re.findall(r'<Documento ID=.*?</Documento>', content, re.DOTALL)
    if not docs_xml:
        # Intenta sin ID
        docs_xml = re.findall(r'<Documento>.*?</Documento>', content, re.DOTALL)
    
    documentos = []
    for doc_xml in docs_xml:
        tipo_dte = extraer(r'<TipoDTE>(.*?)</TipoDTE>', doc_xml)
        folio = extraer_int(r'<Folio>(.*?)</Folio>', doc_xml)
        fecha = extraer(r'<FchEmis>(.*?)</FchEmis>', doc_xml)
        
        # Emisor
        rut_emis = extraer(r'<RUTEmisor>(.*?)</RUTEmisor>', doc_xml)
        rzn_emis = extraer(r'<RznSoc>(.*?)</RznSoc>', doc_xml)
        
        # Totales
        mnt_neto = extraer_int(r'<MntNeto>(.*?)</MntNeto>', doc_xml)
        iva = extraer_int(r'<IVA>(.*?)</IVA>', doc_xml)
        mnt_exe = extraer_int(r'<MntExe>(.*?)</MntExe>', doc_xml)
        mnt_total = extraer_int(r'<MntTotal>(.*?)</MntTotal>', doc_xml)
        
        # Detalle
        detalles_xml = re.findall(r'<Detalle>(.*?)</Detalle>', doc_xml, re.DOTALL)
        items = []
        for det in detalles_xml:
            nombre = extraer(r'<NmbItem>(.*?)</NmbItem>', det)
            desc = extraer(r'<DscItem>(.*?)</DscItem>', det)
            cantidad = extraer_float(r'<QtyItem>(.*?)</QtyItem>', det, 1.0)
            unidad = extraer(r'<UnmdItem>(.*?)</UnmdItem>', det)
            precio = extraer_float(r'<PrcItem>(.*?)</PrcItem>', det, 0.0)
            total = extraer_int(r'<MontoItem>(.*?)</MontoItem>', det, 0)
            
            items.append({
                "nombre": nombre,
                "descripcion": desc,
                "cantidad": cantidad,
                "unidad": unidad,
                "precio_unitario": precio,
                "total": total
            })
            
        documentos.append({
            "folio": folio,
            "tipo_dte": tipo_dte,
            "fecha": fecha,
            "emisor_rut": rut_emis,
            "emisor_nombre": rzn_emis,
            "monto_neto": mnt_neto,
            "iva": iva,
            "monto_exento": mnt_exe,
            "monto_total": mnt_total,
            "items": items
        })
    return documentos

def main():
    print("PROCESANDO XML EXENTO (Type 34):")
    docs_ex = parsear_dte_compras("facturas-compras/DTE_DOWN763080122026-05-24EX.xml")
    for d in docs_ex:
        print(f"Folio: {d['folio']} | Emisor: {d['emisor_nombre']} | Fecha: {d['fecha']} | Total: ${d['monto_total']:,}")
        for item in d['items']:
            print(f"  - {item['nombre']} | Cant: {item['cantidad']} {item['unidad']} | Prc: ${item['precio_unitario']:.2f} | Total: ${item['total']:,}")
            
    print("\nPROCESANDO XML AFECTO (Type 33):")
    docs_af = parsear_dte_compras("facturas-compras/DTE_DOWN763080122026-05-24.xml")
    for d in docs_af:
        print(f"Folio: {d['folio']} | Emisor: {d['emisor_nombre']} | Fecha: {d['fecha']} | Total: ${d['monto_total']:,}")
        for item in d['items']:
            print(f"  - {item['nombre']} | Cant: {item['cantidad']} {item['unidad']} | Prc: ${item['precio_unitario']:.2f} | Total: ${item['total']:,}")

if __name__ == "__main__":
    main()
