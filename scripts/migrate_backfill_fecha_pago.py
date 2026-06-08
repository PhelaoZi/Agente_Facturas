#!/usr/bin/env python3
"""
migrate_backfill_fecha_pago.py - Zigurat ERP

Corrige la inconsistencia historica entre `ventas.fecha_pago` y la tabla
`conciliaciones`.

CONTEXTO DEL PROBLEMA
---------------------
Una carga masiva (timestamp uniforme 2026-01-25 03:51:12) inserto ~160
conciliaciones bancarias SIN escribir `ventas.fecha_pago`. Esto dejaba a esas
facturas como "pendientes" segun el campo `fecha_pago`, aunque tenian una
transferencia bancaria real ya vinculada. Distintas consultas (una mirando
`fecha_pago`, otra mirando `conciliaciones`) entregaban totales de deuda
diferentes para el mismo cliente.

FUENTE DE VERDAD
----------------
`ventas.fecha_pago IS NOT NULL`  <=>  factura pagada.
`conciliaciones` es solo evidencia bancaria de respaldo (puede estar
incompleta: los pagos importados desde Excel no generan conciliacion).

QUE HACE ESTE SCRIPT
--------------------
Para cada factura (tipo != 61) que tenga al menos una conciliacion pero
`fecha_pago IS NULL`, rellena:
  - fecha_pago = fecha del movimiento bancario vinculado (la mas antigua si
    hubiera varias; se verifico que no hay ambiguedad)
  - dias_pago  = fecha_pago - fecha_emision (dias enteros)

Es idempotente: tras correrlo una vez, no quedan filas que cumplan la
condicion, asi que volver a ejecutarlo no hace nada.

USO
---
    python scripts/migrate_backfill_fecha_pago.py --dry-run   # previsualizar
    python scripts/migrate_backfill_fecha_pago.py             # aplicar
    python scripts/migrate_backfill_fecha_pago.py --revert logs/backfill_fecha_pago_<ts>.json
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

# Facturas con conciliacion bancaria pero sin fecha_pago. Toma la fecha de
# movimiento mas antigua por folio (MIN) para ser deterministico aunque
# existieran multiples conciliaciones.
SELECT_AFECTADAS = """
    SELECT v.folio,
           v.fecha                       AS fecha_emision,
           MIN(m.fecha)::date            AS fecha_pago_nueva,
           (MIN(m.fecha)::date - v.fecha) AS dias_pago_nuevo,
           v.rut_cliente
    FROM ventas v
    JOIN conciliaciones c   ON c.folio_venta = v.folio
    JOIN movimientos_banco m ON m.id = c.movimiento_banco_id
    WHERE v.tipo_documento != '61'
      AND v.fecha_pago IS NULL
    GROUP BY v.folio, v.fecha, v.rut_cliente
    ORDER BY v.folio
"""

# Invariante de consistencia: tras el backfill debe dar 0.
COUNT_INCONSISTENTES = """
    SELECT COUNT(*) AS n
    FROM ventas v
    WHERE v.tipo_documento != '61'
      AND v.fecha_pago IS NULL
      AND EXISTS (SELECT 1 FROM conciliaciones c WHERE c.folio_venta = v.folio)
"""


def obtener_afectadas(cur):
    """Retorna la lista de facturas a corregir con sus valores nuevos."""
    cur.execute(SELECT_AFECTADAS)
    return cur.fetchall()


def guardar_snapshot(afectadas):
    """Guarda un JSON reversible con los folios tocados. Retorna la ruta."""
    LOGS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = LOGS_DIR / f"backfill_fecha_pago_{ts}.json"
    payload = [
        {
            "folio": r["folio"],
            "fecha_pago_nueva": str(r["fecha_pago_nueva"]),
            "dias_pago_nuevo": r["dias_pago_nuevo"],
        }
        for r in afectadas
    ]
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return ruta


def aplicar_backfill(cur, afectadas):
    """Escribe fecha_pago + dias_pago en las facturas afectadas."""
    for r in afectadas:
        cur.execute(
            """
            UPDATE ventas
            SET fecha_pago = %s,
                dias_pago  = %s
            WHERE folio          = %s
              AND tipo_documento != '61'
              AND fecha_pago IS NULL
            """,
            (r["fecha_pago_nueva"], r["dias_pago_nuevo"], r["folio"]),
        )


def revertir(cur, ruta_snapshot):
    """Deshace un backfill: vuelve a NULL los folios del snapshot."""
    with open(ruta_snapshot, encoding="utf-8") as f:
        folios = [item["folio"] for item in json.load(f)]
    cur.execute(
        """
        UPDATE ventas
        SET fecha_pago = NULL,
            dias_pago  = NULL
        WHERE folio = ANY(%s)
          AND tipo_documento != '61'
        """,
        (folios,),
    )
    return len(folios)


def mostrar_preview(afectadas):
    """Imprime las primeras filas a modo de previsualizacion."""
    print(f"  Facturas a corregir: {len(afectadas)}")
    if not afectadas:
        return
    print()
    print(f"  {'Folio':>7}  {'Emision':<12}  {'Fecha pago':<12}  {'Dias':>5}  RUT")
    print(f"  {'-'*7}  {'-'*12}  {'-'*12}  {'-'*5}  {'-'*12}")
    for r in afectadas[:15]:
        print(f"  {r['folio']:>7}  {str(r['fecha_emision']):<12}  "
              f"{str(r['fecha_pago_nueva']):<12}  {r['dias_pago_nuevo']:>5}  {r['rut_cliente']}")
    if len(afectadas) > 15:
        print(f"  ... y {len(afectadas) - 15} mas")


def main():
    dry_run = "--dry-run" in sys.argv
    revert_idx = sys.argv.index("--revert") if "--revert" in sys.argv else None

    print("=" * 70)
    print("ZIGURAT ERP - Backfill fecha_pago desde conciliaciones")
    print("=" * 70)

    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        # Modo revertir
        if revert_idx is not None:
            ruta = sys.argv[revert_idx + 1]
            with conn:
                with conn.cursor() as cur:
                    n = revertir(cur, ruta)
            print(f"  Revertidas {n} facturas a fecha_pago = NULL desde {ruta}")
            return

        with conn.cursor() as cur:
            afectadas = obtener_afectadas(cur)

        mostrar_preview(afectadas)

        if not afectadas:
            print("\n  Nada que corregir. La BD ya esta consistente.")
            return

        if dry_run:
            print("\n  [DRY-RUN] No se escribio nada. Quita --dry-run para aplicar.")
            return

        ruta_snapshot = guardar_snapshot(afectadas)
        print(f"\n  Snapshot reversible guardado en: {ruta_snapshot}")

        with conn:
            with conn.cursor() as cur:
                aplicar_backfill(cur, afectadas)

        # Verificacion de la invariante
        with conn.cursor() as cur:
            cur.execute(COUNT_INCONSISTENTES)
            restantes = cur.fetchone()["n"]

        print(f"\n  Facturas corregidas: {len(afectadas)}")
        print(f"  Inconsistencias restantes (conciliacion sin fecha_pago): {restantes}")
        if restantes == 0:
            print("  [OK] Invariante satisfecha: fecha_pago es ahora la fuente de verdad unica.")
        else:
            print("  [!] Aun quedan inconsistencias. Revisar manualmente.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
