# tests/test_dashboard_seguridad.py
"""Protección anti-CSRF de los endpoints POST del dashboard.

El servidor escucha solo en 127.0.0.1, pero cualquier página web abierta en el
navegador puede hacer POST a http://localhost:8777 (CSRF). `origen_permitido`
rechaza esos requests: exige Host local y, si el navegador manda Origin, que
sea el del propio dashboard.
"""
from app.dashboard import PORT, origen_permitido

HOST_OK = f"localhost:{PORT}"
ORIGIN_OK = f"http://localhost:{PORT}"


def test_acepta_host_local_sin_origin():
    # curl / scripts locales no mandan Origin: deben poder seguir funcionando.
    assert origen_permitido(HOST_OK, None) is True
    assert origen_permitido(f"127.0.0.1:{PORT}", None) is True


def test_acepta_origin_del_propio_dashboard():
    assert origen_permitido(HOST_OK, ORIGIN_OK) is True
    assert origen_permitido(f"127.0.0.1:{PORT}", f"http://127.0.0.1:{PORT}") is True


def test_rechaza_origin_de_otro_sitio():
    # Un POST cross-origin desde una web maliciosa siempre trae su Origin.
    assert origen_permitido(HOST_OK, "https://evil.example") is False
    assert origen_permitido(HOST_OK, "null") is False


def test_rechaza_host_ajeno():
    # DNS rebinding: el atacante apunta su dominio a 127.0.0.1.
    assert origen_permitido(f"evil.example:{PORT}", None) is False
    assert origen_permitido("", None) is False
    assert origen_permitido(None, None) is False


# ── Precarga del SDK de agentes ───────────────────────────────────────────────
# `import claude_agent_sdk` tarda ~6s (arrastra mcp + jsonschema) y estaba
# escrito DENTRO de los build_*_server, o sea que se pagaba durante la PRIMERA
# pregunta, con el usuario esperando. Precargarlo al arrancar mueve esos
# segundos a donde no molestan. Provisorio: si algun dia se saca la dependencia
# del SDK, esto sobra.

def test_precalentar_sdk_deja_el_modulo_cargado():
    import sys
    from app import dashboard

    dashboard.precalentar_sdk()

    assert "claude_agent_sdk" in sys.modules, "tras precalentar, el import debe ser gratis"


def test_precalentar_sdk_nunca_voltea_el_dashboard(monkeypatch):
    """Si la precarga falla (dependencia rota, instalacion a medias), el
    dashboard tiene que abrir igual: la primera pregunta reintentara el import
    como hacia antes. Un panel que no levanta es peor que uno lento."""
    import builtins
    from app import dashboard

    real_import = builtins.__import__

    def import_roto(nombre, *a, **kw):
        if nombre == "claude_agent_sdk":
            raise ImportError("simulacion: instalacion rota")
        return real_import(nombre, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", import_roto)

    dashboard.precalentar_sdk()   # no debe levantar excepcion
