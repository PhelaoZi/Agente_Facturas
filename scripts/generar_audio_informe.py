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
    """Define y procesa los argumentos del comando."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entrada", required=True, type=Path)
    parser.add_argument("--salida", required=True, type=Path)
    return parser.parse_args(argumentos)


def main(argumentos=None):
    """Genera el MP3 y devuelve un código de salida para la automatización."""
    args = _argumentos(sys.argv[1:] if argumentos is None else argumentos)
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
