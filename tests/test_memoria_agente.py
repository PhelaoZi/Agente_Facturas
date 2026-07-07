"""Tests de la memoria persistente del agente (memoria-agente/):
guardar/leer notas, índice compacto y su inyección en el orquestador."""
import pytest

from app.agent import memoria


@pytest.fixture
def memoria_tmp(tmp_path, monkeypatch):
    """Redirige la memoria a un directorio temporal para no tocar la real."""
    monkeypatch.setattr(memoria, "MEMORIA_DIR", tmp_path)
    monkeypatch.setattr(memoria, "NOTAS_DIR", tmp_path / "notas")
    monkeypatch.setattr(memoria, "INDICE", tmp_path / "MEMORIA.md")
    return tmp_path


def test_guardar_crea_nota_e_indice(memoria_tmp):
    msg = memoria.guardar_nota("Barril PET sin logística",
                               "Los barriles PET no llevan ítem de logística.",
                               "negocio")
    assert "guardada" in msg
    assert (memoria_tmp / "notas" / "barril-pet-sin-logistica.md").exists()
    indice = memoria.leer_indice()
    assert "Barril PET sin logística" in indice
    assert "[negocio]" in indice


def test_guardar_mismo_titulo_actualiza_sin_duplicar_indice(memoria_tmp):
    memoria.guardar_nota("Regla X", "versión uno")
    msg = memoria.guardar_nota("Regla X", "versión dos")
    assert "actualizada" in msg
    detalle = memoria.leer_nota("Regla X")
    assert "versión uno" in detalle and "versión dos" in detalle
    # El índice mantiene UNA sola línea para la nota
    assert memoria.leer_indice().count("(regla-x)") == 1


def test_leer_nota_inexistente_devuelve_none(memoria_tmp):
    assert memoria.leer_nota("no-existe") is None


def test_nota_sin_titulo_o_contenido_falla(memoria_tmp):
    with pytest.raises(ValueError):
        memoria.guardar_nota("", "algo")
    with pytest.raises(ValueError):
        memoria.guardar_nota("algo", "  ")


def test_tipo_invalido_cae_a_negocio(memoria_tmp):
    memoria.guardar_nota("Nota rara", "contenido", tipo="inventado")
    assert "[negocio]" in memoria.leer_indice()


def test_indice_se_trunca_al_tope(memoria_tmp, monkeypatch):
    monkeypatch.setattr(memoria, "MAX_INDICE_CHARS", 100)
    for i in range(10):
        memoria.guardar_nota(f"Nota {i}", "contenido largo " * 10)
    indice = memoria.leer_indice()
    assert len(indice) < 200
    assert "truncado" in indice


def test_orquestador_inyecta_indice_y_tools(memoria_tmp, monkeypatch):
    from app.agent.orchestrator import _build_options
    from app.canvas.artifacts import Collector

    memoria.guardar_nota("Cliente VDT paga a 45 días", "Confirmado por Christian.")
    opts = _build_options(Collector())
    assert "aprendida en sesiones anteriores" in opts.system_prompt
    assert "Cliente VDT paga a 45 días" in opts.system_prompt
    assert "mcp__memoria__guardar_nota" in opts.allowed_tools
    assert "mcp__memoria__leer_nota" in opts.allowed_tools
    assert "memoria" in opts.mcp_servers


def test_orquestador_sin_memoria_no_agrega_seccion(memoria_tmp):
    from app.agent.orchestrator import _build_options
    from app.canvas.artifacts import Collector

    opts = _build_options(Collector())
    assert "aprendida en sesiones anteriores" not in opts.system_prompt
