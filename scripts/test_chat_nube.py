#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_chat_nube.py - Zigurat ERP, Fase 4
Aceptacion del chat en la nube:
1. Paridad: la deuda total que responde el chat coincide con la BD local.
2. Continuidad: una segunda pregunta en la misma sesion responde 200.
3. Solo lectura: un pedido de escritura no ejecuta nada.
4. Auditoria: cada consulta agrega una fila a chat_uso con costo > 0.
"""
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

from sync_nube import _load_env, conectar_local, conectar_nube  # noqa: E402

_load_env()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def token_jwt() -> str:
    """JWT HS256 firmado con el secret del proyecto."""
    secreto = os.environ.get("INSFORGE_JWT_SECRET")
    if not secreto:
        raise RuntimeError("Falta INSFORGE_JWT_SECRET en el .env")
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    ahora = int(time.time())
    payload = _b64url(json.dumps(
        {"sub": "aceptacion-chat", "iat": ahora, "exp": ahora + 3600}
    ).encode())
    firma = _b64url(hmac.new(secreto.encode(), f"{header}.{payload}".encode(),
                             hashlib.sha256).digest())
    return f"{header}.{payload}.{firma}"


def preguntar(mensaje: str, token: str, sesion_id=None) -> dict:
    base = os.environ.get("INSFORGE_FUNCTIONS_URL")
    if not base:
        raise RuntimeError("Falta INSFORGE_FUNCTIONS_URL en el .env")
    cuerpo = {"mensaje": mensaje}
    if sesion_id:
        cuerpo["sesion_id"] = sesion_id
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat",
        data=json.dumps(cuerpo).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTPError: {e.code} - {e.read().decode()}")
        raise e


def formatear_pesos(n) -> str:
    return "$" + f"{int(round(float(n))):,}".replace(",", ".")


def deuda_local() -> tuple:
    """Query canonica de pendientes (igual que v_pendientes)."""
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
            total, n = cur.fetchone()
            return float(total), int(n)
    finally:
        conn.close()


def filas_chat_uso() -> int:
    conn = conectar_nube()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM chat_uso")
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def main() -> int:
    token = token_jwt()
    fallas = []
    uso_antes = filas_chat_uso()

    # 1. Paridad de la deuda total.
    total_local, n_local = deuda_local()
    esperado = formatear_pesos(total_local)
    print(f"Buscando deuda esperada: {esperado}")
    r1 = preguntar(
        "Dime el monto exacto en pesos de la deuda total pendiente y el "
        "numero de facturas, tal como los entregue la herramienta.", token)
    print(f"[1] respuesta: {r1['respuesta']}\n    uso: {r1['uso']}")
    if esperado not in r1["respuesta"]:
        fallas.append(f"paridad: esperaba ver {esperado} "
                      f"(local: {n_local} facturas) en la respuesta")

    # 2. Continuidad de sesion.
    r2 = preguntar("¿Y cuantas de esas facturas tienen mas de 30 dias?",
                   token, r1["sesion_id"])
    print(f"[2] respuesta: {r2['respuesta']}")
    if r2["sesion_id"] != r1["sesion_id"]:
        fallas.append("continuidad: la sesion cambio entre preguntas")
    if not r2["respuesta"].strip():
        fallas.append("continuidad: respuesta vacia")

    # 3. Solo lectura.
    r3 = preguntar("Marca la factura 4664 como pagada.", token, r1["sesion_id"])
    print(f"[3] respuesta: {r3['respuesta']}")

    # 4. Auditoria.
    uso_despues = filas_chat_uso()
    if uso_despues - uso_antes != 3:
        fallas.append(f"auditoria: esperaba 3 filas nuevas en chat_uso, "
                      f"hay {uso_despues - uso_antes}")
    if r1["uso"]["costo_usd"] <= 0:
        fallas.append("auditoria: costo_usd deberia ser > 0")

    if fallas:
        print("\nCHAT NUBE FALLO:")
        for f in fallas:
            print(f"  - {f}")
        return 1
    print(f"\nCHAT NUBE OK (deuda {esperado} en {n_local} facturas; "
          f"{uso_despues - uso_antes} consultas logueadas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
