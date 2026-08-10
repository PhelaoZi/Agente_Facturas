# -*- coding: utf-8 -*-
"""Telemetria del agente: que cuesta y como trabaja cada turno.

Hasta el 2026-08-09 el orquestador recibia el bloque `usage` de cada llamada y
lo tiraba. No se podia contestar cuanto cuesta el chat ni si el cache esta
pegando — y el `X-Session-Id` se agrego justamente para proteger ese cache, sin
forma de saber si funciono.
"""
from app.agent import telemetria


def _respuesta(usage=None, provider="Fireworks"):
    r = {"choices": [{"message": {"content": "hola"}, "finish_reason": "stop"}]}
    if usage is not None:
        r["usage"] = usage
    if provider is not None:
        r["provider"] = provider
    return r


def test_extrae_los_tokens_basicos():
    uso = telemetria.extraer_uso(_respuesta(
        {"prompt_tokens": 7029, "completion_tokens": 150}))

    assert uso["prompt_tokens"] == 7029
    assert uso["completion_tokens"] == 150


def test_extrae_los_cached_tokens_que_vienen_anidados():
    """La metrica que decide si el cache de prefijo funciona. Viene dentro de
    prompt_tokens_details, no al nivel de arriba."""
    uso = telemetria.extraer_uso(_respuesta(
        {"prompt_tokens": 7029, "prompt_tokens_details": {"cached_tokens": 5100}}))

    assert uso["cached_tokens"] == 5100


def test_extrae_los_reasoning_tokens():
    """GLM 5.2 (el modelo por defecto) gasta tokens PENSANDO antes de escribir, y
    cuentan contra max_tokens. Es la causa de los turnos que se cortan sin
    texto."""
    uso = telemetria.extraer_uso(_respuesta(
        {"completion_tokens": 900, "completion_tokens_details": {"reasoning_tokens": 750}}))

    assert uso["reasoning_tokens"] == 750


def test_extrae_el_proveedor_real_que_atendio():
    """OpenRouter elige proveedor por llamada. Un salto entre vueltas rompe el
    cache: sin este dato no se puede diagnosticar."""
    uso = telemetria.extraer_uso(_respuesta({}, provider="CoreWeave"))

    assert uso["proveedor"] == "CoreWeave"


def test_extrae_el_costo_cuando_el_proveedor_lo_informa():
    """Se le pide el costo a OpenRouter en vez de mantener una tabla de precios
    propia: una lista de precios pegada en el codigo se desincroniza en
    silencio (misma leccion que PRECIOS_VENTA_NETO)."""
    uso = telemetria.extraer_uso(_respuesta({"cost": 0.00123}))

    assert uso["costo_usd"] == 0.00123


def test_una_respuesta_sin_usage_no_revienta():
    """Los proveedores varian y algunos no mandan usage. Perder el dato es
    aceptable; perder la respuesta del agente no."""
    uso = telemetria.extraer_uso(_respuesta(None, provider=None))

    assert uso["prompt_tokens"] is None
    assert uso["cached_tokens"] is None
    assert uso["costo_usd"] is None


def test_guarda_el_usage_crudo_completo():
    """Se guarda tal cual lo que mando el proveedor: si manana agrega un campo
    util, ya esta registrado y no hay que reprocesar nada."""
    crudo = {"prompt_tokens": 10, "campo_nuevo_del_proveedor": "x"}

    uso = telemetria.extraer_uso(_respuesta(crudo))

    assert uso["usage_crudo"] == crudo


def test_el_finish_reason_queda_registrado():
    """`length` es la huella de un turno que se corto sin terminar."""
    r = _respuesta({"prompt_tokens": 1})
    r["choices"][0]["finish_reason"] = "length"

    assert telemetria.extraer_uso(r)["finish_reason"] == "length"


# ── Escritura ─────────────────────────────────────────────────────────────────
# El invariante del modulo: medir nunca puede costar una respuesta.

class _CursorFalso:
    def __init__(self):
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _ConexionFalsa:
    def __init__(self, cur):
        self._cur = cur
        self.cerrada = False

    def cursor(self):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        self.cerrada = True


ARGS = dict(session_id="s1", turno_id="t1", iteracion=2, modelo="z-ai/glm-5.2",
            pregunta="cuanto vendimos?", latencia_ms=3400,
            tools_llamadas=["mcp__negocio__ventas_total"])


def test_registrar_guarda_los_campos_extraidos(monkeypatch):
    cur = _CursorFalso()
    conn = _ConexionFalsa(cur)
    monkeypatch.setattr(telemetria, "_conectar", lambda: conn)

    telemetria.registrar(_respuesta({"prompt_tokens": 7029, "completion_tokens": 150,
                                     "prompt_tokens_details": {"cached_tokens": 5100}}),
                         **ARGS)

    assert "INSERT INTO chat_telemetria" in cur.sql
    assert 7029 in cur.params and 5100 in cur.params
    assert "t1" in cur.params and 3400 in cur.params
    assert conn.cerrada, "la conexion se cierra siempre"


def test_registrar_con_la_base_caida_no_revienta_el_turno(monkeypatch, capsys):
    """Postgres caido = se pierde el dato de medicion, NO la respuesta del
    agente. Si esto lanzara, medir seria mas caro que no medir."""
    def explota():
        raise RuntimeError("Postgres caído")
    monkeypatch.setattr(telemetria, "_conectar", explota)

    telemetria.registrar(_respuesta({"prompt_tokens": 1}), **ARGS)   # no lanza

    assert "telemetr" in capsys.readouterr().out.lower(), "el fallo se avisa, no se silencia"


def test_registrar_con_una_respuesta_deforme_no_revienta(monkeypatch):
    """Si un proveedor cambia el formato, tampoco puede costar el turno."""
    monkeypatch.setattr(telemetria, "_conectar", lambda: _ConexionFalsa(_CursorFalso()))

    telemetria.registrar("esto no es una respuesta", **ARGS)         # no lanza


def test_ningun_test_escribe_telemetria_en_la_base_real(monkeypatch):
    """chat_telemetria es la base de las decisiones de coste y de modelo: si la
    suite le mete filas inventadas, cada promedio queda contaminado.

    Visto el 2026-08-09, el mismo dia que se creo la tabla: los tests del
    orquestador que no falsean `registrar` escribieron 164 filas reales, con
    preguntas como 'pregunta larga' y 'pregunta cara'. Es exactamente el bug que
    ese mismo dia se arreglo en logs/sync_nube.log — un modulo que alcanza un
    recurso compartido de produccion desde los tests.
    """
    llegadas = []
    monkeypatch.setattr(telemetria.psycopg2, "connect",
                        lambda *a, **k: llegadas.append(1))

    telemetria.registrar(_respuesta({"prompt_tokens": 1}), **ARGS)

    assert llegadas == [], "un test alcanzo psycopg2.connect desde la telemetria"
