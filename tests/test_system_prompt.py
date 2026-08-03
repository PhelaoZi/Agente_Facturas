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


def test_system_prompt_incluye_gerente_comercial():
    from app.agent.system_prompt import SYSTEM_PROMPT
    assert "clientes_en_riesgo" in SYSTEM_PROMPT
    assert "listar_seguimiento" in SYSTEM_PROMPT
    assert "proponer_agregar_seguimiento" in SYSTEM_PROMPT
    assert "proponer_marcar_seguimiento" in SYSTEM_PROMPT


def test_el_prompt_prohibe_resolver_un_incobrable_marcando_la_factura_pagada():
    """Le pasó de verdad (2026-08-02): ante 'bier bar quebró, que no figure en
    las deudas', el agente ofreció marcar la factura como PAGADA. Eso inventa
    plata que nunca entró, infla la cobranza histórica y ensucia el promedio de
    días de pago con que se proyecta el flujo de caja. El agente no lee
    CLAUDE.md: si la regla no está acá, no existe para él.
    """
    from app.agent.system_prompt import SYSTEM_PROMPT
    assert "proponer_marcar_cliente_incobrable" in SYSTEM_PROMPT
    assert "proponer_reactivar_cliente" in SYSTEM_PROMPT
    assert "castigar no es cobrar" in SYSTEM_PROMPT.lower()
    assert "contador" in SYSTEM_PROMPT.lower()


def test_el_prompt_prohibe_calcular_precios_sobre_productos():
    """La linea `Logistica` exacta no se guarda en `productos`, asi que
    cualquier precio deducido con SQL a mano sale a la mitad. El agente del
    chat no lee CLAUDE.md: si la regla no esta aqui, no existe para el."""
    from app.agent.system_prompt import SYSTEM_PROMPT
    assert "NUNCA calcules un precio de venta con SQL" in SYSTEM_PROMPT
    assert "mcp__negocio__margenes" in SYSTEM_PROMPT
