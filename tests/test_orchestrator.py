# tests/test_orchestrator.py
import json
import pytest
from app.agent import orchestrator
from app.canvas.artifacts import Collector


class FakeCursor:
    """Cursor falso que registra lo ejecutado. `description=None` imita a las
    sentencias que no devuelven filas (INSERT/UPDATE/DELETE/DDL)."""
    def __init__(self, description=None, rows=None, registro=None):
        self.description = description
        self._rows = rows or []
        self.registro = registro if registro is not None else []

    def execute(self, q, params=None):
        self.registro.append(q)

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.cerrada = False
        self.readonly_seteado = None

    def cursor(self):
        return self._cursor

    def set_session(self, readonly=None, **kw):
        self.readonly_seteado = readonly

    def commit(self):
        self.commits += 1

    def close(self):
        self.cerrada = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_connect(monkeypatch, cursor):
    conn = FakeConn(cursor)
    monkeypatch.setattr(orchestrator.psycopg2, "connect", lambda *a, **kw: conn)
    return conn


def test_ejecutar_sql_local(monkeypatch):
    cur = FakeCursor(description=[("col1",)], rows=[{"col1": "val1"}])
    _fake_connect(monkeypatch, cur)
    res = orchestrator.ejecutar_sql_local("SELECT 1")
    data = json.loads(res)
    assert data == [{"col1": "val1"}]


# --- Blindaje de solo lectura (2026-07-20) ---
# El agente del PC habla con la BD REAL (fuente de verdad), no con una replica:
# una escritura aqui es irreversible. Mismo patron ya validado en el chat movil.

@pytest.mark.parametrize("sql_malo", [
    "DELETE FROM ventas",
    "UPDATE ventas SET fecha_pago = NULL",
    "INSERT INTO ventas (folio) VALUES (1)",
    "DROP TABLE ventas",
    "TRUNCATE ventas",
    "ALTER TABLE ventas ADD COLUMN x int",
    "SELECT 1; DELETE FROM ventas",          # multi-sentencia
])
def test_ejecutar_sql_local_rechaza_escrituras(monkeypatch, sql_malo):
    """Capa 1: la escritura ni siquiera llega a la BD (validacion de texto)."""
    cur = FakeCursor(description=None)
    conn = _fake_connect(monkeypatch, cur)

    res = orchestrator.ejecutar_sql_local(sql_malo)

    assert "Error" in res, f"deberia rechazar: {sql_malo}"
    assert cur.registro == [], "la sentencia no debe ejecutarse en la BD"
    assert conn.commits == 0, "NUNCA debe hacer commit"


def test_cte_con_escritura_queda_a_cargo_de_la_sesion_readonly(monkeypatch):
    """Capa 2: una CTE con DELETE empieza con WITH, asi que pasa la validacion
    de texto — y por eso existe la sesion READ ONLY, donde Postgres la rechaza.
    Aqui se verifica que la barrera este puesta y que nunca haya commit."""
    cur = FakeCursor(description=None)
    conn = _fake_connect(monkeypatch, cur)

    orchestrator.ejecutar_sql_local(
        "WITH x AS (DELETE FROM ventas RETURNING *) SELECT * FROM x")

    assert conn.readonly_seteado is True, "debe abrir la sesion en READ ONLY"
    assert conn.commits == 0, "NUNCA debe hacer commit"


def test_ejecutar_sql_local_abre_la_sesion_en_readonly(monkeypatch):
    """Segunda barrera: aunque el texto burle la validacion, la BD misma
    rechaza la escritura porque la sesion es READ ONLY."""
    cur = FakeCursor(description=[("n",)], rows=[{"n": 1}])
    conn = _fake_connect(monkeypatch, cur)

    orchestrator.ejecutar_sql_local("SELECT COUNT(*) AS n FROM ventas")

    assert conn.readonly_seteado is True
    assert conn.commits == 0


def test_ejecutar_sql_local_acepta_with_y_tolera_punto_y_coma_final(monkeypatch):
    cur = FakeCursor(description=[("n",)], rows=[{"n": 7}])
    _fake_connect(monkeypatch, cur)

    res = orchestrator.ejecutar_sql_local(
        "WITH t AS (SELECT 1) SELECT COUNT(*) AS n FROM t;")

    assert json.loads(res) == [{"n": 7}]
    # registro[0] es el statement_timeout; la consulta va despues, sin el ';'
    assert cur.registro[-1].endswith("FROM t"), "debe quitar el ; final"
    assert any("statement_timeout" in q for q in cur.registro)


def test_ejecutar_sql_local_cierra_la_conexion_siempre(monkeypatch):
    """Incluso cuando se rechaza antes de conectar, no debe quedar conexion
    abierta (el rechazo temprano ni siquiera abre una)."""
    cur = FakeCursor(description=[("n",)], rows=[{"n": 1}])
    conn = _fake_connect(monkeypatch, cur)
    orchestrator.ejecutar_sql_local("SELECT 1 AS n")
    assert conn.cerrada is True


