#!/usr/bin/env python3
"""
wiki_snapshot.py - Zigurat ERP
Genera snapshots JSON por cliente en raw/clientes/.

Los snapshots son la capa "raw" del patrón LLM Wiki (Karpathy): fuente de
verdad histórica inmutable desde el punto de vista del usuario (se
sobrescriben desde código pero NUNCA se editan a mano). Commiteables a git
para obtener `git diff` del estado del negocio entre ingestas.

Uso:
    python wiki_snapshot.py --todos              # snapshots de todos los clientes
    python wiki_snapshot.py --ruts 12345678-9    # uno o más RUTs separados por coma

Nota: wiki_update.py también actualiza el snapshot correspondiente cada vez
que regenera una ficha. Este script existe para refresh masivo independiente
del pipeline de fichas (ej: antes de una auditoría).
"""

import argparse
import sys

from _console import force_utf8

force_utf8()

# Reusa utilidades del módulo principal (conexión, queries, serialización)
from wiki_update import (
    conectar,
    obtener_datos_cliente,
    obtener_ruts_todos,
    guardar_snapshot,
    fmt_monto,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Genera snapshots JSON inmutables de clientes en raw/clientes/"
    )
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument(
        "--todos", action="store_true",
        help="Generar snapshot de todos los clientes"
    )
    grupo.add_argument(
        "--ruts", type=str,
        help="RUTs separados por coma (ej: 12345678-9,98765432-1)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    print("=" * 60)
    print("  WIKI SNAPSHOT — Zigurat ERP")
    print("=" * 60)

    conn = conectar()
    cur = conn.cursor()
    print("[OK] Conectado a PostgreSQL")

    if args.todos:
        ruts = obtener_ruts_todos(cur)
    else:
        ruts = [r.strip() for r in args.ruts.split(",")]

    print(f"  Clientes a snapshot: {len(ruts)}")
    print("-" * 60)

    guardados = 0
    errores = 0

    for rut in ruts:
        datos = obtener_datos_cliente(cur, rut)
        if datos is None:
            print(f"  [{rut}] no encontrado en tabla clientes")
            errores += 1
            continue
        guardar_snapshot(datos)
        guardados += 1
        print(
            f"  ✓ {datos['razon_social']} | "
            f"{datos['facturas_emitidas']} fact. | "
            f"Total: {fmt_monto(datos['total_vendido'])}"
        )

    print("-" * 60)
    print(f"  Snapshots guardados: {guardados}")
    if errores:
        print(f"  Errores: {errores}")
    print()

    cur.close()
    conn.close()
