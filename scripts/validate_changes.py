#!/usr/bin/env python3
"""
validate_changes.py - Zigurat ERP
Valida el changes.json generado por parse_dte.py ANTES de insertar
en PostgreSQL. Si hay errores, los reporta y detiene el proceso.

Uso:
    python validate_changes.py changes.json

Retorna:
    Exit code 0 → todo OK, listo para sync_db.py
    Exit code 1 → hay errores, NO insertar
"""

import json
import re
import sys
from pathlib import Path


# ─── Configuración ────────────────────────────────────────────────────────────
TIPOS_DTE_VALIDOS = {"33", "34", "39", "41", "61", "52"}

CAMPOS_VENTA_OBLIGATORIOS = [
    "folio", "tipo_documento", "fecha", "rut_cliente",
    "monto_neto", "iva", "monto_total"
]

CAMPOS_CLIENTE_OBLIGATORIOS = [
    "rut_cliente", "razon_social"
]

CAMPOS_PRODUCTO_OBLIGATORIOS = [
    "nombre_producto", "cantidad", "precio_unitario", "total_linea"
]


# ─── Validadores ──────────────────────────────────────────────────────────────

def validar_rut(rut):
    """
    Valida que un RUT chileno tenga formato correcto.
    Formatos válidos: 76308012-9 / 77290617-K
    """
    if not rut:
        return False
    patron = r'^\d{7,8}-[\dkK]$'
    return bool(re.match(patron, rut))


def validar_fecha(fecha):
    """Valida formato YYYY-MM-DD."""
    if not fecha:
        return False
    patron = r'^\d{4}-\d{2}-\d{2}$'
    return bool(re.match(patron, fecha))


def validar_totales(venta):
    """
    Verifica que monto_neto + iva + impuesto_adicional ≈ monto_total
    Tolerancia de $1 por redondeo del SII.
    """
    calculado = venta["monto_neto"] + venta["iva"] + venta.get("impuesto_adicional", 0)
    diferencia = abs(calculado - venta["monto_total"])
    return diferencia <= 1, calculado, diferencia


def validar_folio(folio):
    """Valida que el folio sea un número positivo."""
    return isinstance(folio, int) and folio > 0


# ─── Lógica principal ─────────────────────────────────────────────────────────