def test_selector_de_modelos_coincide_ui_y_servidor():
    """La whitelist del servidor y las <option> de la UI deben ser el mismo
    conjunto: un id en la UI que el servidor rechace degrada al default sin
    avisar, y uno permitido pero no ofrecido es codigo muerto."""
    import re as _re
    from pathlib import Path
    from app import dashboard

    html = (Path(__file__).resolve().parent.parent /
            "app" / "dashboard_ui.html").read_text(encoding="utf-8")
    bloque = html.split('id="chat-model-select"', 1)[1].split("</select>", 1)[0]
    en_ui = set(_re.findall(r'<option value="([^"]+)"', bloque))

    assert en_ui == dashboard.MODELOS_CHAT_PERMITIDOS
    assert dashboard.MODELO_CHAT_DEFAULT in dashboard.MODELOS_CHAT_PERMITIDOS


def test_ningun_parametro_array_se_manda_sin_items():
    """Root cause del HTTP 400 con Gemini (2026-08-02).

    El atajo `{"x": list}` del decorador @tool emite {"type": "array"} SIN
    `items`, y el orquestador manda ese inputSchema tal cual a OpenRouter.
    Anthropic, OpenAI y GLM lo toleran; Google AI Studio rechaza la peticion
    ENTERA con INVALID_ARGUMENT ("properties[x].items: missing field"), asi que
    el chat moria apenas se elegia Gemini en el selector, con cualquier
    pregunta. Recorre los 4 servidores MCP para que una tool nueva declarada con
    `list` falle aqui y no en produccion.
    """
    import asyncio
    from mcp.types import ListToolsRequest
    from app.agent import memoria
    from app.agent.publish_tools import build_lienzo_server
    from app.agent.tools_acciones import build_acciones_server
    from app.agent.tools_negocio import build_negocio_server

    col = Collector()
    servidores = {
        "lienzo": build_lienzo_server(col)[0]["instance"],
        "negocio": build_negocio_server()[0]["instance"],
        "acciones": build_acciones_server(col)[0]["instance"],
        "memoria": memoria.build_memoria_server()[0]["instance"],
    }

    async def sin_items():
        faltantes = []
        for nombre, srv in servidores.items():
            res = await srv.request_handlers[ListToolsRequest](ListToolsRequest())
            for t in res.root.tools:
                for prop, spec in t.inputSchema.get("properties", {}).items():
                    if spec.get("type") == "array" and "items" not in spec:
                        faltantes.append(f"mcp__{nombre}__{t.name}.{prop}")
        return faltantes

    faltantes = asyncio.run(sin_items())
    assert faltantes == [], f"arrays sin `items` (Gemini los rechaza): {faltantes}"


def test_ejecutar_sql_local_recorta_resultados_enormes(monkeypatch):
    """Tope de filas: protege el contexto del modelo y el costo por tokens."""
    muchas = [{"id": i} for i in range(500)]
    cur = FakeCursor(description=[("id",)], rows=muchas)
    _fake_connect(monkeypatch, cur)

    res = orchestrator.ejecutar_sql_local("SELECT id FROM ventas")

    assert "mostrando 200 de 500 filas" in res

def test_run_session_persistence(monkeypatch):
    # Mock de llamar_openrouter_api para simular respuesta sin tools
    def mock_api(api_key, model, system, messages, tools=None):
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Respuesta fake"
                },
                "finish_reason": "stop"
            }]
        }
    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_api)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    collector = Collector()
    
    # Primera pregunta (debe generar session_id si es None)
    texto, session_id = orchestrator.run("Hola", collector, session_id=None)
    assert texto == "Respuesta fake"
    assert session_id is not None
    
    # Segunda pregunta usando el mismo session_id
    texto2, session_id2 = orchestrator.run("Segunda pregunta", collector, session_id=session_id)
    assert texto2 == "Respuesta fake"
    assert session_id2 == session_id
    
    # Verificar que el historial local tiene los mensajes enhebrados
    historial = orchestrator.CHAT_SESSIONS[session_id]
    assert len(historial) >= 4  # user(hola) + assistant(fake) + user(segunda) + assistant(fake)
    assert historial[0]["content"] == "Hola"
    assert historial[2]["content"] == "Segunda pregunta"


def test_al_agotar_los_pasos_responde_con_lo_que_alcanzo_a_reunir(monkeypatch):
    """Antes devolvia una disculpa vacia y tiraba a la basura todo lo que el
    agente ya habia averiguado en el turno. Ahora hace una ultima llamada SIN
    herramientas para que cierre con lo que tenga.

    La tool que se pide a proposito no existe: asi el loop gira sin tocar la BD.
    """
    llamadas = []

    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None):
        llamadas.append(tools)
        if tools:
            return {"choices": [{
                "message": {"role": "assistant", "content": None,
                            "tool_calls": [{"id": f"c{len(llamadas)}",
                                            "type": "function",
                                            "function": {"name": "mcp__inexistente__x",
                                                         "arguments": "{}"}}]},
                "finish_reason": "tool_calls"}]}
        return {"choices": [{
            "message": {"role": "assistant",
                        "content": "Alcance a ver el costo. Me falto el margen."},
            "finish_reason": "stop"}]}

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_api)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    texto, _sid = orchestrator.run("pregunta larga", Collector())

    assert texto == "Alcance a ver el costo. Me falto el margen."
    assert "limite de pasos" not in texto
    assert len(llamadas) == orchestrator.MAX_ITERACIONES + 1
    assert llamadas[-1] is None, "el turno de cierre va SIN herramientas"


