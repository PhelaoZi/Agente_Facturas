#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_paridad_nube.py - Zigurat ERP
Criterio de aceptacion de la spec Zigurat Movil: las cifras de la nube deben
coincidir EXACTAMENTE con la BD local al momento del ultimo sync.

Compara: por cobrar (v_pendientes vs query canonica local), ventas del mes
(v_ventas_reales vs query canonica local) y numero de pendientes.

Script de aceptacion manual (necesita red y el stack desplegado): NO es parte
de `python -m pytest -q`. Correr DESPUES de `python scripts/sync_nube.py`.

Uso:
    python scripts/sync_nube.py && python scripts/test_paridad_nube.py
    python scripts/test_paridad_nube.py --solo-token   # imprime un JWT y sale
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from sync_nube import _load_env, conectar_local  # noqa: E402  (reutiliza patron)

_load_env()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def token_jwt() -> str:
    """JWT HS256 minimo firmado con el secret del proyecto (1 hora)."""
    secreto = os.environ.get("INSFORGE_JWT_SECRET")
    if not secreto:
        raise RuntimeError("Falta INSFORGE_JWT_SECRET en el .env")
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    ahora = int(time.time())
    payload = _b64url(json.dumps(
        {"sub": "paridad-local", "iat": ahora, "exp": ahora + 3600}
    ).encode())
    firma = _b64url(hmac.new(secreto.encode(), f"{header}.{payload}".encode(),
                             hashlib.sha256).digest())
    return f"{header}.{payload}.{firma}"


def llamar_api(endpoint: str, token: str) -> dict:
    base = os.environ.get("INSFORGE_FUNCTIONS_URL")
    if not base:
        raise RuntimeError("Falta INSFORGE_FUNCTIONS_URL en el .env")
    req = urllib.request.Request(
        f"{base.rstrip('/')}/{endpoint}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def cifras_locales() -> dict:
    """Queries canonicas del CLAUDE.md directo contra la BD local."""
    conn = conectar_local()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(SUM(COALESCE(v.monto_total_ajustado, v.monto_total)), 0),
                       COUNT(*)
                FROM ventas v
                JOIN clientes c ON c.rut_cliente = v.rut_cliente
                WHERE v.tipo_documento != '61' AND v.fecha_pago IS NULL
                  AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
                  AND COALESCE(c.estado, '') <> 'incobrable'
            """)
            por_cobrar, n_pendientes = cur.fetchone()
            cur.execute("""
                SELECT COALESCE(SUM(COALESCE(monto_total_ajustado, monto_total)), 0)
                FROM ventas
                WHERE tipo_documento != '61'
                  AND date_trunc('month', fecha) = date_trunc('month', CURRENT_DATE)
            """)
            (ventas_mes,) = cur.fetchone()
    finally:
        conn.close()
    return {"por_cobrar": float(por_cobrar), "n_pendientes": int(n_pendientes),
            "ventas_mes": float(ventas_mes)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--solo-token", action="store_true")
    args = parser.parse_args()
    token = token_jwt()
    if args.solo_token:
        print(token)
        return 0

    local = cifras_locales()
    kpis = llamar_api("kpis", token)
    errores = []
    for clave in ("por_cobrar", "n_pendientes", "ventas_mes"):
        nube = float(kpis[clave])
        if abs(nube - local[clave]) > 0.005:  # igualdad exacta (tolerancia float)
            errores.append(f"{clave}: local={local[clave]:,.0f} nube={nube:,.0f}")
    if errores:
        print("PARIDAD FALLIDA:\n  " + "\n  ".join(errores))
        print("¿Corriste `python scripts/sync_nube.py` justo antes?")
        return 1
    print(f"PARIDAD OK: por_cobrar={local['por_cobrar']:,.0f}  "
          f"n_pendientes={local['n_pendientes']}  "
          f"ventas_mes={local['ventas_mes']:,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
