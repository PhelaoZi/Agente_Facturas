"""Ejecuta el orquestador local en Python conectando con el Model Gateway de OpenRouter
y los servidores MCP in-process del Centro de Comando.
"""
import asyncio
import json
import os
import re
import sys
import threading
import time
import urllib.request
import uuid
from typing import Any, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

from app.agent import memoria
from app.agent.publish_tools import build_lienzo_server
from app.agent.system_prompt import SYSTEM_PROMPT
from app.agent.tools_negocio import build_negocio_server
from app.agent.tools_acciones import build_acciones_server
from app.canvas.artifacts import Collector
from app.config import DB_URL, PROJECT_ROOT

# Límite de turns e historial
MAX_ITERACIONES = 12
MAX_TOKENS = 1500

# Presupuesto aparte para el turno de cierre. Los modelos de razonamiento
# (GLM 5.2, el default) gastan tokens PENSANDO antes de escribir, y esos
# reasoning_tokens cuentan contra max_tokens. Cerrar un turno largo exige
# releer todo el historial: con 1500 el modelo se quedaba sin presupuesto
# razonando y devolvia content=None con finish_reason=length, o sea el usuario
# recibia "limite de pasos" aunque el agente ya tenia la respuesta.
MAX_TOKENS_CIERRE = 4000

# Estructura global en memoria para persistir el historial por session_id (mono-usuario/mono-proceso)
CHAT_SESSIONS = {}

MAX_FILAS_SQL = 200          # tope de filas devueltas al modelo
TIMEOUT_SQL_MS = 8000        # corta consultas pesadas (statement_timeout)


class EjecucionDetenida(Exception):
    """El usuario apretó Detener. No es un error: es una salida pedida."""


# Señal de cancelación del turno en curso. El servidor es mono-usuario, así que
# basta una sola: el endpoint /api/chat-stop la enciende desde OTRO hilo
# (ThreadingHTTPServer) mientras el loop sigue corriendo. Se apaga al empezar
# cada pregunta, para que un Detener viejo no mate la siguiente.
_DETENER = threading.Event()


def detener():
    """Pide cortar el turno en curso. Lo llama el endpoint del dashboard."""
    _DETENER.set()


def _abortar_si_detenido():
    """Corta antes de gastar otra llamada al modelo. Va al inicio de cada vuelta:
    lo que ya se pagó no vuelve, pero lo que falta sí se evita."""
    if _DETENER.is_set():
        raise EjecucionDetenida()


# Tablas que el agente consulta de verdad. La BD tiene 26 objetos entre tablas y
# vistas; meterlos todos costaría cientos de tokens en CADA llamada para nombres
# que nunca usa.
TABLAS_CLAVE = ("ventas", "clientes", "productos", "cuentas_por_pagar",
                "conciliaciones", "movimientos_banco", "seguimiento_comercial")

# Postgres avisa la columna que falta, pero no dice cuáles existen. En español o
# en inglés según el locale del servidor.
RE_COLUMNA_FALTANTE = re.compile(r"no existe la columna|column .* does not exist",
                                 re.IGNORECASE)
RE_TABLA_SQL = re.compile(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)

_ESQUEMA_CACHE = None


def _tablas_en(consulta: str) -> list:
    """Nombres de tabla que aparecen tras FROM o JOIN, sin repetir y en orden."""
    vistas = []
    for t in RE_TABLA_SQL.findall(consulta or ""):
        if t.lower() not in vistas:
            vistas.append(t.lower())
    return vistas


def _leer_columnas(tablas) -> dict:
    """{tabla: [columnas]} desde information_schema. Conexión propia: la del
    error quedó en transacción abortada y no acepta más consultas."""
    if not tablas:
        return {}
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ANY(%s) "
                "ORDER BY table_name, ordinal_position",
                (list(tablas),))
            out = {}
            for fila in cur.fetchall():
                out.setdefault(fila["table_name"], []).append(fila["column_name"])
            return out
    finally:
        conn.close()


def _pista_columnas(consulta: str, error) -> str:
    """Ante un error de columna inexistente, adjunta las columnas REALES.

    Visto el 2026-08-03: el agente consultó `fecha_emision` (es `fecha`), recibió
    solo "no existe la columna" y volvió a adivinar, gastando una vuelta entera.
    La BD sabe la respuesta; devolvérsela lo corrige a la primera. Solo aplica a
    errores de columna: en un timeout o un error de sintaxis esto sería ruido.
    """
    if not RE_COLUMNA_FALTANTE.search(str(error)):
        return ""
    try:
        columnas = _leer_columnas(_tablas_en(consulta))
    except Exception:
        return ""      # la pista es una ayuda; nunca debe tapar el error real
    if not columnas:
        return ""
    detalle = "\n".join(f"  {t}: {', '.join(cols)}" for t, cols in columnas.items())
    return f"\nColumnas reales de las tablas de esta consulta:\n{detalle}"


