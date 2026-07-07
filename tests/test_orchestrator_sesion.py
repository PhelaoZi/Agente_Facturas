"""Tests de la memoria de conversación del orquestador (Fase B):
extracción del session_id y reanudación vía resume en las opciones."""
from dataclasses import dataclass, field

from app.agent.orchestrator import _build_options, _extract_session_id
from app.canvas.artifacts import Collector


@dataclass
class _MsgConSesion:
    session_id: str = "abc-123"


@dataclass
class _MsgInit:
    data: dict = field(default_factory=lambda: {"session_id": "init-456"})


@dataclass
class _MsgSinNada:
    content: str = "hola"


def test_extrae_session_id_de_atributo():
    assert _extract_session_id([_MsgSinNada(), _MsgConSesion()]) == "abc-123"


def test_extrae_session_id_de_data_init():
    assert _extract_session_id([_MsgInit()]) == "init-456"


def test_sin_session_id_devuelve_none():
    assert _extract_session_id([_MsgSinNada()]) is None


def test_opciones_sin_sesion_no_reanudan():
    opts = _build_options(Collector())
    assert opts.resume is None


def test_opciones_con_sesion_reanudan():
    opts = _build_options(Collector(), "abc-123")
    assert opts.resume == "abc-123"


def test_opciones_aisladas_y_deterministas():
    # Invariantes de la Fase A: no deben perderse al agregar memoria.
    opts = _build_options(Collector())
    assert opts.setting_sources == []
    assert opts.strict_mcp_config is True
    assert opts.model == "sonnet"
