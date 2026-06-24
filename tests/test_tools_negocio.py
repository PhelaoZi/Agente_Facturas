# tests/test_tools_negocio.py
from app.agent.tools_negocio import build_negocio_server


def test_negocio_server_registra_los_tools():
    server, names = build_negocio_server()
    assert server is not None
    assert len(names) == 12
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
