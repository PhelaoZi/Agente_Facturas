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


# ── El agente no depende de ningun SDK externo ────────────────────────────────
# Hasta el 2026-08-09 el dashboard precalentaba `claude_agent_sdk` en un hilo al
# arrancar, porque su import tardaba ~6s (arrastra mcp + jsonschema) y se pagaba
# durante la PRIMERA pregunta, con el usuario esperando. Del SDK solo se usaba
# el decorador @tool; ahora las tools se declaran con app/agent/tools_base.py y
# no hay nada que precalentar.

def test_el_agente_no_importa_ningun_sdk_externo():
    """Si alguien reintroduce la dependencia, vuelve el retardo de arranque y
    vuelve el atajo que marcaba TODOS los parametros como obligatorios."""
    import sys

    for modulo in ("app.agent.orchestrator", "app.agent.tools_negocio",
                   "app.agent.tools_acciones", "app.agent.publish_tools",
                   "app.agent.memoria", "app.agent.tools_base"):
        __import__(modulo)

    assert "claude_agent_sdk" not in sys.modules
    assert "mcp" not in sys.modules