def test_si_el_turno_de_cierre_falla_queda_el_mensaje_de_siempre(monkeypatch):
    """Red de seguridad: si la ultima llamada revienta, el usuario igual recibe
    una explicacion en vez de un string vacio."""
    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None):
        if tools is None:
            raise RuntimeError("OpenRouter caido")
        return {"choices": [{
            "message": {"role": "assistant", "content": None,
                        "tool_calls": [{"id": "c1", "type": "function",
                                        "function": {"name": "mcp__inexistente__x",
                                                     "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}]}

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_api)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    texto, _sid = orchestrator.run("otra pregunta", Collector())

    assert "límite de pasos" in texto


def test_el_turno_de_cierre_pide_mas_tokens_que_un_turno_normal(monkeypatch):
    """Root cause del bug del 2026-07-27: los modelos de razonamiento (GLM 5.2)
    gastan tokens PENSANDO antes de escribir, y esos reasoning_tokens cuentan
    contra max_tokens. Con un historial largo, los 1500 del loop se consumian
    razonando y la respuesta llegaba vacia (finish_reason=length, content=None),
    asi que el usuario veia el mensaje de limite de pasos aunque el agente si
    tenia los datos. El turno de cierre necesita su propio presupuesto.
    """
    vistos = []

    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None):
        vistos.append({"tools": tools, "max_tokens": max_tokens})
        if tools:
            return {"choices": [{
                "message": {"role": "assistant", "content": None,
                            "tool_calls": [{"id": f"c{len(vistos)}",
                                            "type": "function",
                                            "function": {"name": "mcp__inexistente__x",
                                                         "arguments": "{}"}}]},
                "finish_reason": "tool_calls"}]}
        return {"choices": [{
            "message": {"role": "assistant", "content": "Cierro con lo que tengo."},
            "finish_reason": "stop"}]}

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_api)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    texto, _sid = orchestrator.run("pregunta larga", Collector())

    assert texto == "Cierro con lo que tengo."
    assert orchestrator.MAX_TOKENS_CIERRE > orchestrator.MAX_TOKENS
    assert vistos[-1]["tools"] is None, "el cierre va sin herramientas"
    assert vistos[-1]["max_tokens"] == orchestrator.MAX_TOKENS_CIERRE
    # Los turnos del loop siguen con el presupuesto normal.
    assert vistos[0]["max_tokens"] is None


def test_un_turno_que_vuelve_vacio_no_llega_como_burbuja_en_blanco(monkeypatch):
    """El modelo puede terminar SIN tool_calls y SIN texto: tipicamente
    finish_reason=length, con todo el presupuesto gastado en razonamiento. El
    loop devolvia ese '' tal cual y la UI mostraba "(sin respuesta)".

    Ahora se cierra el turno con el presupuesto ampliado en vez de entregar una
    burbuja en blanco.
    """
    vistos = []

    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None):
        vistos.append(max_tokens)
        if len(vistos) == 1:
            # Se quedo sin tokens razonando: ni texto ni herramientas.
            return {"choices": [{"message": {"role": "assistant", "content": None},
                                 "finish_reason": "length"}]}
        return {"choices": [{
            "message": {"role": "assistant", "content": "Aqui va la respuesta."},
            "finish_reason": "stop"}]}

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_api)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    texto, _sid = orchestrator.run("pregunta cara", Collector())

    assert texto == "Aqui va la respuesta."
    assert len(vistos) == 2, "debe reintentar el cierre, no devolver vacio"
    assert vistos[1] == orchestrator.MAX_TOKENS_CIERRE


def test_el_system_prompt_le_dice_al_agente_que_dia_es_hoy(monkeypatch):
    """Sin la fecha, "el margen de junio" es una moneda al aire: el modelo
    elegia el año por su cuenta y consultaba junio del año pasado sin avisar.
    Toda pregunta relativa ("este mes", "el trimestre pasado") depende de esto.
    """
    from datetime import date
    vistos = []

    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None):
        vistos.append(system)
        return {"choices": [{"message": {"role": "assistant", "content": "ok"},
                             "finish_reason": "stop"}]}

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_api)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    orchestrator.run("¿cuánto vendí este mes?", Collector())

    hoy = date.today()
    assert str(hoy) in vistos[0], "el prompt debe traer la fecha de hoy"
    assert str(hoy.year) in vistos[0]
