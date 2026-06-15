"""Tests de la lógica pura de scripts/backup_db.py (sin BD ni pg_dump reales)."""
import json
from datetime import date, datetime

import pytest

from scripts.backup_db import (
    fecha_de_nombre,
    nombre_dump,
)


# --- nombre_dump -------------------------------------------------------------

def test_nombre_dump_formato():
    momento = datetime(2026, 6, 11, 23, 0)
    assert nombre_dump(momento) == "zigurat_dte_2026-06-11_2300.dump"


# --- fecha_de_nombre ---------------------------------------------------------

def test_fecha_de_nombre_valido():
    assert fecha_de_nombre("zigurat_dte_2026-06-11_2300.dump") == date(2026, 6, 11)


def test_fecha_de_nombre_invalido_devuelve_none():
    assert fecha_de_nombre("_estado.json") is None
    assert fecha_de_nombre("otro_backup_2026-06-11.dump") is None
    assert fecha_de_nombre("zigurat_dte_2026-06-11_2300.dump.part") is None
