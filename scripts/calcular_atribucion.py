#!/usr/bin/env python3
"""
calcular_atribucion.py — Zigurat ERP
Calcula el ingreso neto atribuido a cada cerveza y lo materializa en
`atribucion_ingreso` / `atribucion_documento`.

Uso:
    python scripts/calcular_atribucion.py            # recalcula todo
    python scripts/calcular_atribucion.py --simular  # solo informa, no escribe

Es idempotente y recalculable: borra la versión anterior y la vuelve a construir
entera desde `ventas` y `productos`, que NO se tocan. El "rollback" es volver a
correrlo.

La regla que lo ordena, la misma del motor pero un nivel más arriba: **si no
cuadra, no se publica**. Un documento a medias no se publica, y un lote que no
cuadra contra `ventas` tampoco.
"""
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    print("ERROR: Falta psycopg2. Instala con: pip install psycopg2-binary")
    sys.exit(1)

from app.negocio import atribucion_ingreso as ai

try:
    from _console import force_utf8
except ImportError:
    from scripts._console import force_utf8

force_utf8()

# Tolerancia de la cuadratura del lote: un peso por documento, por el redondeo
# de cada reparto. Más que eso es un error del motor, no aritmética.
TOLERANCIA_POR_DOCUMENTO = 1


def _load_env():
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def conectar():
    _load_env()
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", 5432)),
        dbname=os.environ.get("DB_NAME", "dte_facturas_chile"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
    )


def leer_documentos(cur):
    """Arma los documentos con sus líneas desde `ventas` y `productos`."""
    cur.execute("""
        SELECT tipo_documento, folio, fecha, monto_neto,
               COALESCE(impuesto_adicional, 0)
        FROM ventas
        ORDER BY fecha, folio
    """)
    cabeceras = cur.fetchall()

    cur.execute("""
        SELECT tipo_documento, folio, id, nombre_producto, cantidad, total_linea
        FROM productos
        ORDER BY id
    """)
    lineas = defaultdict(list)
    for tipo, folio, id_linea, nombre, cantidad, total in cur.fetchall():
        lineas[(tipo, folio)].append({
            "id": id_linea,
            "nombre_producto": nombre,
            "cantidad": float(cantidad or 1),
            "total_linea": float(total or 0),
        })

    return [
        {
            "tipo_documento": tipo,
            "folio": folio,
            "fecha": fecha,
            "monto_neto": float(neto or 0),
            "impuesto_adicional": float(ila or 0),
            "lineas": lineas.get((tipo, folio), []),
        }
        for tipo, folio, fecha, neto, ila in cabeceras
    ]


def calcular(documentos):
    """Corre el motor sobre todos los documentos y arma el lote a escribir.

    No toca la base. Devuelve las filas listas más el informe de cobertura y si
    el lote cuadra contra el neto de `ventas`.
    """
    filas_documento, filas_linea = [], []
    neto_total = atribuido = pass_through = sin_atribuir = 0.0
    documentos_atribuidos = 0

    for documento in documentos:
        resultado = ai.atribuir(documento)

        neto_total += documento["monto_neto"]
        atribuido += resultado["monto_atribuido"]
        pass_through += resultado["monto_pass_through"]
        sin_atribuir += resultado["monto_sin_atribuir"]
        documentos_atribuidos += resultado["estado"] == "atribuido"

        filas_documento.append({
            "tipo_documento": documento["tipo_documento"],
            "folio": documento["folio"],
            "fecha": documento["fecha"],
            "signo_evento": resultado["signo_evento"],
            "neto_documento": documento["monto_neto"],
            "monto_atribuido": resultado["monto_atribuido"],
            "monto_pass_through": resultado["monto_pass_through"],
            "monto_sin_atribuir": resultado["monto_sin_atribuir"],
            "estado": resultado["estado"],
            "motivo": resultado["motivo"],
            "version_algoritmo": resultado["version_algoritmo"],
        })
        for linea in resultado["lineas"]:
            filas_linea.append({
                "tipo_documento": documento["tipo_documento"],
                "folio": documento["folio"],
                **linea,
            })

    descuadre = abs(atribuido + pass_through + sin_atribuir - neto_total)

    return {
        "documentos": filas_documento,
        "lineas": filas_linea,
        "neto_total": neto_total,
        "monto_atribuido": atribuido,
        "monto_pass_through": pass_through,
        "monto_sin_atribuir": sin_atribuir,
        "documentos_totales": len(documentos),
        "documentos_atribuidos": documentos_atribuidos,
        "cuadra": descuadre <= TOLERANCIA_POR_DOCUMENTO * max(len(documentos), 1),
        "descuadre": descuadre,
    }


