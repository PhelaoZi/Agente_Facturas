#!/usr/bin/env python3
"""
migrate_recompute_dias_pago.py - Zigurat ERP

Recomputa `ventas.dias_pago` en facturas que estan pagadas
(`fecha_pago IS NOT NULL`) pero quedaron con `dias_pago IS NULL`.

CONTEXTO
--------
`importar_pagos_excel.py` no pudo calcular los dias cuando faltaba la columna
"FECHA EMISION" en el Excel de origen. Pero `ventas.fecha` (fecha de emision)
SIEMPRE existe en la BD, asi que el dato se puede reconstruir:

    dias_pago = fecha_pago - fecha          (ambas son DATE -> dias enteros)

Importa porque `flujo_caja.py` proyecta usando el promedio de `dias_pago` por
cliente; con NULLs la proyeccion queda sesgada.

Es idempotente: el WHERE exige `dias_pago IS NULL`, asi que correrlo de nuevo
no toca nada.

USO
---
    python scripts/migrate_recompute_dias_pago.py --dry-run   # previsualizar
    python scripts/migrate_recompute_dias_pago.py             # aplicar
    python scripts/migrate_recompute_dias_pago.py --revert logs/recompute_dias_pago_<ts>.json
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: Falta psycopg2.")
    sys.exit(1)


def _load_env():
    """Carga variables desde .env (mismo patron que el resto de scripts)."""
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

LOGS_DIR = Path(__file__).parent.parent / "logs"

# Facturas pagadas con dias_pago sin calcular.
SELECT_AFECTADAS = """
    SELECT folio,
           fecha                AS fecha_emision,
           fecha_pago,
           (fecha_pago - fecha) AS dias_nuevo,
           rut_cliente
    FROM ventas
    WHERE fecha_pago IS NOT NULL
      AND dias_pago IS NULL
      AND tipo_documento != 61
    ORDER BY folio
"""

UPDATE_DIAS = """
    UPDATE ventas
    SET dias_pago = (fecha_pago - fecha)
    WHERE fecha_pago IS NOT NULL
      AND dias_pago IS NULL
      AND tipo_documento != 61
"""

# Anomalias preexistentes (no las corrige este script, solo las reporta).
SELECT_NEGATIVOS = """
    SELECT folio, rut_cliente, fecha, fecha_pago, dias_pago
    FROM ventas
    WHERE dias_pago < 0
      AND tipo_documento != 61
    ORDER BY dias_pago
"""


def obtener_afectadas(cur):
    cur.execute(SELECT_AFECTADAS)
    return cur.fetchall()


def guardar_snapshot(afectadas):
    """Guarda JSON reversible con los folios tocados. Retorna la ruta."""
    LOGS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = LOGS_DIR / f"recompute_dias_pago_{ts}.json"
    payload = [{"folio": r["folio"], "dias_nuevo": r["dias_nuevo"]} for r in afectadas]
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return ruta


def revertir(cur, ruta_snapshot):
    """Deshace: vuelve a NULL el dias_pago de los folios del snapshot."""
    with open(ruta_snapshot, encoding="utf-8") as f:
        folios = [item["folio"] for item in json.load(f)]
    cur.execute(
        """
        UPDATE ventas
        SET dias_pago = NULL
        WHERE folio = ANY(%s)
          AND tipo_documento != 61
        """,
        (folios,),
    )
    return len(folios)


def mostrar_preview(afectadas):
    print(f"  Facturas a recomputar: {len(afectadas)}")
    if not afectadas:
        return
    negativos = [r for r in afectadas if r["dias_nuevo"] < 0]
    print(f"  De esas, producirian dias negativos: {len(negativos)}")
    print()
    print(f"  {'Folio':>7}  {'Emision':<12}  {'Fecha pago':<12}  {'Dias':>5}  RUT")
    print(f"  {'-'*7}  {'-'*12}  {'-'*12}  {'-'*5}  {'-'*12}")
    for r in afectadas[:15]:
        print(f"  {r['folio']:>7}  {str(r['fecha_emision']):<12}  "
              f"{str(r['fecha_pago']):<12}  {r['dias_nuevo']:>5}  {r['rut_cliente']}")
    if len(afectadas) > 15:
        print(f"  ... y {len(afectadas) - 15} mas")


def reportar_negativos(cur):
    """Reporta dias_pago negativos preexistentes (datos sospechosos)."""
    cur.execute(SELECT_NEGATIVOS)
    negativos = cur.fetchall()
    if not negativos:
        return
    print()
    print(f"  [!] {len(negativos)} factura(s) con dias_pago NEGATIVO (revisar a mano,")
    print(f"      probable error de fecha en emision o pago):")
    for n in negativos:
        print(f"      folio {n['folio']}  {n['rut_cliente']}  "
              f"emision {n['fecha']}  pago {n['fecha_pago']}  ({n['dias_pago']} dias)")


def main():
    dry_run = "--dry-run" in sys.argv
    revert_idx = sys.argv.index("--revert") if "--revert" in sys.argv else None

    print("=" * 70)
    print("ZIGURAT ERP - Recomputar dias_pago en facturas pagadas")
    print("=" * 70)

    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        if revert_idx is not None:
            ruta = sys.argv[revert_idx + 1]
            with conn:
                with conn.cursor() as cur:
                    n = revertir(cur, ruta)
            print(f"  Revertidas {n} facturas a dias_pago = NULL desde {ruta}")
            return

        with conn.cursor() as cur:
            afectadas = obtener_afectadas(cur)

        mostrar_preview(afectadas)

        if not afectadas:
            print("\n  Nada que recomputar. La BD ya esta completa.")
            with conn.cursor() as cur:
                reportar_negativos(cur)
            return

        if dry_run:
            print("\n  [DRY-RUN] No se escribio nada. Quita --dry-run para aplicar.")
            with conn.cursor() as cur:
                reportar_negativos(cur)
            return

        ruta_snapshot = guardar_snapshot(afectadas)
        print(f"\n  Snapshot reversible guardado en: {ruta_snapshot}")

        with conn:
            with conn.cursor() as cur:
                cur.execute(UPDATE_DIAS)
                tocadas = cur.rowcount

        # Verificacion: no deben quedar pagadas con dias_pago NULL
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) AS n FROM ventas
                WHERE fecha_pago IS NOT NULL AND dias_pago IS NULL AND tipo_documento != 61
            """)
            restantes = cur.fetchone()["n"]
            print(f"\n  Facturas recomputadas: {tocadas}")
            print(f"  Pagadas con dias_pago aun NULL: {restantes}")
            if restantes == 0:
                print("  [OK] Todas las facturas pagadas tienen dias_pago.")
            reportar_negativos(cur)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
