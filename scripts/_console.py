"""Utilidad de consola compartida por los scripts del pipeline.

force_utf8() reconfigura stdout/stderr a UTF-8 para que los scripts puedan
imprimir simbolos (checkmarks, flechas, advertencias) sin lanzar
UnicodeEncodeError en consolas Windows con codificacion cp1252.

Se llama al inicio de cada script en vez de depender de la variable de
entorno PYTHONIOENCODING, que no siempre esta fijada en la maquina del
usuario.
"""
import sys


def force_utf8() -> None:
    """Fuerza UTF-8 en stdout/stderr. No-op seguro si el stream no lo soporta."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # AttributeError: el stream no es un TextIOWrapper (p.ej. captura de tests).
            # ValueError: stream ya cerrado o en un estado que no admite reconfigure.
            pass