def bloque_esquema() -> str:
    """Columnas de las tablas clave, para el system prompt. Se lee UNA vez.

    Prevenir en vez de corregir: sin esto el agente adivina los nombres de
    columna en cada SQL improvisado. Se genera desde la BD y no se escribe a
    mano a propósito — una lista pegada en el prompt se desincroniza en silencio
    y el agente le cree igual.
    """
    global _ESQUEMA_CACHE
    if _ESQUEMA_CACHE is None:
        try:
            columnas = _leer_columnas(TABLAS_CLAVE)
            lineas = "\n".join(f"- {t}: {', '.join(cols)}"
                               for t, cols in sorted(columnas.items()))
            _ESQUEMA_CACHE = (
                f"\n\nCOLUMNAS REALES (no inventes nombres; si necesitas otra "
                f"tabla, consulta information_schema):\n{lineas}" if lineas else "")
        except Exception as e:
            print(f"No se pudo leer el esquema para el prompt: {e}")
            _ESQUEMA_CACHE = ""      # sin BD el chat igual responde
    return _ESQUEMA_CACHE


def ejecutar_sql_local(sql_str: str) -> str:
    """Ejecuta una consulta de SOLO LECTURA en la BD local.

    INVARIANTE: el agente NUNCA escribe en la base. Esta funcion recibe SQL
    generado por un modelo, asi que el blindaje va en capas (mismo patron que
    consulta_sql del chat movil, pero aqui importa mas: esta es la BD real,
    no una replica, y una escritura seria irreversible):

    1. Validacion de texto: una sola sentencia que empiece en SELECT/WITH.
    2. Sesion READ ONLY: Postgres MISMO rechaza cualquier escritura, aunque
       el texto burle la capa 1 (ej. una CTE con DELETE ... RETURNING).
    3. Limites: statement_timeout y tope de filas.

    Nunca hace commit: no hay camino de escritura que confirmar.
    """
    consulta = (sql_str or "").strip().rstrip(";").strip()
    if not consulta:
        return "Error: consulta vacía."
    if not re.match(r"^(select|with)\b", consulta, re.IGNORECASE):
        return ("Error: solo se permiten consultas de lectura (SELECT o WITH). "
                "Para modificar datos usa las herramientas de acciones, que "
                "piden confirmación al usuario.")
    if ";" in consulta:
        return "Error: una sola sentencia por consulta (sin ';')."

    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        # Capa 2: la barrera que no depende de adivinar la intencion del texto.
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {TIMEOUT_SQL_MS}")
            cur.execute(consulta)
            if not cur.description:
                # Una sentencia sin columnas paso las 3 capas: no deberia
                # ocurrir. Se informa sin confirmar nada (jamas commit).
                return "Error: la consulta no devolvió resultados de lectura."
            rows = cur.fetchall()
            total = len(rows)
            recortadas = rows[:MAX_FILAS_SQL]
            # Serializar fechas y Decimales usando str por defecto
            salida = json.dumps(recortadas, default=str, ensure_ascii=False)
            if total > MAX_FILAS_SQL:
                salida += (f"\n(mostrando {MAX_FILAS_SQL} de {total} filas; "
                           f"acota la consulta con LIMIT o filtros)")
            return salida
    except Exception as e:
        return f"Error ejecutando SQL: {e}{_pista_columnas(consulta, e)}"
    finally:
        conn.close()

