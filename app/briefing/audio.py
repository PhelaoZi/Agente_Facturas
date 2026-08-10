"""Generación acotada de audio MP3 para informes breves."""
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from app import config  # noqa: F401  Carga .env sin exponer sus valores.


OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
MODELO_TTS = "gpt-4o-mini-tts"
VOZ_TTS = "marin"
MAX_PALABRAS = 420
MAX_CARACTERES = 4500
TIMEOUT_SEGUNDOS = 60
INSTRUCCIONES_VOZ = "Habla en español neutro, con ritmo claro y profesional."


def validar_guion(texto: str) -> str:
    """Normaliza y limita el guion para el informe express."""
    guion = " ".join(texto.split())
    if not guion:
        raise ValueError("El guion de audio está vacío.")
    if len(guion.split()) > MAX_PALABRAS:
        raise ValueError(f"El guion supera el máximo de {MAX_PALABRAS} palabras.")
    if len(guion) > MAX_CARACTERES:
        raise ValueError(f"El guion supera el máximo de {MAX_CARACTERES} caracteres.")
    return guion


def _clave_api() -> str:
    """Obtiene la clave configurada sin incluirla en errores."""
    clave = os.environ.get("OPENAI_API_KEY", "").strip()
    if not clave:
        raise RuntimeError("Falta OPENAI_API_KEY en el archivo .env.")
    return clave


def _guardar_mp3_atomico(salida: Path, contenido: bytes) -> None:
    """Reemplaza el destino solo después de escribir el MP3 completo."""
    salida.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, dir=salida.parent, suffix=".part") as temporal:
        temporal.write(contenido)
        ruta_temporal = Path(temporal.name)
    try:
        ruta_temporal.replace(salida)
    except OSError:
        ruta_temporal.unlink(missing_ok=True)
        raise


def generar_mp3(texto: str, salida: Path, urlopen: Callable = urllib.request.urlopen) -> Path:
    """Solicita TTS a OpenAI y escribe el MP3 en la ruta indicada."""
    cuerpo = json.dumps({
        "model": MODELO_TTS,
        "voice": VOZ_TTS,
        "input": validar_guion(texto),
        "instructions": INSTRUCCIONES_VOZ,
        "response_format": "mp3",
        "speed": 1.0,
    }).encode("utf-8")
    solicitud = urllib.request.Request(
        OPENAI_TTS_URL,
        data=cuerpo,
        headers={"Authorization": f"Bearer {_clave_api()}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(solicitud, timeout=TIMEOUT_SEGUNDOS) as respuesta:
            contenido = respuesta.read()
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"OpenAI TTS respondió HTTP {error.code}.") from error
    except urllib.error.URLError as error:
        raise RuntimeError("No se pudo conectar con OpenAI TTS.") from error
    _guardar_mp3_atomico(salida, contenido)
    return salida