def validar_changes(changes):
    """
    Valida todo el changes.json y retorna lista de errores.
    """
    errores = []
    advertencias = []
    folios_vistos = set()

    documentos = changes.get("documentos", [])

    if not documentos:
        errores.append("ERROR CRÍTICO: No hay documentos en changes.json")
        return errores, advertencias

    for i, doc in enumerate(documentos):
        prefijo = f"Doc {i+1}"
        venta    = doc.get("venta", {})
        cliente  = doc.get("cliente", {})
        productos = doc.get("productos", [])

        folio = venta.get("folio", "?")
        prefijo = f"Folio {folio}"

        # ── 1. Campos obligatorios en venta ──────────────────────────────────
        for campo in CAMPOS_VENTA_OBLIGATORIOS:
            if not venta.get(campo) and venta.get(campo) != 0:
                errores.append(
                    f"{prefijo} → Campo obligatorio vacío en venta: '{campo}'"
                )

        # ── 2. Folio válido ───────────────────────────────────────────────────
        if not validar_folio(folio):
            errores.append(f"{prefijo} → Folio inválido: '{folio}'")

        # ── 3. Folio duplicado dentro del mismo XML ───────────────────────────
        clave = (folio, venta.get("tipo_documento"))
        if clave in folios_vistos:
            errores.append(
                f"{prefijo} → Folio duplicado dentro del XML "
                f"(folio:{folio}, tipo:{venta.get('tipo_documento')})"
            )
        folios_vistos.add(clave)

        # ── 4. Tipo DTE válido ────────────────────────────────────────────────
        tipo = venta.get("tipo_documento")
        if tipo not in TIPOS_DTE_VALIDOS:
            errores.append(
                f"{prefijo} → Tipo DTE inválido: '{tipo}'. "
                f"Válidos: {', '.join(sorted(TIPOS_DTE_VALIDOS))}"
            )

        # ── 5. Fecha válida ───────────────────────────────────────────────────
        if not validar_fecha(venta.get("fecha")):
            errores.append(
                f"{prefijo} → Fecha inválida: '{venta.get('fecha')}'. "
                f"Formato esperado: YYYY-MM-DD"
            )

        # ── 6. RUT cliente válido ─────────────────────────────────────────────
        rut = venta.get("rut_cliente")
        if not validar_rut(rut):
            errores.append(
                f"{prefijo} → RUT cliente inválido: '{rut}'. "
                f"Formato esperado: XXXXXXXX-X"
            )

        # ── 7. Montos válidos (negativos permitidos en NC tipo 61) ────────────
        es_nota_credito = tipo == "61"
        for campo_monto in ["monto_neto", "iva", "monto_total"]:
            valor = venta.get(campo_monto, 0)
            if not isinstance(valor, (int, float)):
                errores.append(
                    f"{prefijo} → Monto inválido en '{campo_monto}': '{valor}'"
                )
            elif not es_nota_credito and valor < 0:
                errores.append(
                    f"{prefijo} → Monto negativo no permitido en '{campo_monto}': '{valor}'"
                )
            elif es_nota_credito and valor > 0:
                errores.append(
                    f"{prefijo} → Nota de Crédito debe tener monto negativo en '{campo_monto}': '{valor}'"
                )

        # ── 8. Coherencia de totales ──────────────────────────────────────────
        ok_total, calculado, diferencia = validar_totales(venta)
        if not ok_total:
            errores.append(
                f"{prefijo} → Totales no cuadran: "
                f"neto({venta['monto_neto']}) + "
                f"iva({venta['iva']}) + "
                f"imp_adic({venta.get('impuesto_adicional', 0)}) = "
                f"{calculado} ≠ total({venta['monto_total']}) "
                f"(diferencia: ${diferencia})"
            )

        # ── 9. Campos obligatorios en cliente ─────────────────────────────────
        for campo in CAMPOS_CLIENTE_OBLIGATORIOS:
            if not cliente.get(campo):
                errores.append(
                    f"{prefijo} → Campo obligatorio vacío en cliente: '{campo}'"
                )

        # ── 10. RUT cliente coherente entre venta y cliente ───────────────────
        if venta.get("rut_cliente") != cliente.get("rut_cliente"):
            errores.append(
                f"{prefijo} → RUT no coincide: "
                f"venta tiene '{venta.get('rut_cliente')}' "
                f"pero cliente tiene '{cliente.get('rut_cliente')}'"
            )

        # ── 11. Productos ─────────────────────────────────────────────────────
        if not productos:
            advertencias.append(
                f"{prefijo} → No tiene líneas de productos"
            )
        else:
            for j, prod in enumerate(productos):
                for campo in CAMPOS_PRODUCTO_OBLIGATORIOS:
                    if not prod.get(campo) and prod.get(campo) != 0:
                        errores.append(
                            f"{prefijo} Línea {j+1} → "
                            f"Campo obligatorio vacío en producto: '{campo}'"
                        )

                # Verificar que total_linea ≈ cantidad * precio_unitario
                if prod.get("cantidad") and prod.get("precio_unitario"):
                    esperado = round(prod["cantidad"] * prod["precio_unitario"])
                    real = prod.get("total_linea", 0)
                    if abs(esperado - real) > 1:
                        advertencias.append(
                            f"{prefijo} Línea {j+1} '{prod.get('nombre_producto')}' → "
                            f"total_linea({real}) ≠ cantidad({prod['cantidad']}) × "
                            f"precio({prod['precio_unitario']}) = {esperado}"
                        )

        # ── 12. Nota de Crédito debe tener referencia ─────────────────────────
        if tipo == "61":
            if not venta.get("folio_referencia"):
                advertencias.append(
                    f"{prefijo} → Es Nota de Crédito pero no tiene folio_referencia"
                )

    return errores, advertencias


def main():
    if len(sys.argv) < 2:
        print("Uso: python validate_changes.py changes.json")
        sys.exit(1)

    ruta = Path(sys.argv[1])
    if not ruta.exists():
        print(f"ERROR: No se encontró el archivo: {ruta}")
        sys.exit(1)

    print("=" * 60)
    print("ZIGURAT ERP — Validador de Changes")
    print("=" * 60)
    print()

    with open(ruta, "r", encoding="utf-8") as f:
        changes = json.load(f)

    # Mostrar metadata
    meta = changes.get("metadata", {})
    print(f"  Archivo origen:    {meta.get('archivo_origen', '?')}")
    print(f"  Documentos:        {meta.get('total_documentos', '?')}")
    print(f"  Clientes únicos:   {meta.get('total_clientes', '?')}")
    print(f"  Líneas productos:  {meta.get('total_productos', '?')}")
    print()

    # Validar
    errores, advertencias = validar_changes(changes)

    # Mostrar advertencias
    if advertencias:
        print(f"⚠️  ADVERTENCIAS ({len(advertencias)}):")
        for adv in advertencias:
            print(f"   ⚠  {adv}")
        print()

    # Mostrar errores
    if errores:
        print(f"❌ ERRORES ENCONTRADOS ({len(errores)}):")
        for err in errores:
            print(f"   ✗  {err}")
        print()
        print("=" * 60)
        print("❌ VALIDACIÓN FALLIDA — No se insertará nada en la BD")
        print("   Corrige los errores y vuelve a ejecutar parse_dte.py")
        print("=" * 60)
        sys.exit(1)

    # Todo OK — crear flag para que sync_db.py sepa que fue validado
    Path(".changes_validated").write_text("ok", encoding="utf-8")

    print("=" * 60)
    print("✅ VALIDACIÓN EXITOSA — Sin errores")
    if advertencias:
        print(f"   (con {len(advertencias)} advertencia(s) menores)")
    print()
    print("   Siguiente paso: python sync_db.py changes.json")
    print("=" * 60)
    sys.exit(0)


if __name__ == "__main__":
    main()
