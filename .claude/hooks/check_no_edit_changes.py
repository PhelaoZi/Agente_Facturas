#!/usr/bin/env python3
"""
check_no_edit_changes.py — Zigurat ERP Hook
Bloquea ediciones manuales a changes.json.
Este archivo es generado por parse_dte.py y no debe editarse directamente.
"""

import json
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    file_path = (
        tool_input.get("file_path", "")
        or tool_input.get("path", "")
    )

    if "changes.json" in file_path:
        print(
            "BLOQUEADO: changes.json es generado automáticamente por parse_dte.py.\n"
            "No editar manualmente — cualquier cambio se perderá en el próximo parse.\n"
            "Si necesitas reprocesar, ejecuta: /sync-facturas DTE_DDMMYYYY",
            file=sys.stderr,
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
