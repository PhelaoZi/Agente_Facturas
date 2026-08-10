import subprocess
import sys
from pathlib import Path


RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
SCRIPT_AUDIO = RAIZ_PROYECTO / "scripts" / "generar_audio_informe.py"


def test_cli_rechaza_guion_inexistente_sin_crear_audio(tmp_path):
    """Evita una llamada TTS y un MP3 vacío si el guion no está disponible."""
    salida = tmp_path / "informe.mp3"
    resultado = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_AUDIO),
            "--entrada",
            str(tmp_path / "no-existe.txt"),
            "--salida",
            str(salida),
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        text=True,
    )

    assert resultado.returncode == 1
    assert "No existe el guion:" in resultado.stderr
    assert not salida.exists()
