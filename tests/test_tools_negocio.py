# tests/test_tools_negocio.py
from app.agent.tools_negocio import build_negocio_server


def test_negocio_server_registra_los_tools():
    server, names = build_negocio_server()
    assert server is not None
    assert len(names) == 14
    for esperado in [
        "mcp__negocio__deuda_total",
        "mcp__negocio__deuda_cliente",
        "mcp__negocio__ranking_deudores",
        "mcp__negocio__facturas_vencidas",
        "mcp__negocio__ventas_total",
        "mcp__negocio__ranking_clientes",
        "mcp__negocio__ventas_cliente",
        "mcp__negocio__ventas_producto",
        "mcp__negocio__flujo_caja",
        "mcp__negocio__costos_sku",
        "mcp__negocio__margenes",
    ]:
        assert esperado in names


def test_listar_gastos_registrado_en_tools():
    from app.agent.tools_negocio import build_negocio_server
    _server, tool_names = build_negocio_server()
    assert "mcp__negocio__listar_gastos" in tool_names


def test_tools_gerente_comercial_registradas():
    _server, names = build_negocio_server()
    assert "mcp__negocio__clientes_en_riesgo" in names
    assert "mcp__negocio__listar_seguimiento" in names


def test_la_tool_margenes_ya_no_dice_que_solo_cubre_barriles():
    """La descripcion es lo unico que el modelo lee antes de decidir si la usa.
    Mientras dijo 'solo barriles', ante una pregunta por botellas se iba a
    improvisar SQL sobre `productos` y agotaba sus pasos."""
    import asyncio
    from mcp.types import ListToolsRequest
    cfg, _names = build_negocio_server()
    handler = cfg["instance"].request_handlers[ListToolsRequest]
    res = asyncio.run(handler(ListToolsRequest()))
    descripciones = {t.name: t.description for t in res.root.tools}
    assert "solo barriles" not in descripciones["margenes"].lower()
    assert "botella" in descripciones["margenes"].lower()
