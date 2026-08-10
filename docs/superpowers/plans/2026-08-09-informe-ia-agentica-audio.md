# Informe de IA agéntica con audio — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generar un MP3 express de las novedades diarias y adjuntarlo al correo Gmail junto al informe escrito.

**Architecture:** El agente programado recopila las fuentes y escribe un guion oral de máximo 420 palabras. `app/briefing/audio.py` valida ese guion y llama a `POST /v1/audio/speech` con `gpt-4o-mini-tts`; `scripts/generar_audio_informe.py` guarda el MP3 temporal para que Gmail lo adjunte. El archivo de audio no se conserva en el repositorio ni se escribe en la base de datos.

**Tech Stack:** Python 3.11+, biblioteca estándar (`urllib`), OpenAI Audio API (`gpt-4o-mini-tts`), Gmail plugin, pytest y Codex thread automation.

## Global Constraints

- Usar exclusivamente `OPENAI_API_KEY` desde `.env`; nunca imprimirla, versionarla ni incluirla en logs.
- Usar la voz integrada `marin`, español neutro y velocidad `1.0`.
- Limitar el guion a 420 palabras y 4.500 caracteres; el audio objetivo dura entre dos y tres minutos.
- El script solo crea el MP3 indicado con `--salida`; no lee ni modifica la base de datos.
- Si OpenAI TTS falla, el informe escrito igual se envía por Gmail sin adjunto y explica que el audio no estuvo disponible.
- Usar `gpt-4o-mini-tts`; presupuesto esperado: US$0,045 por informe de tres minutos, sin incluir el consumo propio de Codex.

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `app/briefing/audio.py` | Validar el guion, construir la petición HTTP y guardar bytes MP3. |
| `scripts/generar_audio_informe.py` | CLI para convertir un archivo UTF-8 de guion a MP3 temporal. |
| `tests/test_briefing_audio.py` | Pruebas sin red: límites, petición, errores y escritura de audio. |
| `.env.example` | Documentar `OPENAI_API_KEY` sin valor real. |
| `.gitignore` | Evitar que MP3 temporales entren a Git. |
| Codex automation | Investigación, guion, ejecución del CLI y envío Gmail con adjunto. |

### Task 1: Módulo testeable para OpenAI TTS

**Files:**

- Create: `app/briefing/audio.py`
- Create: `tests/test_briefing_audio.py`

**Interfaces:**

- Produces: `validar_guion(texto: str) -> str`
- Produces: `generar_mp3(texto: str, salida: Path, urlopen: Callable = urllib.request.urlopen) -> Path`
- Consumes: `OPENAI_API_KEY` desde el entorno cargado por `app.config`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_briefing_audio.py
import json

import pytest

from app.briefing import audio


class RespuestaFalsa:
    def __init__(self, contenido):
        self.contenido = contenido

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.contenido


def test_validar_guion_rechaza_texto_vacio():
    with pytest.raises(ValueError, match="vacío"):
        audio.validar_guion("   ")


def test_validar_guion_rechaza_limite_de_palabras():
    with pytest.raises(ValueError, match="palabras"):
        audio.validar_guion("palabra " * (audio.MAX_PALABRAS + 1))


def test_generar_mp3_guarda_la_respuesta(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "clave_de_prueba")
    solicitud = {}

    def urlopen_falso(request, timeout):
        solicitud["url"] = request.full_url
        solicitud["cuerpo"] = json.loads(request.data.decode("utf-8"))
        solicitud["autorizacion"] = request.get_header("Authorization")
        return RespuestaFalsa(b"ID3mp3-de-prueba")

    salida = audio.generar_mp3("Informe breve.", tmp_path / "informe.mp3", urlopen_falso)

    assert salida.read_bytes() == b"ID3mp3-de-prueba"
    assert solicitud["url"] == audio.OPENAI_TTS_URL
    assert solicitud["autorizacion"] == "Bearer clave_de_prueba"
    assert solicitud["cuerpo"]["model"] == "gpt-4o-mini-tts"
    assert solicitud["cuerpo"]["voice"] == "marin"
    assert solicitud["cuerpo"]["response_format"] == "mp3"


