# tests/test_tools_acciones.py
from app.agent import tools_acciones
from app.canvas.artifacts import Collector


def test_accion_gasto_artifact_arma_payload():
    params = {"descripcion": "Luz", "monto": 185000.0, "fecha": "2026-06-30",
              "proveedor": None, "categoria": "servicios"}
    art = tools_acciones.accion_gasto_artifact(params)
    assert art.tipo == "accion"
    assert art.titulo == "Confirmar gasto"
    assert art.payload["tipo_accion"] == "registrar_gasto"
    assert art.payload["params"] == params
    assert "185.000" in art.payload["resumen"]
    assert "Luz" in art.payload["resumen"]
    assert "30/06/2026" in art.payload["resumen"]
    assert "None" not in art.payload["resumen"]


def test_build_acciones_server_lista_proponer_gasto():
    server, tool_names = tools_acciones.build_acciones_server(Collector())
    assert "mcp__acciones__proponer_gasto" in tool_names


def test_borrar_gasto_artifact():
    g = {"id": 5, "descripcion": "Contadora", "monto": 50000, "fecha_vencimiento": "2026-06-30"}
    art = tools_acciones.borrar_gasto_artifact(g)
    assert art.tipo == "accion"
    assert art.payload["tipo_accion"] == "borrar_gasto"
    assert art.payload["params"] == {"id": 5}
    assert "Contadora" in art.payload["resumen"]
    assert "50.000" in art.payload["resumen"]


def test_marcar_pagado_artifact():
    g = {"id": 5, "descripcion": "Contadora", "monto": 50000, "fecha_vencimiento": "2026-06-30"}
    art = tools_acciones.marcar_pagado_artifact(g, "2026-06-22")
    assert art.payload["tipo_accion"] == "marcar_gasto_pagado"
    assert art.payload["params"] == {"id": 5, "fecha_pago": "2026-06-22"}
    assert "22/06/2026" in art.payload["resumen"]


def test_editar_gasto_artifact_muestra_antes_despues():
    g = {"id": 4, "descripcion": "Gas", "monto": 200000, "fecha_vencimiento": "2026-06-30"}
    art = tools_acciones.editar_gasto_artifact(g, {"id": 4, "monto": "180000"}, {"monto": 180000.0})
    assert art.payload["tipo_accion"] == "editar_gasto"
    assert art.payload["params"] == {"id": 4, "monto": "180000"}
    assert "200.000" in art.payload["resumen"] and "180.000" in art.payload["resumen"]


def test_build_acciones_server_incluye_las_tres_nuevas():
    server, tool_names = tools_acciones.build_acciones_server(Collector())
    for n in ("mcp__acciones__proponer_borrar_gasto",
              "mcp__acciones__proponer_editar_gasto",
              "mcp__acciones__proponer_marcar_gasto_pagado"):
        assert n in tool_names
    # No rompe la existente:
    assert "mcp__acciones__proponer_gasto" in tool_names


def test_agregar_seguimiento_artifact_arma_payload():
    params = {"rut_cliente": "77-1", "cliente": "Bar X", "motivo": "Se enfrió",
              "prioridad": "alta", "senales": "caida_consumo"}
    art = tools_acciones.agregar_seguimiento_artifact(params)
    assert art.tipo == "accion"
    assert art.payload["tipo_accion"] == "agregar_seguimiento"
    # El cliente (razón social) es solo para el resumen, no va en los params de la acción:
    assert "cliente" not in art.payload["params"]
    assert art.payload["params"]["rut_cliente"] == "77-1"
    assert art.payload["params"]["motivo"] == "Se enfrió"
    assert "Bar X" in art.payload["resumen"]


def test_marcar_seguimiento_artifact_arma_payload():
    s = {"id": 5, "rut_cliente": "77-1", "razon_social": "Bar X", "motivo": "Se enfrió"}
    art = tools_acciones.marcar_seguimiento_artifact(s, "contactado")
    assert art.payload["tipo_accion"] == "marcar_seguimiento"
    assert art.payload["params"] == {"id": 5, "estado": "contactado"}
    assert "Bar X" in art.payload["resumen"]
    assert "contactado" in art.payload["resumen"]


def test_build_acciones_server_incluye_las_de_seguimiento():
    _server, tool_names = tools_acciones.build_acciones_server(Collector())
    assert "mcp__acciones__proponer_agregar_seguimiento" in tool_names
    assert "mcp__acciones__proponer_marcar_seguimiento" in tool_names
    # No rompe las existentes:
    assert "mcp__acciones__proponer_gasto" in tool_names
