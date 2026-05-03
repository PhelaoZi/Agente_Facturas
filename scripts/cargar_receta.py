#!/usr/bin/env python3
"""
cargar_receta.py - Zigurat ERP
Crea o actualiza una receta y su BOM (receta_detalle) desde un JSON.

Uso:
    python cargar_receta.py recipe.json

Formato del JSON:
{
  "nombre_cerveza": "IPA West Coast Mandarina",
  "litros_lote_estandar": 540,
  "costo_mano_obra_lote": 300000,
  "costo_servicios_lote": 185000,
  "merma_porcentaje": 5.0,
  "insumos": [
    {"nombre": "Malta Pale Ale", "cantidad": 110},
    {"nombre": "Lupulo Citra", "cantidad": 800}
  ]
}

Las cantidades se interpretan en la unidad ya registrada del insumo.
"""

import json
import os
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2.")
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

CAMPOS_REQUERIDOS = ("nombre_cerveza", "litros_lote_estandar", "insumos")


def _validar_payload(data):
    errores = []
    for campo in CAMPOS_REQUERIDOS:
        if campo not in data:
            errores.append(f"Falta campo requerido: '{campo}'")

    if "litros_lote_estandar" in data and data["litros_lote_estandar"] <= 0:
        errores.append("litros_lote_estandar debe ser > 0")

    merma = data.get("merma_porcentaje", 5.0)
    if not (0 <= merma <= 30):
        errores.append("merma_porcentaje debe estar entre 0 y 30")

    insumos = data.get("insumos", [])
    if not insumos:
        errores.append("La receta no tiene insumos")
    suma = sum(i.get("cantidad", 0) for i in insumos)
    if suma <= 0:
        errores.append("La suma de cantidades de insumos debe ser > 0")

    for i, ins in enumerate(insumos):
        if "nombre" not in ins or "cantidad" not in ins:
            errores.append(f"insumos[{i}] requiere 'nombre' y 'cantidad'")
        elif ins["cantidad"] <= 0:
            errores.append(f"insumos[{i}]: cantidad debe ser > 0")

    return errores


def main():
    if len(sys.argv) != 2:
        print("Uso: python cargar_receta.py recipe.json")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    if not json_path.exists():
        print(f"ERROR: Archivo no encontrado: {json_path}")
        sys.exit(1)

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    errores = _validar_payload(data)
    if errores:
        print("ERROR: payload inválido:")
        for e in errores:
            print(f"  - {e}")
        sys.exit(1)

    nombre_cerveza        = data["nombre_cerveza"].strip()
    litros_lote           = int(data["litros_lote_estandar"])
    costo_mano_obra       = float(data.get("costo_mano_obra_lote", 300000))
    costo_servicios       = float(data.get("costo_servicios_lote", 185000))
    merma                 = float(data.get("merma_porcentaje", 5.0))
    insumos               = data["insumos"]

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            cur = conn.cursor()

            # Mapear nombres de insumos a IDs y validar que existan
            nombres = [i["nombre"].strip() for i in insumos]
            cur.execute(
                "SELECT id, nombre FROM maestro_insumos WHERE nombre = ANY(%s)",
                (nombres,)
            )
            mapa = {row[1]: row[0] for row in cur.fetchall()}
            faltantes = [n for n in nombres if n not in mapa]
            if faltantes:
                print("ERROR: insumos no existen en maestro_insumos:")
                for n in faltantes:
                    print(f"  - {n}")
                print("Crea primero con: /actualizar-precio-insumo")
                sys.exit(1)

            # Upsert receta
            cur.execute(
                "SELECT id FROM recetas WHERE nombre_cerveza = %s",
                (nombre_cerveza,)
            )
            row = cur.fetchone()
            if row:
                receta_id = row[0]
                cur.execute("""
                    UPDATE recetas
                    SET litros_lote_estandar = %s,
                        costo_mano_obra_lote = %s,
                        costo_servicios_lote = %s,
                        merma_porcentaje     = %s
                    WHERE id = %s
                """, (litros_lote, costo_mano_obra, costo_servicios, merma, receta_id))
                cur.execute("DELETE FROM receta_detalle WHERE receta_id = %s", (receta_id,))
                accion = "actualizada"
            else:
                cur.execute("""
                    INSERT INTO recetas
                        (nombre_cerveza, litros_lote_estandar,
                         costo_mano_obra_lote, costo_servicios_lote, merma_porcentaje)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                """, (nombre_cerveza, litros_lote, costo_mano_obra, costo_servicios, merma))
                receta_id = cur.fetchone()[0]
                accion = "creada"

            # Insertar receta_detalle
            for ins in insumos:
                cur.execute("""
                    INSERT INTO receta_detalle (receta_id, insumo_id, cantidad_requerida)
                    VALUES (%s, %s, %s)
                """, (receta_id, mapa[ins["nombre"].strip()], ins["cantidad"]))

            # Costo total del lote (informativo)
            cur.execute("""
                SELECT SUM(rd.cantidad_requerida * mi.precio_neto_unitario)
                FROM receta_detalle rd
                JOIN maestro_insumos mi ON mi.id = rd.insumo_id
                WHERE rd.receta_id = %s
            """, (receta_id,))
            costo_insumos = cur.fetchone()[0] or 0
    finally:
        conn.close()

    costo_lote_total = float(costo_insumos) + costo_mano_obra + costo_servicios
    litros_envasables = litros_lote * (1 - merma / 100)
    costo_por_litro = costo_lote_total / litros_envasables if litros_envasables > 0 else 0

    fmt = lambda v: "$" + "{:,.0f}".format(v).replace(",", ".")
    print(f"Receta {accion} (id={receta_id})")
    print(f"   Cerveza:           {nombre_cerveza}")
    print(f"   Litros lote:       {litros_lote} L (envasables: {litros_envasables:.1f} L con {merma}% merma)")
    print(f"   Insumos:           {len(insumos)}")
    print(f"   Costo insumos:     {fmt(float(costo_insumos))}")
    print(f"   Mano de obra:      {fmt(costo_mano_obra)}")
    print(f"   Servicios:         {fmt(costo_servicios)}")
    print(f"   Costo lote total:  {fmt(costo_lote_total)}")
    print(f"   Costo por litro:   {fmt(costo_por_litro)}")


if __name__ == "__main__":
    main()
