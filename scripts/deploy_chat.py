#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deploy_chat.py — Zigurat ERP
Publica la edge function `chat` (el chat del teléfono) en InsForge.

Uso:
    python scripts/deploy_chat.py

Por qué existe
--------------
El 2026-08-16 este deploy se hizo a mano así:

    deno bundle -o nube/dist/chat.bundle.js functions/chat.ts | tail -2 \\
        && npx @insforge/cli functions deploy chat --file nube/dist/chat.bundle.js

El bundle **falló** (un backtick dentro del template literal del prompt) y el
deploy dijo "success": subió el `chat.bundle.js` de la corrida anterior, que
seguía en disco. El chat quedó desplegado con la versión vieja y todo parecía
bien. Dos trampas a la vez: el `| tail` se come el código de salida de `deno`, y
un artefacto viejo en disco es indistinguible de uno recién construido.

Acá el bundle viejo se BORRA antes de construir. Si el bundle falla no hay
archivo que subir, y el deploy no corre.
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from _console import force_utf8
except ImportError:
    from scripts._console import force_utf8

# La salida de `deno` y del CLI trae emoji; la consola de Windows es cp1252 y
# revienta al imprimirlos.
force_utf8()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FUENTE = PROJECT_ROOT / "functions" / "chat.ts"
BUNDLE = PROJECT_ROOT / "nube" / "dist" / "chat.bundle.js"
TIMEOUT_SEG = 300
MINIMO_BYTES = 10_000          # el bundle real pesa ~155 KB


def correr(cmd, que_es):
    """Ejecuta y muestra la salida. Devuelve True solo si terminó bien.

    El ejecutable se resuelve con `which` porque en Windows `npx` es un `.cmd` y
    subprocess no lo encuentra por nombre. Se resuelve en vez de usar
    `shell=True`, que además traería problemas de comillas con las rutas del
    proyecto (tienen espacios).
    """
    print(f"\n> {que_es}")
    ejecutable = shutil.which(cmd[0])
    if not ejecutable:
        print(f"ERROR: no encuentro '{cmd[0]}' en el PATH.")
        return False
    cmd = [ejecutable, *cmd[1:]]
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), text=True,
                          capture_output=True, timeout=TIMEOUT_SEG,
                          encoding="utf-8", errors="replace")
    salida = (proc.stdout or "") + (proc.stderr or "")
    print(salida.strip()[-1500:])
    return proc.returncode == 0


def main():
    if not FUENTE.exists():
        print(f"ERROR: no existe {FUENTE}")
        return 1

    # Borrar ANTES de construir: sin esto, un bundle fallido deja el anterior en
    # disco y el deploy lo sube como si fuera nuevo.
    BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    BUNDLE.unlink(missing_ok=True)

    if not correr(["deno", "bundle", "--platform=deno", "-o", str(BUNDLE),
                   str(FUENTE)], "Construyendo el bundle"):
        print("\nERROR: el bundle falló. No se despliega nada.")
        return 1

    if not BUNDLE.exists() or BUNDLE.stat().st_size < MINIMO_BYTES:
        tamano = BUNDLE.stat().st_size if BUNDLE.exists() else 0
        print(f"\nERROR: el bundle quedó en {tamano} bytes "
              f"(mínimo esperado {MINIMO_BYTES:,}). No se despliega nada.")
        return 1

    print(f"\nBundle OK: {BUNDLE.stat().st_size:,} bytes")

    if not correr(["npx", "@insforge/cli", "functions", "deploy", "chat",
                   "--file", str(BUNDLE)], "Desplegando en InsForge"):
        print("\nERROR: el deploy falló.")
        return 1

    print("\nListo: el chat del teléfono quedó con esta versión.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