def materializar(cur, lote):
    """Reemplaza la capa derivada por la del lote. Levanta si el lote no cuadra.

    Borra antes de insertar: la capa se recalcula entera, y sin el borrado cada
    corrida duplicaría el ingreso de cada cerveza.
    """
    if not lote["cuadra"]:
        raise ValueError(
            f"El lote no cuadra contra `ventas` (descuadre "
            f"${lote.get('descuadre', 0):,.0f}). No se escribe nada.")

    cur.execute("DELETE FROM atribucion_ingreso")
    cur.execute("DELETE FROM atribucion_documento")

    if lote["documentos"]:
        execute_values(cur, """
            INSERT INTO atribucion_documento (
                tipo_documento, folio, fecha, signo_evento, neto_documento,
                monto_atribuido, monto_pass_through, monto_sin_atribuir,
                estado, motivo, version_algoritmo
            ) VALUES %s
        """, [(d["tipo_documento"], d["folio"], d["fecha"], d["signo_evento"],
               d["neto_documento"], d["monto_atribuido"], d["monto_pass_through"],
               d["monto_sin_atribuir"], d["estado"], d["motivo"],
               d["version_algoritmo"]) for d in lote["documentos"]])

    if lote["lineas"]:
        execute_values(cur, """
            INSERT INTO atribucion_ingreso (
                tipo_documento, folio, linea_id, cerveza, formato, litros,
                unidades, monto_linea_evidencia, logistica_atribuida,
                ingreso_neto_atribuido, fuente, metodo, calidad,
                version_algoritmo
            ) VALUES %s
        """, [(l["tipo_documento"], l["folio"], l["linea_id"], l["cerveza"],
               l["formato"], l["litros"], l["unidades"],
               l["monto_linea_evidencia"], l["logistica_atribuida"],
               l["ingreso_neto_atribuido"], l["fuente"], l["metodo"],
               l["calidad"], l["version_algoritmo"]) for l in lote["lineas"]])

    return {"documentos": len(lote["documentos"]), "lineas": len(lote["lineas"])}


def informe(lote):
    """Cobertura en documentos Y en monto: una del 97% en documentos puede ser
    del 60% en plata."""
    porcentaje = (100 * lote["monto_atribuido"] / lote["neto_total"]
                  if lote["neto_total"] else 0)
    motivos = defaultdict(lambda: [0, 0.0])
    for d in lote["documentos"]:
        if d["motivo"]:
            motivos[d["motivo"]][0] += 1
            motivos[d["motivo"]][1] += abs(d["neto_documento"])

    lineas = [
        "",
        f"  Documentos:        {lote['documentos_atribuidos']} de "
        f"{lote['documentos_totales']} atribuidos",
        f"  Neto de ventas:    ${lote['neto_total']:>14,.0f}",
        f"  Atribuido:         ${lote['monto_atribuido']:>14,.0f}  ({porcentaje:.1f}%)",
        f"  Pass-through:      ${lote['monto_pass_through']:>14,.0f}  (envase PET, CO2)",
        f"  Sin atribuir:      ${lote['monto_sin_atribuir']:>14,.0f}",
        f"  Cuadratura:        {'OK' if lote['cuadra'] else 'DESCUADRE'}"
        f" (diferencia ${lote['descuadre']:,.0f})",
    ]
    if motivos:
        lineas.append("")
        lineas.append("  Sin atribuir, por motivo:")
        for motivo, (n, monto) in sorted(motivos.items(), key=lambda x: -x[1][1]):
            lineas.append(f"    {motivo:<26} {n:>3} docs   ${monto:>12,.0f}")
    return "\n".join(lineas)


def main():
    simular = "--simular" in sys.argv

    print("=" * 62)
    print("ZIGURAT ERP — Atribución de ingreso por producto")
    print("=" * 62)

    conn = conectar()
    try:
        with conn.cursor() as cur:
            documentos = leer_documentos(cur)
        lote = calcular(documentos)
        print(informe(lote))

        if simular:
            print("\n  --simular: no se escribió nada.")
            return 0

        with conn, conn.cursor() as cur:
            escrito = materializar(cur, lote)
        print(f"\n  Materializado: {escrito['documentos']} documentos, "
              f"{escrito['lineas']} líneas de atribución.")
    except ValueError as e:
        print(f"\n  ERROR: {e}")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
