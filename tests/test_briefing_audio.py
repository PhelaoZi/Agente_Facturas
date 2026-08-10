import json

import pytest

from app.briefing import audio


class RespuestaFalsa:
    """Respuesta mínima de OpenAI para probar la escritura local."""

    def __init__(self, contenido):
        self.contenido = contenido

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.contenido


def test_validar_guion_rechaza_texto_vacio():
    """Evita enviar una petición TTS sin contenido útil."""
    with pytest.raises(ValueError, match="vacío"):
        audio.validar_guion("   ")


def test_validar_guion_rechaza_mas_de_420_palabras():
    """Protege la duración objetivo de dos a tres minutos."""
    with pytest.raises(ValueError, match="palabras"):
        audio.validar_guion("palabra " * 421)


def test_generar_mp3_guarda_respuesta_y_envia_configuracion_esperada(monkeypatch, tmp_path):
    """Detecta un cambio en el contrato HTTP o en los bytes que se guardan."""
    monkeypatch.setenv("OPENAI_API_KEY", "clave_de_prueba")
    solicitud = {}

    def urlopen_falso(request, timeout):
        solicitud["url"] = request.full_url
        solicitud["cuerpo"] = json.loads(request.data.decode("utf-8"))
        solicitud["autorizacion"] = request.get_header("Authorization")
        solicitud["timeout"] = timeout
        return RespuestaFalsa(b"ID3mp3-de-prueba")

    salida = audio.generar_mp3("Informe breve.", tmp_path / "informe.mp3", urlopen_falso)

    assert salida.read_bytes() == b"ID3mp3-de-prueba"
    assert solicitud["url"] == "https://api.openai.com/v1/audio/speech"
    assert solicitud["autorizacion"] == "Bearer clave_de_prueba"
    assert solicitud["timeout"] == 60
    assert solicitud["cuerpo"] == {
        "model": "gpt-4o-mini-tts",
        "voice": "marin",
        "input": "Informe breve.",
        "instructions": "Habla en español neutro, con ritmo claro y profesional.",
        "response_format": "mp3",
        "speed": 1.0,
    }


def test_generar_mp3_exige_clave(monkeypatch, tmp_path):
    """Evita una llamada de red accidental si falta la credencial local."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        audio.generar_mp3("Informe breve.", tmp_path / "informe.mp3")
