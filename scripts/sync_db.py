#!/usr/bin/env python3
"""
sync_db.py - Zigurat ERP
Inserta en PostgreSQL los datos del changes.json validado.
SIEMPRE ejecutar validate_changes.py antes de este script.

Uso:
    python sync_db.py changes.json

Flujo completo:
    1. python parse_dte.py archivo.xml
    2. python validate_changes.py changes.json
    3. python sync_db.py changes.json        ← este script
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    print("ERROR: Falta la librería psycopg2.")
    print("Instala con: pip install psycopg2-binary")
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


# ─── Configuración de conexión ────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}


# ─── Conexión ─────────────────────────────────────────────────────────────────

def conectar():
    """Establece conexión a PostgreSQL."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL:")
        print(f"  {e}")
        print()
        print("Verifica que PostgreSQL esté corriendo y que los datos de")
        print("conexión en sync_db.py sean correctos.")
        sys.exit(1)


# ─── Verificar duplicados ─────────────────────────────────────────────────────

def obtener_folios_existentes(cur, folios_a_verificar):
    """
    Consulta PostgreSQL para ver qué folios ya existen en la tabla ventas.
    Retorna un set de tuplas (folio, tipo_documento).
    """
    if not folios_a_verificar:
        return set()

    placeholders = ",".join(["(%s,%s)"] * len(folios_a_verificar))
    valores = [v for par in folios_a_verificar for v in par]

    cur.execute(f"""
        SELECT folio, tipo_documento
        FROM ventas
        WHERE (folio::integer, tipo_documento::text) IN ({placeholders})
    """, valores)

    return {(row[0], str(row[1])) for row in cur.fetchall()}


# ─── Insertar cliente ─────────────────────────────────────────────────────────

def upsert_cliente(cur, cliente):
    """
    Inserta o actualiza un cliente.
    Si el RUT ya existe, actualiza razon_social, direccion y comuna.
    """
    cur.execute("""
        INSERT INTO clientes (rut_cliente, razon_social, direccion, comuna)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (rut_cliente)
        DO UPDATE SET
            razon_social = EXCLUDED.razon_social,
            direccion    = EXCLUDED.direccion,
            comuna       = EXCLUDED.comuna,
            updated_at   = NOW()
    """, (
        cliente["rut_cliente"],
        cliente["razon_social"],
        cliente.get("direccion"),
        cliente.get("comuna"),
    ))


# ─── Migración: columnas de ajuste por NC ────────────────────────────────────

def ensure_columnas_ajuste(cur):
    """
    Agrega monto_neto_ajustado y monto_total_ajustado a ventas si no existen.
    Estas columnas reflejan el neto real de la factura después de aplicar NC.
    """
    cur.execute("""
        ALTER TABLE ventas
            ADD COLUMN IF NOT EXISTS monto_neto_ajustado  numeric,
            ADD COLUMN IF NOT EXISTS monto_total_ajustado numeric
    """)


# ─── Aplicar Nota de Crédito sobre factura referenciada ──────────────────────

def aplicar_nota_credito(cur, venta):
    """
    Cuando se inserta una NC (tipo 61), descuenta su monto de la factura
    original referenciada actualizando monto_neto_ajustado / monto_total_ajustado.
    Si ya existe un ajuste previo (otra NC sobre la misma factura), acumula.
    """
    folio_ref = venta.get("folio_referencia")
    tipo_ref  = venta.get("tipo_documento_referencia")
    if not folio_ref or not tipo_ref:
        return False

    # Los montos de la NC ya son negativos; usamos abs() para restar
    cur.execute("""
        UPDATE ventas
        SET
            monto_neto_ajustado  = COALESCE(monto_neto_ajustado,  monto_neto)  + %s,
            monto_total_ajustado = COALESCE(monto_total_ajustado, monto_total) + %s
        WHERE folio           = %s
          AND tipo_documento  = %s
    """, (
        venta["monto_neto"],    # ya negativo → suma un negativo = resta
        venta["monto_total"],   # ídem
        folio_ref,
        tipo_ref,
    ))
    return cur.rowcount > 0


# ─── Insertar venta ───────────────────────────────────────────────────────────

