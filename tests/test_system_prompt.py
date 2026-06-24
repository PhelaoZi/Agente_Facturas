# tests/test_system_prompt.py
from app.agent.system_prompt import SYSTEM_PROMPT


def test_incluye_reglas_sql_criticas():
    assert "COALESCE" in SYSTEM_PROMPT
    assert "'61'" in SYSTEM_PROMPT  # excluir notas de crédito en sumas


def test_instruye_publicar_artefactos():
    assert "publicar_grafico" in SYSTEM_PROMPT
    assert "publicar_kpi" in SYSTEM_PROMPT


def test_responde_en_espanol():
    assert "español" in SYSTEM_PROMPT.lower()


def test_system_prompt_menciona_herramientas_de_negocio():
    from app.agent.system_prompt import SYSTEM_PROMPT
    assert "mcp__negocio__" in SYSTEM_PROMPT
    assert "deuda_total" in SYSTEM_PROMPT
    assert "flujo_caja" in SYSTEM_PROMPT


def test_system_prompt_incluye_regla_de_gastos():
    from app.agent.system_prompt import SYSTEM_PROMPT
    assert "proponer_gasto" in SYSTEM_PROMPT
    # Debe dejar claro que NO afirme que quedó registrado:
    assert "registrad" in SYSTEM_PROMPT.lower()


def test_system_prompt_menciona_acciones_de_gasto():
    from app.agent.system_prompt import SYSTEM_PROMPT
    assert "listar_gastos" in SYSTEM_PROMPT
    assert "proponer_borrar_gasto" in SYSTEM_PROMPT
    assert "proponer_editar_gasto" in SYSTEM_PROMPT
    assert "proponer_marcar_gasto_pagado" in SYSTEM_PROMPT
