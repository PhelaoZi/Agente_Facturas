#!/usr/bin/env python3
"""
migrate_fusionar_cara50_ruby.py — Zigurat ERP

Fusiona "Malta Cara 50" y "Malta Cara Ruby" en un solo insumo.

Son la misma malta caramelo: cada proveedor la nombra distinto y el productor
compra una u otra según el stock (confirmado 2026-07-26). Tenerlas separadas
partía el costeo en dos: comprar la Cara 50 en MACC no movía el precio de las
recetas que apuntaban a Cara Ruby, aunque en la práctica fuera el mismo saco.

Deja un insumo llamado "Malta Cara 50 / Ruby" con el precio de la compra más
reciente de las dos, y reapunta ahí las recetas. El insumo sobrante queda
inactivo, no borrado: conserva el historial.

Idempotente. Uso: python scripts/migrate_fusionar_cara50_ruby.py
"""
import os
import sys
from pathlib import Path

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

NOMBRE_FINAL = "Malta Cara 50 / Ruby"
NOMBRE_DESTINO = "Malta Cara 50"
NOMBRE_SOBRANTE = "Malta Cara Ruby"


def _buscar(cur, nombre):
    """Retorna (id, precio, actualizado_el, activo) del insumo, o None."""
    cur.execute(
        """SELECT id, precio_neto_unitario, actualizado_el, activo
           FROM maestro_insumos WHERE nombre = %s""",
        (nombre,))
    return cur.fetchone()


def _ya_fusionado(cur, sobrante_id):
    """True si el insumo sobrante ya quedó inactivo y sin referencias."""
    for tabla, _, _ in TABLAS_DETALLE:
        cur.execute(f"SELECT 1 FROM {tabla} WHERE insumo_id = %s LIMIT 1", (sobrante_id,))
        if cur.fetchone():
            return False
    return True


# Tablas que referencian un insumo, con su columna de agrupación y de cantidad
# (no son iguales: receta_detalle usa cantidad_requerida y sku_envasado cantidad).
TABLAS_DETALLE = (
    ("receta_detalle", "receta_id", "cantidad_requerida"),
    ("sku_envasado",   "sku_id",    "cantidad"),
)


def _mover_referencias(cur, tabla, clave, cantidad, origen, destino):
    """Reapunta una tabla de detalle del insumo sobrante al que queda.

    Si una misma receta/SKU usaba los dos insumos, las cantidades se suman: al
    ser el mismo insumo real, lo que corresponde es el total. Los nombres de
    tabla y columna salen de TABLAS_DETALLE, no de datos externos.
    """
    cur.execute(
        f"""
        UPDATE {tabla} d SET {cantidad} = d.{cantidad} + x.{cantidad}
        FROM {tabla} x
        WHERE d.{clave} = x.{clave} AND d.insumo_id = %s AND x.insumo_id = %s
        """,
        (destino, origen))
    sumadas = cur.rowcount

    cur.execute(
        f"""DELETE FROM {tabla} WHERE insumo_id = %s AND {clave} IN
            (SELECT {clave} FROM {tabla} WHERE insumo_id = %s)""",
        (origen, destino))

    cur.execute(f"UPDATE {tabla} SET insumo_id = %s WHERE insumo_id = %s", (destino, origen))
    return cur.rowcount, sumadas


def main():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            with conn.cursor() as cur:
                destino = _buscar(cur, NOMBRE_FINAL) or _buscar(cur, NOMBRE_DESTINO)
                if destino is None:
                    print(f"ERROR: no existe '{NOMBRE_DESTINO}' ni '{NOMBRE_FINAL}'.")
                    sys.exit(1)
                destino_id, destino_precio, destino_fecha, _ = destino

                sobrante = _buscar(cur, NOMBRE_SOBRANTE)
                if sobrante is None:
                    print(f"Nada que fusionar: '{NOMBRE_SOBRANTE}' ya no existe.")
                elif not sobrante[3] and _ya_fusionado(cur, sobrante[0]):
                    print(f"Ya estaba fusionado: '{NOMBRE_SOBRANTE}' inactivo y sin referencias.")
                else:
                    sobrante_id, sobrante_precio, sobrante_fecha, _ = sobrante

                    for tabla, clave, cantidad in TABLAS_DETALLE:
                        movidas, sumadas = _mover_referencias(
                            cur, tabla, clave, cantidad, sobrante_id, destino_id)
                        if movidas or sumadas:
                            print(f"   {tabla}: {movidas} reapuntada(s), {sumadas} sumada(s)")

                    # Gana el precio de la compra más reciente de las dos.
                    if sobrante_fecha and destino_fecha and sobrante_fecha > destino_fecha:
                        cur.execute(
                            """UPDATE maestro_insumos
                               SET precio_neto_unitario = %s, actualizado_el = %s WHERE id = %s""",
                            (sobrante_precio, sobrante_fecha, destino_id))
                        print(f"   Precio: ${sobrante_precio}/kg (venía de '{NOMBRE_SOBRANTE}', "
                              f"más reciente)")
                    else:
                        print(f"   Precio: ${destino_precio}/kg (el de '{NOMBRE_DESTINO}' "
                              f"es el más reciente)")

                    cur.execute("UPDATE maestro_insumos SET activo = FALSE WHERE id = %s",
                                (sobrante_id,))
                    print(f"   '{NOMBRE_SOBRANTE}' (id={sobrante_id}) queda inactivo.")

                cur.execute("UPDATE maestro_insumos SET nombre = %s WHERE id = %s",
                            (NOMBRE_FINAL, destino_id))
                print(f"OK — insumo único '{NOMBRE_FINAL}' (id={destino_id}).")

    except psycopg2.Error as e:
        print(f"ERROR DB: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
