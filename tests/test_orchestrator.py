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

    Un parametro de tipo lista declarado sin `items` se manda tal cual a
    OpenRouter. Anthropic, OpenAI y GLM lo toleran; Google AI Studio rechaza la
    peticion ENTERA con INVALID_ARGUMENT ("properties[x].items: missing field"),
    asi que el chat moria apenas se elegia Gemini en el selector, con cualquier
    pregunta. Recorre los 4 registros para que una tool nueva mal declarada
    falle aqui y no en produccion.
    """
    from app.agent import memoria
    from app.agent.publish_tools import build_lienzo_server
    from app.agent.tools_acciones import build_acciones_server
    from app.agent.tools_negocio import build_negocio_server

    col = Collector()
    registros = [build_lienzo_server(col)[0], build_negocio_server()[0],
                 build_acciones_server(col)[0], memoria.build_memoria_server()[0]]

    faltantes = [
        f"{s['function']['name']}.{prop}"
        for r in registros for s in r.schemas_openai()
        for prop, spec in s["function"]["parameters"].get("properties", {}).items()
        if spec.get("type") == "array" and "items" not in spec
    ]

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
    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None,
                 session_id=None):
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

    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None, session_id=None):
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
    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None, session_id=None):
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


def test_el_turno_de_cierre_lleva_su_propio_presupuesto(monkeypatch):
    """Root cause del bug del 2026-07-27: los modelos de razonamiento (GLM 5.2)
    gastan tokens PENSANDO antes de escribir, y esos reasoning_tokens cuentan
    contra max_tokens. Con un historial largo, los 1500 del loop se consumian
    razonando y la respuesta llegaba vacia (finish_reason=length, content=None),
    asi que el usuario veia el mensaje de limite de pasos aunque el agente si
    tenia los datos. El turno de cierre necesita su propio presupuesto.

    Este test pedia MAX_TOKENS_CIERRE > MAX_TOKENS. Dejo de pedirlo el
    2026-08-09: la telemetria mostro que el turno MAS exigente es el del loop,
    no el del cierre — la vuelta del loop tiene que razonar Y emitir la llamada
    a la herramienta, mientras el cierre solo redacta prosa (midio 425 tokens de
    4000 disponibles). Subir el loop a 4000 dejo la desigualdad estricta sin
    sustento, pero la leccion original sigue en pie y es la que se verifica
    aqui: el cierre lleva presupuesto EXPLICITO y nunca hereda un default.
    """
    vistos = []

    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None, session_id=None):
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
    cierre = vistos[-1]
    assert cierre["tools"] is None, "el cierre va sin herramientas"
    assert cierre["max_tokens"] == orchestrator.MAX_TOKENS_CIERRE, \
        "el cierre lleva presupuesto explicito, no el default del loop"
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

    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None, session_id=None):
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


# ── El error de columna enseña el esquema ─────────────────────────────────────
# Visto el 2026-08-03: el agente consulto `fecha_emision` (no existe; es `fecha`)
# y Postgres solo respondio "no existe la columna", sin decir cuales SI existen.
# El agente adivino de nuevo y gasto una vuelta entera. La BD sabe la respuesta:
# basta devolversela.

def test_tablas_en_saca_los_nombres_del_from_y_los_join():
    sql = ("SELECT v.folio FROM ventas v JOIN clientes c ON c.rut_cliente = "
           "v.rut_cliente LEFT JOIN productos p ON p.id = v.folio")
    assert orchestrator._tablas_en(sql) == ["ventas", "clientes", "productos"]


def test_tablas_en_ignora_alias_y_no_repite():
    assert orchestrator._tablas_en("SELECT * FROM ventas v, ventas w") == ["ventas"]


def test_la_pista_lista_las_columnas_reales_de_la_tabla(monkeypatch):
    monkeypatch.setattr(orchestrator, "_leer_columnas",
                        lambda tablas: {"ventas": ["folio", "fecha", "fecha_pago"]})

    pista = orchestrator._pista_columnas(
        "SELECT * FROM ventas WHERE fecha_emision > '2026-01-01'",
        'no existe la columna «fecha_emision»')

    assert "ventas" in pista
    assert "fecha_pago" in pista and "folio" in pista


def test_no_agrega_pista_cuando_el_error_no_es_de_columna(monkeypatch):
    """Un error de sintaxis o un timeout no se arreglan con la lista de
    columnas: agregarla ahi seria ruido en el contexto del modelo."""
    monkeypatch.setattr(orchestrator, "_leer_columnas",
                        lambda tablas: {"ventas": ["folio"]})

    assert orchestrator._pista_columnas("SELECT * FROM ventas",
                                        "canceling statement due to statement timeout") == ""


def test_ejecutar_sql_local_devuelve_las_columnas_al_fallar(monkeypatch):
    """Integración: el agente recibe el error Y la salida del laberinto."""
    class CursorQueFalla:
        description = None
        def execute(self, q, params=None):
            if "statement_timeout" not in q:
                raise RuntimeError('no existe la columna «fecha_emision»')
        def __enter__(self): return self
        def __exit__(self, *a): return False

    _fake_connect(monkeypatch, CursorQueFalla())
    monkeypatch.setattr(orchestrator, "_leer_columnas",
                        lambda tablas: {"ventas": ["folio", "fecha", "monto_total"]})

    res = orchestrator.ejecutar_sql_local("SELECT * FROM ventas WHERE fecha_emision > '2026-01-01'")

    assert "fecha_emision" in res          # el error original se conserva
    assert "monto_total" in res            # y ahora sabe que columnas hay


# ── Esquema en el prompt: prevenir en vez de corregir ─────────────────────────

def test_el_bloque_de_esquema_nombra_las_columnas_de_ventas(monkeypatch):
    """Se genera desde la BD, no se escribe a mano: una lista pegada en el
    prompt se desincroniza y el agente le cree igual."""
    monkeypatch.setattr(orchestrator, "_ESQUEMA_CACHE", None)
    monkeypatch.setattr(orchestrator, "_leer_columnas",
                        lambda tablas: {"ventas": ["folio", "fecha", "fecha_pago"],
                                        "clientes": ["rut_cliente", "razon_social"]})

    bloque = orchestrator.bloque_esquema()

    assert "ventas" in bloque and "fecha_pago" in bloque
    assert "clientes" in bloque and "razon_social" in bloque


def test_el_esquema_se_lee_una_sola_vez(monkeypatch):
    """Va en cada pregunta: no puede costar una consulta a la BD cada vez."""
    lecturas = []
    monkeypatch.setattr(orchestrator, "_ESQUEMA_CACHE", None)
    monkeypatch.setattr(orchestrator, "_leer_columnas",
                        lambda tablas: (lecturas.append(1), {"ventas": ["folio"]})[1])

    orchestrator.bloque_esquema()
    orchestrator.bloque_esquema()

    assert len(lecturas) == 1


def test_si_la_bd_no_responde_el_esquema_no_voltea_el_chat(monkeypatch):
    """Sin BD el chat igual debe contestar lo que no dependa de ella."""
    def explota(tablas):
        raise RuntimeError("BD caida")

    monkeypatch.setattr(orchestrator, "_ESQUEMA_CACHE", None)
    monkeypatch.setattr(orchestrator, "_leer_columnas", explota)

    assert orchestrator.bloque_esquema() == ""


# ── Publicar y responder en el mismo turno ────────────────────────────────────
# Medido el 2026-08-03: "cuanto me deben en total?" gastaba 3 vueltas — pedir el
# dato, publicar KPI+grafico, y recien escribir. La vuelta del medio no averigua
# NADA: las tools del lienzo no devuelven informacion al modelo. Si el modelo ya
# escribio su respuesta junto con los publicar_*, esa vuelta extra es regalada.

# Argumentos válidos por tool: las del lienzo fallarían con {} y no publicarían.
_ARGS = {
    "mcp__lienzo__publicar_kpi": {"etiqueta": "Deuda", "valor": "$8.883.587", "delta": ""},
    "mcp__lienzo__publicar_grafico": {"titulo": "Deuda", "chart_type": "bar",
                                      "x": ["a"], "y": [1]},
    "mcp__lienzo__publicar_tabla": {"titulo": "T", "columnas": ["c"], "filas": [["v"]]},
}


def _turno(texto, tools, finish="tool_calls"):
    llamadas = [{"id": f"c{i}", "type": "function",
                 "function": {"name": n, "arguments": json.dumps(_ARGS.get(n, {}))}}
                for i, n in enumerate(tools)]
    msg = {"role": "assistant", "content": texto}
    if llamadas:
        msg["tool_calls"] = llamadas
    return {"choices": [{"message": msg, "finish_reason": finish}]}


def _guionizar(monkeypatch, turnos):
    """El modelo falso responde el guion, turno por turno. Devuelve el contador."""
    usados = []

    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None,
                 session_id=None):
        usados.append(1)
        return turnos[min(len(usados) - 1, len(turnos) - 1)]

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_api)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")
    return usados


def test_si_publica_y_ya_escribio_la_respuesta_no_gasta_otra_vuelta(monkeypatch):
    usados = _guionizar(monkeypatch, [
        _turno("Te deben $8.883.587 en 55 facturas.",
               ["mcp__lienzo__publicar_kpi", "mcp__lienzo__publicar_grafico"]),
        _turno("ESTA VUELTA NO DEBERIA OCURRIR", [], finish="stop"),
    ])
    col = Collector()

    texto, _sid = orchestrator.run("cuanto me deben?", col)

    assert texto == "Te deben $8.883.587 en 55 facturas."
    assert len(usados) == 1, "publicar no justifica otra vuelta al modelo"
    assert len(col.items) == 2, "los artefactos igual se publican"


def test_si_pide_un_dato_sigue_el_ciclo_aunque_traiga_texto(monkeypatch):
    """Barrera clave: con una tool de DATOS el texto es prematuro ('voy a
    consultar…'). Ahi el ciclo tiene que seguir, o el usuario recibe el relato
    en vez de la respuesta."""
    usados = _guionizar(monkeypatch, [
        _turno("Voy a consultar la deuda.", ["mcp__negocio__deuda_total"]),
        _turno("Te deben $8.883.587.", [], finish="stop"),
    ])

    texto, _sid = orchestrator.run("cuanto me deben?", Collector())

    assert texto == "Te deben $8.883.587."
    assert len(usados) == 2


def test_si_publica_sin_escribir_nada_sigue_como_siempre(monkeypatch):
    """Los modelos que no mandan texto junto a las tool_calls no cambian de
    comportamiento: necesitan la vuelta extra para redactar."""
    usados = _guionizar(monkeypatch, [
        _turno(None, ["mcp__lienzo__publicar_tabla"]),
        _turno("Aqui va el ranking.", [], finish="stop"),
    ])

    texto, _sid = orchestrator.run("ranking de clientes", Collector())

    assert texto == "Aqui va el ranking."
    assert len(usados) == 2


def test_mezcla_de_lienzo_y_datos_no_corta(monkeypatch):
    """Si en la misma vuelta publica Y pide un dato, todavia falta informacion."""
    usados = _guionizar(monkeypatch, [
        _turno("Publico lo que tengo y sigo.",
               ["mcp__lienzo__publicar_kpi", "mcp__negocio__ranking_clientes"]),
        _turno("Listo, aqui esta todo.", [], finish="stop"),
    ])

    texto, _sid = orchestrator.run("resumen", Collector())

    assert texto == "Listo, aqui esta todo."
    assert len(usados) == 2


# ── Caché de prompt: sticky routing ───────────────────────────────────────────
# Cada vuelta reenvía ~5.400 tokens fijos idénticos (instrucciones + catálogo de
# 32 herramientas). Los proveedores de GLM cachean solos, PERO OpenRouter enruta
# cada llamada por su cuenta: medido el 2026-08-03, las vueltas 1 y 2 fueron a
# CoreWeave y la 3 saltó a Fireworks, con cached_tokens=0 en las tres. Mandando
# un session_id, OpenRouter fija el proveedor y el caché puede pegar.

class _RespuestaFalsa:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capturar_peticion(monkeypatch):
    """Intercepta urlopen y devuelve un dict que se llena con la petición."""
    capturado = {}

    def fake_urlopen(req, timeout=None):
        capturado["headers"] = {k.lower(): v for k, v in req.headers.items()}
        capturado["cuerpo"] = json.loads(req.data.decode("utf-8"))
        return _RespuestaFalsa({"choices": [{"message": {"role": "assistant",
                                                         "content": "ok"},
                                             "finish_reason": "stop"}]})

    monkeypatch.setattr(orchestrator.urllib.request, "urlopen", fake_urlopen)
    return capturado


def test_manda_el_session_id_para_fijar_el_proveedor(monkeypatch):
    cap = _capturar_peticion(monkeypatch)

    orchestrator.llamar_openrouter_api(
        "clave", "z-ai/glm-5.2", "instrucciones",
        [{"role": "user", "content": "hola"}], session_id="sesion-abc")

    assert cap["headers"].get("x-session-id") == "sesion-abc"


def test_sin_session_id_no_manda_la_cabecera(monkeypatch):
    """Los tests y scripts que llaman sin sesión deben seguir funcionando."""
    cap = _capturar_peticion(monkeypatch)

    orchestrator.llamar_openrouter_api("clave", "m", "sys",
                                       [{"role": "user", "content": "hola"}])

    assert "x-session-id" not in cap["headers"]


def test_el_loop_le_pasa_la_sesion_de_la_conversacion(monkeypatch):
    """De nada sirve el parámetro si el loop no lo usa: las vueltas de una misma
    pregunta tienen que ir todas al mismo proveedor."""
    vistos = []

    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None,
                 session_id=None):
        vistos.append(session_id)
        return {"choices": [{"message": {"role": "assistant", "content": "listo"},
                             "finish_reason": "stop"}]}

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_api)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    _texto, sid = orchestrator.run("una pregunta", Collector())

    assert vistos and all(v == sid for v in vistos), \
        "todas las vueltas deben ir con el mismo session_id"


# ── Botón Detener ─────────────────────────────────────────────────────────────
# Una pregunta puede costar hasta MAX_ITERACIONES llamadas al modelo. Si el
# usuario se arrepiente, lo que ya se gastó no vuelve, pero todo lo que falta sí
# se puede evitar. El corte va al INICIO de cada vuelta, antes de llamar.

def _mock_pide_tool(registro, al_llamar=None):
    """Modelo falso que siempre pide una herramienta inexistente: hace girar el
    loop sin tocar la BD. `al_llamar` corre en cada llamada (para detener)."""
    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None, session_id=None):
        registro.append(1)
        if al_llamar:
            al_llamar()
        return {"choices": [{
            "message": {"role": "assistant", "content": None,
                        "tool_calls": [{"id": f"c{len(registro)}", "type": "function",
                                        "function": {"name": "mcp__inexistente__x",
                                                     "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}]}
    return mock_api


def test_detener_corta_el_loop_y_no_vuelve_a_llamar_al_modelo(monkeypatch):
    """El ahorro real: detener en la vuelta 1 evita las 11 restantes."""
    llamadas = []
    monkeypatch.setattr(orchestrator, "llamar_openrouter_api",
                        _mock_pide_tool(llamadas, al_llamar=orchestrator.detener))
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    with pytest.raises(orchestrator.EjecucionDetenida):
        orchestrator.run("pregunta cara", Collector())

    assert len(llamadas) == 1, "no debe haber ni una llamada despues de detener"


def test_al_detener_el_historial_queda_como_antes_de_la_pregunta(monkeypatch):
    """Lo que de verdad importa. Al cortar a media vuelta el historial queda con
    un `tool_calls` del asistente sin su respuesta: si eso se guarda, la
    SIGUIENTE pregunta la rechaza la API del modelo. Hay que revertirlo."""
    def mock_ok(api_key, model, system, messages, tools=None, max_tokens=None, session_id=None):
        return {"choices": [{"message": {"role": "assistant", "content": "Respuesta 1"},
                             "finish_reason": "stop"}]}

    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")
    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_ok)
    _texto, sid = orchestrator.run("primera", Collector())
    historial_sano = list(orchestrator.CHAT_SESSIONS[sid])

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api",
                        _mock_pide_tool([], al_llamar=orchestrator.detener))
    with pytest.raises(orchestrator.EjecucionDetenida):
        orchestrator.run("segunda, me arrepenti", Collector(), session_id=sid)

    assert orchestrator.CHAT_SESSIONS[sid] == historial_sano
    assert not any(m.get("tool_calls") for m in orchestrator.CHAT_SESSIONS[sid])


def test_una_senal_de_detencion_vieja_no_mata_la_pregunta_siguiente(monkeypatch):
    """La señal se apaga al empezar cada pregunta. Si quedara encendida, el
    Detener de hace un rato cancelaría la pregunta nueva apenas se escribe."""
    def mock_ok(api_key, model, system, messages, tools=None, max_tokens=None, session_id=None):
        return {"choices": [{"message": {"role": "assistant", "content": "Respondí igual"},
                             "finish_reason": "stop"}]}

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_ok)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    orchestrator.detener()                      # señal vieja, sin nada corriendo
    texto, _sid = orchestrator.run("pregunta nueva", Collector())

    assert texto == "Respondí igual"


def test_run_agent_reporta_detenido_y_no_lo_trata_como_error(monkeypatch):
    """Detener es una salida pedida, no un fallo: no debe llegar a la UI como
    "Tuve un problema al responder" ni ensuciar logs/agente_chat.log."""
    from app import dashboard

    def detenido(*a, **kw):
        raise orchestrator.EjecucionDetenida()

    monkeypatch.setattr(orchestrator, "run", detenido)
    logueado = []
    monkeypatch.setattr(dashboard, "_log_agente_error",
                        lambda *a, **kw: logueado.append(a))

    r = dashboard.run_agent("una pregunta que me arrepenti de hacer")

    assert r["ok"] is True
    assert r["detenido"] is True
    assert logueado == [], "detener no es un error: no va al log de errores"


def test_el_system_prompt_le_dice_al_agente_que_dia_es_hoy(monkeypatch):
    """Sin la fecha, "el margen de junio" es una moneda al aire: el modelo
    elegia el año por su cuenta y consultaba junio del año pasado sin avisar.
    Toda pregunta relativa ("este mes", "el trimestre pasado") depende de esto.
    """
    from datetime import date
    vistos = []

    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None, session_id=None):
        vistos.append(system)
        return {"choices": [{"message": {"role": "assistant", "content": "ok"},
                             "finish_reason": "stop"}]}

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_api)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    orchestrator.run("¿cuánto vendí este mes?", Collector())

    hoy = date.today()
    assert str(hoy) in vistos[0], "el prompt debe traer la fecha de hoy"
    assert str(hoy.year) in vistos[0]


# ── El turno de cierre no puede pedirle herramientas al modelo ────────────────
# Visto el 2026-08-06 en pantalla: el usuario pregunto por sus facturas por
# cobrar y recibio la respuesta correcta SEGUIDA de la sintaxis cruda de cuatro
# tool calls. El cierre es la unica llamada que va con tools=None, pero el
# system prompt le sigue exigiendo publicar en el lienzo: sin el array de tools
# el modelo no puede emitir una llamada de verdad, asi que la escribio en prosa.
# La prueba de que fue el cierro son los nombres de parametro inventados
# (label/value/title/rows en vez de etiqueta/valor/titulo/filas): un modelo con
# el schema al frente no los inventa.

SALIDA_CON_TOOL_CALL_EN_TEXTO = (
    "Tienes **$8.883.587** por cobrar en **55 facturas**.\n"
    '<tool_call>mcp__acciones__publicar_kpi(label="Total por cobrar", '
    'value=8883587, format="currency")</tool_call>'
    '<tool_call>mcp__acciones__publicar_tabla(title="Facturas", '
    'rows=[[4754, "VDT SPA"]])</arg_value></tool_call>'
)


def test_la_instruccion_de_cierre_avisa_que_no_hay_herramientas():
    """La causa raiz es una contradiccion: el system prompt ordena publicar en
    el lienzo y el cierre va sin tools. Si no se lo decimos explicitamente, el
    modelo obedece al prompt y escribe la llamada como texto."""
    instruccion = orchestrator.INSTRUCCION_CIERRE.lower()
    assert "no tienes herramientas" in instruccion
    assert "publicar" in instruccion, "debe nombrar lo que NO puede hacer"
    assert "prosa" in instruccion


def test_el_saneador_borra_la_sintaxis_de_tool_call_y_conserva_la_respuesta():
    limpio = orchestrator._sin_sintaxis_de_tool(SALIDA_CON_TOOL_CALL_EN_TEXTO)

    assert "$8.883.587" in limpio and "55 facturas" in limpio
    assert "<tool_call>" not in limpio
    assert "publicar_kpi" not in limpio and "publicar_tabla" not in limpio
    assert "arg_value" not in limpio


def test_el_saneador_cierra_una_tool_call_truncada_a_la_mitad():
    """Cortada por max_tokens no trae `</tool_call>`: sin el ancla de cierre un
    regex perezoso la dejaria entera en pantalla."""
    limpio = orchestrator._sin_sintaxis_de_tool(
        'La deuda es $8.883.587.\n<tool_call>mcp__lienzo__publicar_tabla(filas=[[47')

    assert limpio == "La deuda es $8.883.587."


def test_el_saneador_no_toca_una_respuesta_normal():
    """Nombrar una herramienta en prosa es legitimo ('use facturas_vencidas'):
    el saneador solo persigue la SINTAXIS, no las menciones."""
    texto = "Consulté las facturas vencidas con mcp__negocio__facturas_vencidas y son 55."
    assert orchestrator._sin_sintaxis_de_tool(texto) == texto


def test_el_cierre_no_le_entrega_al_usuario_la_sintaxis_de_una_tool(monkeypatch):
    """Defensa en profundidad: la instruccion es la causa raiz, pero un modelo
    siempre puede desobedecer y esto NUNCA debe llegar a la pantalla."""
    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None, session_id=None):
        if tools is None:                      # turno de cierre
            return {"choices": [{
                "message": {"role": "assistant",
                            "content": SALIDA_CON_TOOL_CALL_EN_TEXTO},
                "finish_reason": "stop"}]}
        return {"choices": [{                  # un turno normal que no cierra nada
            "message": {"role": "assistant", "content": None,
                        "tool_calls": [{"id": "c1", "type": "function",
                                        "function": {"name": "mcp__inexistente__x",
                                                     "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}]}

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_api)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    texto, _sid = orchestrator.run("que facturas tengo por cobrar", Collector())

    assert "<tool_call>" not in texto
    assert "publicar_kpi" not in texto
    assert "$8.883.587" in texto, "la respuesta util sobrevive"


def test_un_cierre_que_es_puro_tool_call_no_deja_burbuja_vacia(monkeypatch):
    """Si al sanear no queda nada, el usuario merece el mensaje explicito, no
    una burbuja en blanco."""
    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None, session_id=None):
        if tools is None:
            return {"choices": [{
                "message": {"role": "assistant",
                            "content": '<tool_call>mcp__lienzo__publicar_kpi(x=1)</tool_call>'},
                "finish_reason": "stop"}]}
        return {"choices": [{
            "message": {"role": "assistant", "content": None,
                        "tool_calls": [{"id": "c1", "type": "function",
                                        "function": {"name": "mcp__inexistente__x",
                                                     "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}]}

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_api)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    texto, _sid = orchestrator.run("que facturas tengo por cobrar", Collector())

    assert texto == orchestrator.MENSAJE_SIN_PASOS


# ── El SQL ad-hoc tampoco inunda el contexto ──────────────────────────────────
# Medido el 2026-08-07, DESPUES de que las tools de negocio publicaran solas su
# tabla: el modelo esquivo la mejora yendose por mcp__postgres__query, que le
# devolvio las 55 filas en JSON crudo. El prompt de la vuelta siguiente paso de
# 7.029 a 15.029 tokens y el turno se corto igual. La regla tiene que vivir en
# el borde por donde entran los datos, no en cada tool: este es el otro borde,
# y el mas ancho (MAX_FILAS_SQL son 200 filas).

def _filas_falsas(n):
    return [{"folio": 4700 + i, "cliente": f"CLIENTE {i}", "total": 100000 + i}
            for i in range(n)]


def test_un_select_largo_se_guarda_y_al_modelo_le_llega_un_resumen(monkeypatch):
    cur = FakeCursor(description=[("folio",), ("cliente",), ("total",)],
                     rows=_filas_falsas(55))
    _fake_connect(monkeypatch, cur)
    resultados = orchestrator.ResultadosSQL()

    res = orchestrator.ejecutar_sql_local("SELECT * FROM ventas", resultados)

    guardado = resultados.obtener("q1")
    assert guardado["columnas"] == ["folio", "cliente", "total"]
    assert len(guardado["filas"]) == 55, "se guardan TODAS las filas"

    assert "55 filas" in res
    assert "CLIENTE 54" not in res, "el detalle no debe entrar al contexto"
    assert "CLIENTE 0" in res, "pero si una muestra, para que pueda redactar"
    assert "publicar_consulta" in res and 'ref="q1"' in res


def test_publicar_automatico_no_ensucia_el_lienzo(monkeypatch):
    """Medido el 2026-08-07: el modelo hizo 4 SELECT exploratorios para UNA
    pregunta. Publicarlos solos dejaba tres tablas 'Resultado de la consulta'
    encima. Consultar no es querer mostrar: publica el modelo, cuando decide."""
    cur = FakeCursor(description=[("folio",)], rows=_filas_falsas(55))
    _fake_connect(monkeypatch, cur)
    col = Collector()

    orchestrator.ejecutar_sql_local("SELECT * FROM ventas",
                                    orchestrator.ResultadosSQL())

    assert col.items == []


def test_un_select_corto_sigue_llegando_entero_al_modelo(monkeypatch):
    """Bajo el umbral el modelo necesita las filas a mano para razonar, y una
    tabla de 3 filas en el lienzo seria ruido."""
    cur = FakeCursor(description=[("folio",)], rows=_filas_falsas(3))
    _fake_connect(monkeypatch, cur)
    resultados = orchestrator.ResultadosSQL()

    res = orchestrator.ejecutar_sql_local("SELECT * FROM ventas", resultados)

    assert resultados.obtener("q1") is None
    assert "CLIENTE 2" in res


def test_sin_lienzo_el_sql_devuelve_el_json_de_siempre(monkeypatch):
    """ejecutar_sql_local se usa tambien fuera del loop del chat."""
    cur = FakeCursor(description=[("folio",)], rows=_filas_falsas(55))
    _fake_connect(monkeypatch, cur)

    res = orchestrator.ejecutar_sql_local("SELECT * FROM ventas")

    assert "CLIENTE 54" in res


def test_el_loop_le_pasa_el_almacen_de_resultados_a_la_tool_de_sql(monkeypatch):
    """Sin esto el SELECT vuelve a volcar sus filas enteras en el contexto."""
    vistos = []
    monkeypatch.setattr(orchestrator, "ejecutar_sql_local",
                        lambda sql, resultados=None: vistos.append(resultados) or "[]")

    def mock_api(api_key, model, system, messages, tools=None, max_tokens=None, session_id=None):
        if any(m.get("role") == "tool" for m in messages):
            return {"choices": [{"message": {"role": "assistant", "content": "Listo."},
                                 "finish_reason": "stop"}]}
        return {"choices": [{
            "message": {"role": "assistant", "content": None,
                        "tool_calls": [{"id": "c1", "type": "function",
                                        "function": {"name": "mcp__postgres__query",
                                                     "arguments": '{"query": "SELECT 1"}'}}]},
            "finish_reason": "tool_calls"}]}

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_api)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    orchestrator.run("dame el detalle", Collector())

    assert len(vistos) == 1
    assert isinstance(vistos[0], orchestrator.ResultadosSQL)


# ── Telemetria ────────────────────────────────────────────────────────────────
# Hasta el 2026-08-09 el orquestador recibia el bloque `usage` de cada llamada y
# lo tiraba: no habia forma de contestar cuanto cuesta el chat ni si el cache de
# prefijo esta pegando (justo lo que el X-Session-Id intenta proteger).

def test_registra_una_fila_de_telemetria_por_llamada_al_modelo(monkeypatch):
    _guionizar(monkeypatch, [
        _turno("", ["mcp__lienzo__publicar_kpi"]),
        _turno("Te deben $8.883.587.", [], finish="stop"),
    ])
    filas = []
    monkeypatch.setattr(orchestrator.telemetria, "registrar",
                        lambda resp, **kw: filas.append(kw))

    orchestrator.run("cuanto me deben?", Collector())

    assert len(filas) == 2, "una fila por llamada al modelo, no una por turno"
    assert [f["iteracion"] for f in filas] == [1, 2]
    assert len({f["turno_id"] for f in filas}) == 1, "las vueltas comparten turno_id"
    assert filas[0]["pregunta"] == "cuanto me deben?"
    assert filas[0]["modelo"]


def test_la_telemetria_anota_que_tools_pidio_cada_vuelta(monkeypatch):
    """Sin esto no se puede responder que tools son caras ni cuales se usan de
    verdad, que es la mitad del benchmark."""
    _guionizar(monkeypatch, [
        _turno("", ["mcp__lienzo__publicar_kpi", "mcp__lienzo__publicar_tabla"]),
        _turno("listo", [], finish="stop"),
    ])
    filas = []
    monkeypatch.setattr(orchestrator.telemetria, "registrar",
                        lambda resp, **kw: filas.append(kw))

    orchestrator.run("x", Collector())

    assert filas[0]["tools_llamadas"] == ["mcp__lienzo__publicar_kpi",
                                          "mcp__lienzo__publicar_tabla"]
    assert filas[1]["tools_llamadas"] == []


def test_la_telemetria_mide_lo_que_espera_el_usuario(monkeypatch):
    """La latencia se mide alrededor de la llamada completa, reintentos
    incluidos: lo que importa es el tiempo que el usuario mira 'Pensando...'."""
    _guionizar(monkeypatch, [_turno("listo", [], finish="stop")])
    filas = []
    monkeypatch.setattr(orchestrator.telemetria, "registrar",
                        lambda resp, **kw: filas.append(kw))

    orchestrator.run("x", Collector())

    assert filas[0]["latencia_ms"] >= 0


def test_el_turno_de_cierre_tambien_se_registra(monkeypatch):
    """Es una llamada al modelo mas, con presupuesto AMPLIADO (4000 tokens).
    Dejarla fuera subestimaria el costo justo de los turnos mas caros."""
    monkeypatch.setattr(orchestrator, "MAX_ITERACIONES", 1)
    _guionizar(monkeypatch, [_turno("", ["mcp__lienzo__publicar_kpi"])])
    filas = []
    monkeypatch.setattr(orchestrator.telemetria, "registrar",
                        lambda resp, **kw: filas.append(kw))

    orchestrator.run("x", Collector())

    assert len(filas) == 2, "la vuelta del loop y el turno de cierre"
    assert filas[-1]["iteracion"] == 0, "el cierre se marca con iteracion 0"


def test_el_presupuesto_del_loop_alcanza_para_razonar_y_ademas_llamar_tools():
    """Medido el 2026-08-09 con GLM 5.2, la primera pregunta real con telemetria:

        pregunta: "los 5 mejores clientes de barril 30L Cream Ale en 2026"
        completion_tokens = 1500   <- el techo EXACTO
        reasoning_tokens  = 1499   <- gasto TODO el presupuesto pensando
        finish_reason     = length
        tools_llamadas    = []     <- no alcanzo a pedir ni una

    La vuelta se perdio entera y el usuario recibio el turno de cierre ("no
    tengo herramientas disponibles en este momento"), que es lo que
    INSTRUCCION_CIERRE le dice al modelo que responda.

    El proyecto ya conocia este modo de falla y lo habia arreglado para el turno
    de cierre (MAX_TOKENS_CIERRE = 4000), pero dejo el loop en 1500. Mismo bug,
    otro lugar.

    `max_tokens` es un TECHO, no un objetivo: subirlo no gasta mas cuando el
    modelo no lo necesita. Lo que si costaba plata era truncar — 1500 tokens de
    salida pagados por cero resultado, mas el turno de cierre encima.
    """
    assert orchestrator.MAX_TOKENS >= orchestrator.MAX_TOKENS_CIERRE, (
        "la vuelta del loop tiene que poder razonar Y emitir la tool call; el "
        "cierre solo tiene que redactar, asi que nunca puede tener mas holgura")
