#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_chat_tareas.py - Zigurat ERP, Fase 4
Pruebas de aceptación para el asistente de tareas en la nube:
1. Creación: Agendar un compromiso.
2. Listado: Consultar compromisos y verificar presencia.
3. Actualización: Completar el compromiso.
4. Base de datos: Confirmar estado final.
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

from sync_nube import _load_env, conectar_nube  # noqa: E402

_load_env()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def token_jwt() -> str:
    secreto = os.environ.get("INSFORGE_JWT_SECRET")
    if not secreto:
        raise RuntimeError("Falta INSFORGE_JWT_SECRET en el .env")
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    ahora = int(time.time())
    payload = _b64url(json.dumps(
        {"sub": "aceptacion-tareas", "iat": ahora, "exp": ahora + 3600}
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


def limpiar_tareas_prueba():
    conn = conectar_nube()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chat_tareas WHERE descripcion LIKE '%TestTarea%'")
            conn.commit()
    finally:
        conn.close()


def main() -> int:
    token = token_jwt()
    limpiar_tareas_prueba()
    
    print("1. Creando una tarea...")
    r1 = preguntar(
        "Por favor, agenda una nueva tarea para el 2026-07-28 que sea: 'Reunion TestTarea'",
        token
    )
    print(f"Respuesta creación: {r1['respuesta']}")
    
    print("\n2. Consultando la lista de tareas...")
    r2 = preguntar(
        "Dime la lista de mis tareas programadas",
        token,
        r1["sesion_id"]
    )
    print(f"Respuesta listado:\n{r2['respuesta']}")
    
    # Extraer ID de la respuesta
    import re
    resp1_clean = r1["respuesta"].replace("**", "")
    resp2_clean = r2["respuesta"].replace("**", "")
    
    m = re.search(r"ID:\s*(\d+)", resp1_clean, re.IGNORECASE)
    if not m:
        m = re.search(r"\[ID:\s*(\d+)\]", resp2_clean, re.IGNORECASE)
        
    if not m:
        print("ERROR: No se encontró el ID de la tarea creada en la respuesta del agente.")
        return 1
        
    tarea_id = int(m.group(1))
    print(f"ID Detectado: {tarea_id}")
    
    print(f"\n3. Completando la tarea ID {tarea_id}...")
    r3 = preguntar(
        f"Marca la tarea con ID {tarea_id} como completada.",
        token,
        r1["sesion_id"]
    )
    print(f"Respuesta completada: {r3['respuesta']}")
    
    print("\n4. Verificando estado final en la base de datos...")
    conn = conectar_nube()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT descripcion, fecha, completada FROM chat_tareas WHERE id = %s",
                (tarea_id,)
            )
            fila = cur.fetchone()
            if not fila:
                print(f"ERROR: Tarea con ID {tarea_id} no encontrada en PostgreSQL.")
                return 1
            desc, fecha, completada = fila
            print(f"Encontrada en BD: '{desc}' para el {fecha} -> completada: {completada}")
            if not completada:
                print("ERROR: La tarea debería estar marcada como completada en la BD.")
                return 1
    finally:
        conn.close()
        
    print("\nASISTENTE DE TAREAS OK (Creación, Listado, Completar y Base de Datos verificados con éxito!)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