def llamar_openrouter_api(api_key: str, model: str, system: str, messages: list,
                          tools: list | None = None, max_tokens: int | None = None,
                          session_id: str | None = None) -> dict:
    """Envía peticiones de completions a OpenRouter usando urllib.request.

    `session_id` va como cabecera `X-Session-Id` y sirve para el CACHÉ: cada
    vuelta reenvía ~5.400 tokens fijos idénticos (instrucciones + las 32
    herramientas). Los proveedores de GLM cachean solos, pero OpenRouter elige
    proveedor en cada llamada y un salto rompe el caché — medido el 2026-08-03:
    vueltas 1 y 2 a CoreWeave, la 3 a Fireworks, `cached_tokens=0` en las tres.
    Con el session_id, OpenRouter fija el proveedor (sticky routing) y las
    vueltas siguientes de la MISMA pregunta pueden reutilizar el prefijo.
    """
    url = "https://openrouter.ai/api/v1/chat/completions"

    # Preparar el cuerpo estilo OpenAI
    cuerpo = {
        "model": model,
        "messages": [{"role": "system", "content": system}] + messages,
        "max_tokens": max_tokens or MAX_TOKENS,
    }
    if tools:
        cuerpo["tools"] = tools
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://insforge.dev", # Referer opcional para OpenRouter
        "X-Title": "Zigurat ERP",
    }
    if session_id:
        headers["X-Session-Id"] = str(session_id)[:256]   # tope que impone OpenRouter
    
    req = urllib.request.Request(
        url,
        data=json.dumps(cuerpo).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    
    esperas = [0, 1000, 3000]
    ultimo_error = ""
    
    for espera in esperas:
        if espera > 0:
            time.sleep(espera / 1000.0)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            res_body = e.read().decode("utf-8")
            ultimo_error = f"HTTP {e.code} - {res_body}"
            if e.code not in (429, 500, 502, 503, 504):
                break
        except Exception as e:
            ultimo_error = str(e)
            
    raise RuntimeError(f"OpenRouter falló: {ultimo_error}")

MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _bloque_fecha(hoy=None):
    """Le dice al agente qué día es hoy.

    Sin esto, "el margen de junio" era una moneda al aire: el modelo elegía el
    año por su cuenta y consultaba junio del año pasado sin avisar. Toda
    pregunta relativa ("este mes", "el trimestre pasado", "la semana pasada")
    depende de este bloque.
    """
    from datetime import date, timedelta
    hoy = hoy or date.today()
    anterior = hoy.replace(day=1) - timedelta(days=1)
    mes_actual = f"{MESES_ES[hoy.month - 1]} de {hoy.year}"
    mes_pasado = f"{MESES_ES[anterior.month - 1]} de {anterior.year}"
    return (
        f"\n\nFECHA DE HOY: {hoy.isoformat()} ({mes_actual}).\n"
        f'"Este mes" = {mes_actual}. "El mes pasado" = {mes_pasado}. Cuando el '
        f"usuario nombre un mes sin año, entiende el MÁS RECIENTE que ya "
        f"ocurrió, no el del año anterior. Si aun así queda ambiguo, pregunta "
        f"antes de consultar: una cifra del período equivocado es peor que una "
        f"repregunta."
    )


MENSAJE_SIN_PASOS = ("No alcancé a terminar la consulta (límite de pasos del "
                     "agente). Intenta acotar tu pregunta.")

INSTRUCCION_CIERRE = (
    "Se acabaron los pasos disponibles para herramientas. Responde AHORA al "
    "usuario con lo que ya averiguaste, sin pedir mas herramientas. Si algo "
    "quedo incompleto, dilo explicitamente en una linea al final."
)


def _respuesta_de_cierre(api_key, model, system_prompt, historial, session_id=None):
    """Ultimo turno SIN tools: el modelo cierra con lo que ya reunio.

    Sin esto, agotar MAX_ITERACIONES botaba todo el trabajo del turno y el
    usuario recibia una disculpa vacia. Una respuesta parcial y honesta le
    sirve; la disculpa no. La instruccion de cierre NO se guarda en el
    historial: es andamiaje de este turno, no parte de la conversacion.
    """
    mensajes = historial + [{"role": "user", "content": INSTRUCCION_CIERRE}]
    try:
        resp = llamar_openrouter_api(api_key, model, system_prompt, mensajes, None,
                                     max_tokens=MAX_TOKENS_CIERRE,
                                     session_id=session_id)
        choice = resp["choices"][0]
        texto = (choice["message"].get("content") or "").strip()
        if not texto:
            # Sin esto el modo de falla es invisible: el usuario ve el mensaje
            # generico y en el log no queda por que.
            print(f"El turno de cierre no devolvió texto "
                  f"(finish_reason={choice.get('finish_reason')})")
        return texto
    except Exception as e:
        print(f"El turno de cierre falló: {e}")
        return ""


async def correr_loop_agente(
    pregunta: str,
    collector: Collector,
    session_id: str,
    model: str
) -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Falta la clave OPENROUTER_API_KEY en tu archivo .env.")

    # 1. Instanciar los servidores MCP locales
    lienzo_cfg, _ = build_lienzo_server(collector)
    negocio_cfg, _ = build_negocio_server()
    acciones_cfg, _ = build_acciones_server(collector)
    memoria_cfg, _ = memoria.build_memoria_server()

    servidores = {
        "lienzo": lienzo_cfg["instance"],
        "negocio": negocio_cfg["instance"],
        "acciones": acciones_cfg["instance"],
        "memoria": memoria_cfg["instance"]
    }

    # 2. Mapear herramientas en memoria
    # Recorremos cada servidor e invocamos su list_tools handler
    from mcp.types import ListToolsRequest, CallToolRequest, CallToolRequestParams
    
    mcp_tools_map = {}
    openai_tools = []

    for name, server in servidores.items():
        list_handler = server.request_handlers.get(ListToolsRequest)
        if list_handler:
            req = ListToolsRequest()
            res = await list_handler(req)
            for t in getattr(res.root, "tools", []):
                # El agente busca el formato mcp__nombreServer__nombreTool
                mcp_name = f"mcp__{name}__{t.name}"
                mcp_tools_map[mcp_name] = {
                    "server": name,
                    "tool_name": t.name,
                    "handler": server.request_handlers.get(CallToolRequest)
                }
                
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": mcp_name,
                        "description": t.description,
                        "parameters": t.inputSchema,
                    }
                })

    # Añadir tool de postgres query
    openai_tools.append({
        "type": "function",
        "function": {
            "name": "mcp__postgres__query",
            "description": "Ejecuta una consulta SQL de solo lectura en la réplica de base de datos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Consulta SQL SELECT"}
                },
                "required": ["query"]
            }
        }
    })

    # 3. Construir system prompt
    indice = memoria.leer_indice()
    system_prompt = SYSTEM_PROMPT + _bloque_fecha() + bloque_esquema()
    if indice:
        system_prompt += "\n\nMEMORIA DEL NEGOCIO (aprendida en sesiones anteriores):\n" + indice

    # 4. Obtener/crear historial de sesión
    if session_id not in CHAT_SESSIONS:
        CHAT_SESSIONS[session_id] = []
    
    historial = CHAT_SESSIONS[session_id]
    historial.append({"role": "user", "content": pregunta})

    # 5. Loop de ejecución de herramientas
    for _ in range(MAX_ITERACIONES):
        _abortar_si_detenido()      # el corte va ANTES de gastar la llamada
        # Preparar payload para la API (con tools si hay disponibles)
        body = {
            "messages": historial,
            "tools": openai_tools if openai_tools else None
        }
        
        resp = llamar_openrouter_api(api_key, model, system_prompt, body["messages"],
                                     openai_tools, session_id=session_id)
        choice = resp["choices"][0]
        msg = choice["message"]
        
        # Guardar respuesta del asistente en el historial local
        historial.append(msg)
        
        # Si no hay llamadas de herramientas o terminó normalmente, retornamos el texto
        if choice.get("finish_reason") != "tool_calls" or not msg.get("tool_calls"):
            texto = (msg.get("content") or "").strip()
            if texto:
                return texto
            # Ni texto ni herramientas: tipicamente finish_reason=length, con
            # todo el presupuesto gastado razonando. Devolver ese "" tal cual
            # dejaba una burbuja "(sin respuesta)" en la UI. Se cierra abajo,
            # con el presupuesto ampliado.
            print(f"Turno sin texto (finish_reason={choice.get('finish_reason')}); "
                  f"cerrando con presupuesto ampliado")
            historial.pop()          # el mensaje vacio no aporta nada al cierre
            break

        # Procesar llamadas a herramientas secuencialmente
        for tc in msg["tool_calls"]:
            nombre_tool = tc["function"]["name"]
            arguments_str = tc["function"]["arguments"]
            args = {}
            try:
                args = json.loads(arguments_str) if arguments_str else {}
            except Exception as e:
                print(f"Error parseando argumentos de {nombre_tool}: {e}")

            print(f"Agente llama a tool: {nombre_tool}({args})")
            
            contenido = ""
            if nombre_tool == "mcp__postgres__query":
                sql_q = args.get("query", "")
                contenido = ejecutar_sql_local(sql_q)
            elif nombre_tool in mcp_tools_map:
                t_info = mcp_tools_map[nombre_tool]
                if t_info["handler"]:
                    req_call = CallToolRequest(
                        method="tools/call",
                        params=CallToolRequestParams(
                            name=t_info["tool_name"],
                            arguments=args
                        )
                    )
                    try:
                        res_call = await t_info["handler"](req_call)
                        # Extraer texto de la lista de contenidos
                        items = getattr(res_call.root, "content", [])
                        text_parts = []
                        for item in items:
                            t_val = getattr(item, "text", None)
                            if isinstance(t_val, str):
                                text_parts.append(t_val)
                        contenido = "\n".join(text_parts) if text_parts else "Ejecutada con éxito."
                    except Exception as e:
                        contenido = f"Error ejecutando tool {nombre_tool}: {e}"
                else:
                    contenido = f"Error: herramienta {nombre_tool} no tiene manejador registrado."
            else:
                contenido = f"Error: herramienta '{nombre_tool}' desconocida."

            # Enhebrar el resultado como mensaje de rol 'tool'
            historial.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": nombre_tool,
                "content": contenido
            })

        # Publicar no le enseña nada al modelo: las tools del lienzo dibujan en
        # pantalla y no devuelven datos. Si ya escribió su respuesta en ESTA
        # vuelta, pedirle otra es regalar una llamada — medida entre 2,8 y 11,6s,
        # 1 de cada 3 en una pregunta simple.
        #
        # El `all(...)` es la barrera: con una tool de DATOS en la misma vuelta,
        # el texto todavía es prematuro ("voy a consultar la deuda…") y cortar
        # ahí le entregaría al usuario el relato en vez de la respuesta.
        texto_ya_escrito = (msg.get("content") or "").strip()
        if texto_ya_escrito and all(
                tc["function"]["name"].startswith("mcp__lienzo__")
                for tc in msg["tool_calls"]):
            return texto_ya_escrito

    _abortar_si_detenido()      # detener tampoco paga el turno de cierre
    texto = _respuesta_de_cierre(api_key, model, system_prompt, historial, session_id)
    if texto:
        historial.append({"role": "assistant", "content": texto})
        return texto
    return MENSAJE_SIN_PASOS