def test_generar_mp3_exige_clave(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        audio.generar_mp3("Informe breve.", tmp_path / "informe.mp3")
```

- [ ] **Step 2: Ejecutar para confirmar el fallo inicial**

Run: `python -m pytest tests/test_briefing_audio.py -v`

Expected: FAIL con `ImportError` porque `app.briefing.audio` aún no existe.

- [ ] **Step 3: Implementar el módulo mínimo sin nuevas dependencias**

```python
# app/briefing/audio.py
"""Generación acotada de audio MP3 para informes breves."""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from app import config  # Carga .env sin exponer sus valores.

OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
MODELO_TTS = "gpt-4o-mini-tts"
VOZ_TTS = "marin"
MAX_PALABRAS = 420
MAX_CARACTERES = 4500
TIMEOUT_SEGUNDOS = 60


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


def generar_mp3(texto: str, salida: Path, urlopen: Callable = urllib.request.urlopen) -> Path:
    """Solicita TTS a OpenAI y escribe el MP3 en la ruta indicada."""
    cuerpo = json.dumps({
        "model": MODELO_TTS,
        "voice": VOZ_TTS,
        "input": validar_guion(texto),
        "instructions": "Habla en español neutro, con ritmo claro y profesional.",
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
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_bytes(contenido)
    return salida
```

- [ ] **Step 4: Ejecutar los tests del módulo**

Run: `python -m pytest tests/test_briefing_audio.py -v`

Expected: PASS (4 tests), sin llamadas a internet.

- [ ] **Step 5: Commit**

```bash
git add app/briefing/audio.py tests/test_briefing_audio.py
git commit -m "Agrega generador de audio para informes express"
```

### Task 2: CLI, configuración segura y archivos temporales

**Files:**

- Create: `scripts/generar_audio_informe.py`
- Modify: `.env.example`
- Modify: `.gitignore`

**Interfaces:**

- Consumes: `python scripts/generar_audio_informe.py --entrada <guion.txt> --salida <informe.mp3>`
- Produces: un MP3 en la ruta `--salida` o código de salida 1 sin dejar un MP3 parcial.

- [ ] **Step 1: Crear el CLI**

```python
# scripts/generar_audio_informe.py
#!/usr/bin/env python3
"""Convierte un guion UTF-8 breve en MP3 mediante OpenAI TTS."""
import argparse
import sys
from pathlib import Path

from _console import force_utf8

force_utf8()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.briefing.audio import generar_mp3  # noqa: E402


def _argumentos(argumentos):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entrada", required=True, type=Path)
    parser.add_argument("--salida", required=True, type=Path)
    return parser.parse_args(argumentos)


def main(argumentos=None):
    args = _argumentos(argumentos or sys.argv[1:])
    if not args.entrada.is_file():
        print(f"No existe el guion: {args.entrada}", file=sys.stderr)
        return 1
    try:
        generar_mp3(args.entrada.read_text(encoding="utf-8"), args.salida)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"No se pudo generar el audio: {error}", file=sys.stderr)
        return 1
    print(args.salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Documentar la variable y excluir MP3**

Add to `.env.example`:

```dotenv
# API de OpenAI para generar el MP3 del informe diario; nunca versionar la clave real.
OPENAI_API_KEY=
```

Add to `.gitignore`:

```gitignore
# Audio temporal generado para correos diarios
*.mp3
```

- [ ] **Step 3: Verificar el error sin clave ni red**

Run: `python scripts/generar_audio_informe.py --entrada no-existe.txt --salida %TEMP%\\informe.mp3`

Expected: código 1 y el mensaje `No existe el guion:`; no se crea el MP3.

- [ ] **Step 4: Ejecutar toda la suite**

Run: `python -m pytest -q`

Expected: PASS; las pruebas nuevas no realizan llamadas de red.

- [ ] **Step 5: Commit**

```bash
git add scripts/generar_audio_informe.py .env.example .gitignore
git commit -m "Agrega comando para crear audio del informe"
```

### Task 3: Prueba real y automatización diaria con Gmail

**Files:**

- Modify: `.env` (local, ignorado por Git; Christian agrega su propia clave)
- Create: `%TEMP%\\zigurat-informe-prueba.txt` (temporal)
- Create: `%TEMP%\\zigurat-informe-prueba.mp3` (temporal)
- Modify: Codex thread automation `Resumen diario de IA agéntica`.

**Interfaces:**

- Consumes: guion oral de 350–420 palabras generado por la investigación diaria.
- Produces: correo a `cdelafue31@gmail.com` con cuerpo Markdown y un adjunto MP3.

- [ ] **Step 1: Configurar la clave sin exponerla**

Christian crea una clave de API con saldo en [OpenAI Platform](https://platform.openai.com/api-keys) y agrega localmente una sola línea al final de `.env`:

```dotenv
OPENAI_API_KEY=clave_creada_en_platform_openai
```

Verificar solo presencia, nunca contenido:

```powershell
if (Select-String -LiteralPath .env -Pattern '^OPENAI_API_KEY=.+') { 'Configurada' } else { 'Falta OPENAI_API_KEY' }
```

- [ ] **Step 2: Preparar y convertir un guion real**

Crear `%TEMP%\\zigurat-informe-prueba.txt` con 350–420 palabras basadas en el informe de IA agéntica del 9 de agosto de 2026, sin URLs leídas en voz alta. Ejecutar:

```powershell
python scripts\generar_audio_informe.py --entrada "$env:TEMP\zigurat-informe-prueba.txt" --salida "$env:TEMP\zigurat-informe-prueba.mp3"
Get-Item "$env:TEMP\zigurat-informe-prueba.mp3" | Select-Object Name, Length
```

Expected: código 0 y un MP3 de tamaño mayor que 0 bytes.

- [ ] **Step 3: Enviar la prueba por Gmail**

Usar Gmail `send_email` con destinatario `me`, asunto `[Prueba] Audio — Informe de IA agéntica`, el resumen escrito y `attachment_files` apuntando al MP3 temporal. Confirmar que se envió exactamente un correo con un MP3 adjunto.

- [ ] **Step 4: Actualizar la automatización diaria**

Actualizar o crear una única automatización diaria a las 08:00 de Santiago con esta secuencia obligatoria:

1. Investigar fuentes primarias y crear el informe escrito de hasta cinco novedades.
2. Crear en `%TEMP%` un guion oral de 350–420 palabras, sin URLs leídas en voz alta.
3. Ejecutar `scripts/generar_audio_informe.py` con `--entrada` y `--salida` temporales.
4. Enviar Gmail a `me`, con el informe como cuerpo y el MP3 como adjunto.
5. Si el script falla, enviar el mismo informe sin adjunto y declarar la falla.
6. Eliminar los dos archivos temporales después del envío, incluso tras un fallo.

- [ ] **Step 5: Verificación final y commit**

Run:

```bash
python -m pytest -q
git status --short
```

Expected: suite verde; `.env`, guion y MP3 no aparecen como archivos versionables; los cambios ajenos siguen sin tocarse.

Commit:

```bash
git add app/briefing/audio.py scripts/generar_audio_informe.py tests/test_briefing_audio.py .env.example .gitignore
git commit -m "Automatiza audio express del informe de IA"
```

## Self-Review

**Cobertura del diseño:** Tasks 1–3 cubren el MP3 express, OpenAI TTS, la clave solo local, Gmail con adjunto, respaldo escrito, prueba real y limpieza temporal. ✅

**Sin placeholders:** los archivos, funciones, comandos, límites, voz, modelo y criterios de prueba están definidos. La clave queda fuera del plan por seguridad y solo Christian la agrega localmente.

**Consistencia de interfaces:** el CLI consume `generar_mp3(texto, salida)`; la automatización recibe el MP3 desde `--salida`; Gmail adjunta esa misma ruta.