def insertar_venta(cur, venta):
    """
    Inserta una venta en la tabla ventas.
    """
    cur.execute("""
        INSERT INTO ventas (
            folio,
            tipo_documento,
            fecha,
            rut_cliente,
            monto_neto,
            iva,
            impuesto_adicional,
            monto_total,
            folio_referencia,
            tipo_documento_referencia,
            razon_referencia
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        venta["folio"],
        venta["tipo_documento"],
        venta["fecha"],
        venta["rut_cliente"],
        venta["monto_neto"],
        venta["iva"],
        venta.get("impuesto_adicional", 0),
        venta["monto_total"],
        venta.get("folio_referencia"),
        venta.get("tipo_documento_referencia"),
        venta.get("razon_referencia"),
    ))


# ─── Insertar productos ───────────────────────────────────────────────────────

def insertar_productos(cur, folio, tipo_documento, productos):
    """
    Inserta las líneas de productos asociadas a un folio.
    """
    if not productos:
        return

    rows = [
        (
            folio,
            tipo_documento,
            prod["nombre_producto"],
            prod["cantidad"],
            prod["precio_unitario"],
            prod["total_linea"],
        )
        for prod in productos
    ]

    execute_values(cur, """
        INSERT INTO productos (
            folio,
            tipo_documento,
            nombre_producto,
            cantidad,
            precio_unitario,
            total_linea
        ) VALUES %s
    """, rows)


# ─── Lógica principal ─────────────────────────────────────────────────────────

def sincronizar(changes):
    """
    Sincroniza los documentos del changes.json con PostgreSQL.
    Usa una transacción: si algo falla, revierte todo.
    """
    documentos = changes["documentos"]

    # Preparar set de folios a verificar
    folios_a_verificar = [
        (doc["venta"]["folio"], doc["venta"]["tipo_documento"])
        for doc in documentos
    ]

    conn = conectar()
    print(f"✓ Conectado a PostgreSQL: {DB_CONFIG['dbname']}")
    print()

    try:
        with conn:  # transacción automática
            cur = conn.cursor()

            # ── 0. Migración: asegurar columnas de ajuste ─────────────────────
            ensure_columnas_ajuste(cur)

            # ── 1. Verificar duplicados en BD ─────────────────────────────────
            print("  Verificando folios existentes en BD...")
            existentes = obtener_folios_existentes(cur, folios_a_verificar)

            duplicados = [
                (folio, tipo)
                for folio, tipo in folios_a_verificar
                if (folio, tipo) in existentes
            ]

            if duplicados:
                print()
                print(f"  ⚠️  Folios ya existentes en BD ({len(duplicados)}):")
                for folio, tipo in duplicados:
                    print(f"     Folio {folio} tipo {tipo} → se omitirá")
                print()

            # ── 2. Filtrar solo documentos nuevos ─────────────────────────────
            docs_nuevos = [
                doc for doc in documentos
                if (doc["venta"]["folio"], doc["venta"]["tipo_documento"])
                not in existentes
            ]

            if not docs_nuevos:
                print("  ℹ️  Todos los folios ya existen en la BD.")
                print("  No hay nada nuevo que insertar.")
                return 0, 0, len(duplicados)

            print(f"  Documentos a insertar: {len(docs_nuevos)}")
            print()

            # ── 3. Insertar cada documento ────────────────────────────────────
            insertados = 0
            productos_insertados = 0

            for doc in docs_nuevos:
                venta    = doc["venta"]
                cliente  = doc["cliente"]
                productos = doc["productos"]

                folio = venta["folio"]

                # Upsert cliente (puede que ya exista)
                upsert_cliente(cur, cliente)

                # Insertar venta
                insertar_venta(cur, venta)

                # Insertar líneas de productos
                insertar_productos(
                    cur,
                    folio,
                    venta["tipo_documento"],
                    productos
                )

                # Si es Nota de Crédito, descontar de la factura referenciada
                if str(venta["tipo_documento"]) == "61":
                    ajustada = aplicar_nota_credito(cur, venta)
                    ref_info = f" → descuenta folio {venta.get('folio_referencia')}"
                    if not ajustada:
                        ref_info += " (factura ref. no encontrada en BD)"
                else:
                    ref_info = ""

                insertados += 1
                productos_insertados += len(productos)

                print(f"  ✓ Folio {folio} | "
                      f"{cliente['razon_social']} | "
                      f"${venta['monto_total']:,} | "
                      f"{len(productos)} producto(s){ref_info}")

            # La transacción se hace commit automáticamente al salir del with
            return insertados, productos_insertados, len(duplicados)

    except psycopg2.Error as e:
        print()
        print(f"ERROR en PostgreSQL: {e}")
        print("Se revirtió toda la transacción. No se insertó nada.")
        sys.exit(1)
    finally:
        conn.close()


def main():
    if len(sys.argv) < 2:
        print("Uso: python sync_db.py changes.json")
        sys.exit(1)

    ruta = Path(sys.argv[1])
    if not ruta.exists():
        print(f"ERROR: No se encontró el archivo: {ruta}")
        sys.exit(1)

    print("=" * 60)
    print("ZIGURAT ERP — Sincronización a PostgreSQL")
    print("=" * 60)
    print()

    with open(ruta, "r", encoding="utf-8") as f:
        changes = json.load(f)

    # ── Verificar que validate_changes.py fue ejecutado primero ───────────────
    validated_flag = Path(".changes_validated")
    if not validated_flag.exists():
        print("=" * 60)
        print("❌ BLOQUEADO: changes.json no ha sido validado.")
        print("   Ejecuta validate_changes.py primero:")
        print("   python scripts/validate_changes.py changes.json")
        print("=" * 60)
        sys.exit(1)

    meta = changes.get("metadata", {})
    resumen = changes.get("resumen_financiero", {})

    print(f"  Archivo:       {meta.get('archivo_origen', '?')}")
    print(f"  Documentos:    {meta.get('total_documentos', '?')}")
    print(f"  Monto total:   ${resumen.get('total_monto_total', 0):,}")
    print()

    inicio = datetime.now()
    insertados, productos_insertados, duplicados = sincronizar(changes)
    fin = datetime.now()
    duracion = (fin - inicio).total_seconds()

    print()
    print("=" * 60)
    print("RESULTADO")
    print("=" * 60)
    print(f"  ✓ Facturas insertadas:   {insertados}")
    print(f"  ✓ Productos insertados:  {productos_insertados}")
    if duplicados:
        print(f"  ⚠  Folios omitidos (ya existían): {duplicados}")
    print(f"  ⏱  Tiempo: {duracion:.2f} segundos")
    print()

    if insertados > 0:
        print("✅ Sincronización completada exitosamente")
    else:
        print("ℹ️  No se insertó nada (todo ya existía en la BD)")
    print("=" * 60)

    # Limpiar flag de validación
    if validated_flag.exists():
        validated_flag.unlink()


if __name__ == "__main__":
    main()
