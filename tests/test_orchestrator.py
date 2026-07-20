# tests/test_orchestrator.py
import json
import pytest
from app.agent import orchestrator
from app.canvas.artifacts import Collector

def test_ejecutar_sql_local(monkeypatch):
    class FakeCursor:
        description = [("col1",)]
        def execute(self, q):
            pass
        def fetchall(self):
            return [{"col1": "val1"}]
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
            
    class FakeConn:
        def cursor(self):
            return FakeCursor()
        def close(self):
            pass
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr(orchestrator.psycopg2, "connect", lambda *a, **kw: FakeConn())
    res = orchestrator.ejecutar_sql_local("SELECT 1")
    data = json.loads(res)
    assert data == [{"col1": "val1"}]

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
