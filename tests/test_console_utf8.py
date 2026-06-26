# tests/test_console_utf8.py
"""Regresión del papercut de codificación: imprimir símbolos (✓, →, ⚠) en una
consola Windows cp1252 lanzaba UnicodeEncodeError y rompía el pipeline DTE.
force_utf8() reconfigura stdout/stderr a UTF-8 para evitarlo.

Se prueba con subprocesos forzando PYTHONIOENCODING=cp1252 (reproducible en
cualquier plataforma, no solo Windows)."""
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
CHECK = "✓"  # ✓ — fuera de cp1252


def _run(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"  # fuerza la consola "problemática"
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def test_sin_force_utf8_falla_en_cp1252():
    """Demuestra el bug que el fix corrige: sin reconfigurar, cp1252 revienta."""
    r = _run(f"print('{CHECK}')")
    assert r.returncode != 0
    assert "UnicodeEncodeError" in r.stderr


def test_force_utf8_permite_imprimir_simbolos_en_cp1252():
    """Con force_utf8() el mismo print no lanza error y sale con código 0."""
    code = (
        f"import sys; sys.path.insert(0, r'{SCRIPTS}'); "
        f"from _console import force_utf8; force_utf8(); "
        f"print('{CHECK} ok')"
    )
    r = _run(code)
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_force_utf8_es_seguro_si_stdout_no_soporta_reconfigure():
    """No debe fallar cuando stdout fue reemplazado por algo sin reconfigure()."""
    code = (
        f"import sys, io; sys.path.insert(0, r'{SCRIPTS}'); "
        f"sys.stdout = io.StringIO(); "  # StringIO no tiene reconfigure()
        f"from _console import force_utf8; force_utf8(); "
        f"sys.stderr.write('sin-crash')"
    )
    r = _run(code)
    assert r.returncode == 0, r.stderr
    assert "sin-crash" in r.stderr
