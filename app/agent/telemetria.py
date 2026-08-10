"""Telemetría del agente: qué cuesta y cómo trabaja cada turno.

Hasta el 2026-08-09 el orquestador recibía el bloque `usage` de cada llamada al
modelo y lo tiraba: usaba `resp["choices"][0]` y nada más. No había forma de
contestar cuánto cuesta el chat, ni si el caché de prefijo está pegando —
justo lo que el `X-Session-Id` intenta proteger.

INVARIANTE: la telemetría NUNCA puede romper el chat. Si la BD está caída o el
proveedor cambió el formato de su respuesta, se pierde el dato de medición; la
respuesta al usuario sigue su camino. Medir no puede costar una respuesta.
"""
import json

import psycopg2

from app.config import DB_URL

INSERT = """
    INSERT INTO chat_telemetria (
        session_id, turno_id, iteracion, modelo, pregunta, proveedor,
        prompt_tokens, cached_tokens, completion_tokens, reasoning_tokens,
        latencia_ms, finish_reason, tools_llamadas, costo_usd, usage_crudo)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _conectar():
    """Conexión propia y de vida corta. Separada para que los tests la
    reemplacen sin tocar la base."""
    return psycopg2.connect(DB_URL)


def _entero(valor):
    """Los proveedores mandan números como int, str o nada."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _decimal(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def extraer_uso(respuesta) -> dict:
    """Normaliza el bloque `usage` de una respuesta del modelo.

    Cada proveedor manda lo suyo y algunos no mandan nada, así que todo campo
    puede volver en None. Se guarda además el `usage` crudo completo: si mañana
    el proveedor agrega un campo útil, ya queda registrado y no hay que
    reprocesar histórico.
    """
    respuesta = respuesta or {}
    usage = respuesta.get("usage") or {}
    detalle_prompt = usage.get("prompt_tokens_details") or {}
    detalle_salida = usage.get("completion_tokens_details") or {}
    choices = respuesta.get("choices") or [{}]

    return {
        "proveedor": respuesta.get("provider"),
        "prompt_tokens": _entero(usage.get("prompt_tokens")),
        "completion_tokens": _entero(usage.get("completion_tokens")),
        # La métrica que decide si el caché de prefijo sirve: viene anidada.
        "cached_tokens": _entero(detalle_prompt.get("cached_tokens")),
        # Los modelos de razonamiento gastan esto ANTES de escribir, y cuenta
        # contra max_tokens: es la causa de los turnos que se cortan sin texto.
        "reasoning_tokens": _entero(detalle_salida.get("reasoning_tokens")),
        # Se le pide el costo al proveedor en vez de mantener una tabla de
        # precios propia: una lista pegada en el código se desincroniza en
        # silencio (misma lección que PRECIOS_VENTA_NETO).
        "costo_usd": _decimal(usage.get("cost")),
        "finish_reason": (choices[0] or {}).get("finish_reason"),
        "usage_crudo": usage or None,
    }


def registrar(respuesta, *, session_id, turno_id, iteracion, modelo, pregunta,
              latencia_ms, tools_llamadas=()):
    """Guarda una fila por llamada al modelo. Nunca lanza.

    `turno_id` agrupa las vueltas de UNA pregunta: el costo de una tarea es la
    suma de sus filas, no una fila. Por eso no hay tabla de turnos — se agrega
    con SQL y no hay dos sitios que puedan quedar inconsistentes.
    """
    try:
        uso = extraer_uso(respuesta)
        conn = _conectar()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(INSERT, (
                    session_id, turno_id, iteracion, modelo, pregunta,
                    uso["proveedor"], uso["prompt_tokens"], uso["cached_tokens"],
                    uso["completion_tokens"], uso["reasoning_tokens"],
                    latencia_ms, uso["finish_reason"], list(tools_llamadas),
                    uso["costo_usd"],
                    json.dumps(uso["usage_crudo"]) if uso["usage_crudo"] else None,
                ))
        finally:
            conn.close()
    except Exception as e:
        # Se avisa pero no se propaga: la alternativa es que medir el turno
        # cueste el turno.
        print(f"(aviso) no se pudo registrar la telemetría: {e}")
