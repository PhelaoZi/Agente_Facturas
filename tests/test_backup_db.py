"""Tests de la lógica pura de scripts/backup_db.py (sin BD ni pg_dump reales)."""
import json
from datetime import date, datetime

import pytest

from scripts.backup_db import (
    archivos_a_borrar,
    fecha_de_nombre,
    localizar_pg_dump,
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


# --- archivos_a_borrar (retención) --------------------------------------------

def test_retencion_conserva_recientes():
    hoy = date(2026, 6, 11)
    nombres = ["zigurat_dte_2026-06-01_2300.dump"]  # 10 días de antigüedad
    assert archivos_a_borrar(nombres, hoy) == []


def test_retencion_borra_viejos_que_no_son_primeros_del_mes():
    hoy = date(2026, 6, 11)
    nombres = [
        "zigurat_dte_2026-03-05_2300.dump",  # más antiguo de marzo: se conserva
        "zigurat_dte_2026-03-20_2300.dump",  # viejo y no primero: se borra
    ]
    assert archivos_a_borrar(nombres, hoy) == ["zigurat_dte_2026-03-20_2300.dump"]


def test_retencion_conserva_primero_de_cada_mes():
    hoy = date(2026, 6, 11)
    nombres = [
        "zigurat_dte_2026-01-02_2300.dump",
        "zigurat_dte_2026-02-01_2300.dump",
        "zigurat_dte_2026-03-01_2300.dump",
    ]
    assert archivos_a_borrar(nombres, hoy) == []


def test_retencion_mismo_dia_conserva_solo_el_mas_antiguo_del_mes():
    hoy = date(2026, 12, 31)
    nombres = [
        "zigurat_dte_2026-03-01_0900.dump",
        "zigurat_dte_2026-03-01_2300.dump",
    ]
    assert archivos_a_borrar(nombres, hoy) == ["zigurat_dte_2026-03-01_2300.dump"]


def test_retencion_nunca_borra_nombres_desconocidos():
    hoy = date(2030, 1, 1)
    assert archivos_a_borrar(["notas.txt", "_estado.json"], hoy) == []


# --- localizar_pg_dump ----------------------------------------------------------

def test_localizar_pg_dump_prioriza_env(tmp_path, monkeypatch):
    falso = tmp_path / "pg_dump.exe"
    falso.write_bytes(b"")
    monkeypatch.setenv("PG_DUMP_PATH", str(falso))
    assert localizar_pg_dump(base=tmp_path / "no-existe") == falso


def test_localizar_pg_dump_env_roto_falla(tmp_path, monkeypatch):
    monkeypatch.setenv("PG_DUMP_PATH", str(tmp_path / "no-existe.exe"))
    with pytest.raises(FileNotFoundError, match="PG_DUMP_PATH"):
        localizar_pg_dump(base=tmp_path)


def test_localizar_pg_dump_elige_version_mas_alta(tmp_path, monkeypatch):
    monkeypatch.delenv("PG_DUMP_PATH", raising=False)
    for version in ("9", "15", "16"):
        bin_dir = tmp_path / version / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "pg_dump.exe").write_bytes(b"")
    assert localizar_pg_dump(base=tmp_path) == tmp_path / "16" / "bin" / "pg_dump.exe"


def test_localizar_pg_dump_sin_nada_falla(tmp_path, monkeypatch):
    monkeypatch.delenv("PG_DUMP_PATH", raising=False)
    monkeypatch.setattr("scripts.backup_db.shutil.which", lambda _: None)
    with pytest.raises(FileNotFoundError, match="No se encontró pg_dump"):
        localizar_pg_dump(base=tmp_path / "vacio")