def run(
    pregunta: str,
    collector: Collector,
    session_id: str | None = None,
    model: str = "z-ai/glm-5.2"
) -> Tuple[str, str | None]:
    """Punto de entrada síncrono del dashboard. Ejecuta el loop de OpenRouter."""
    if not session_id:
        session_id = str(uuid.uuid4())

    # Un Detener de hace un rato no debe matar esta pregunta.
    _DETENER.clear()
    # Punto al que se revierte el historial si el usuario detiene a media vuelta:
    # dejarlo con un `tool_calls` del asistente sin su respuesta hace que la
    # pregunta SIGUIENTE la rechace la API del modelo.
    largo_previo = len(CHAT_SESSIONS.get(session_id, []))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        texto = loop.run_until_complete(
            correr_loop_agente(pregunta, collector, session_id, model)
        )
    except EjecucionDetenida:
        del CHAT_SESSIONS[session_id][largo_previo:]
        raise
    finally:
        loop.close()

    return texto, session_id


# --- Compatibilidad con Tests ---
class DummyOptions:
    def __init__(self, system_prompt, allowed_tools, mcp_servers, resume=None, setting_sources=None, strict_mcp_config=None, model=None, disallowed_tools=None, permission_mode=None):
        self.system_prompt = system_prompt
        self.allowed_tools = allowed_tools
        self.mcp_servers = mcp_servers
        self.resume = resume
        self.setting_sources = setting_sources or []
        self.strict_mcp_config = strict_mcp_config if strict_mcp_config is not None else True
        self.model = model or "sonnet"
        self.disallowed_tools = disallowed_tools or []
        self.permission_mode = permission_mode or "bypassPermissions"

def _build_options(collector: Collector, session_id: str | None = None) -> DummyOptions:
    indice = memoria.leer_indice()
    system_prompt = SYSTEM_PROMPT
    if indice:
        system_prompt += "\n\nMEMORIA DEL NEGOCIO (aprendida en sesiones anteriores):\n" + indice
    return DummyOptions(
        system_prompt=system_prompt,
        allowed_tools=[
            "mcp__memoria__guardar_nota",
            "mcp__memoria__leer_nota",
            "mcp__postgres__query",
            "mcp__lienzo__publicar_kpi",
            "mcp__negocio__deuda_total",
            "mcp__negocio__flujo_caja",
            "mcp__acciones__proponer_gasto"
        ],
        mcp_servers={"memoria": None, "acciones": None},
        resume=session_id
    )
