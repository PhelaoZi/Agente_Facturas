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


def test_marcar_factura_pagada_artifact_arma_payload():
    f = {"folio": 4664, "razon_social": "BOTILLERIA X", "total": 69990,
         "fecha": "2026-06-15", "fecha_pago": None, "rut_cliente": "77-1"}
    art = tools_acciones.marcar_factura_pagada_artifact(f, "2026-07-03")
    assert art.tipo == "accion"
    assert art.payload["tipo_accion"] == "marcar_factura_pagada"
    assert art.payload["params"] == {"folio": 4664, "fecha_pago": "2026-07-03"}
    assert "BOTILLERIA X" in art.payload["resumen"]
    assert "69.990" in art.payload["resumen"]
    assert "03/07/2026" in art.payload["resumen"]


def test_build_acciones_server_incluye_marcar_factura_pagada():
    _server, tool_names = tools_acciones.build_acciones_server(Collector())
    assert "mcp__acciones__proponer_marcar_factura_pagada" in tool_names


def test_corregir_fecha_pago_artifact_muestra_antes_despues():
    f = {"folio": 4686, "razon_social": "VDT SPA", "total": 100000,
         "fecha": "2026-05-15", "fecha_pago": "2026-06-09", "rut_cliente": "77-2"}
    art = tools_acciones.corregir_fecha_pago_artifact(f, "2026-06-30")
    assert art.tipo == "accion"
    assert art.payload["tipo_accion"] == "corregir_fecha_pago"
    assert art.payload["params"] == {"folio": 4686, "fecha_pago": "2026-06-30"}
    assert "09/06/2026" in art.payload["resumen"]
    assert "30/06/2026" in art.payload["resumen"]
    assert "VDT SPA" in art.payload["resumen"]


def test_build_acciones_server_incluye_corregir_fecha_pago():
    _server, tool_names = tools_acciones.build_acciones_server(Collector())
    assert "mcp__acciones__proponer_corregir_fecha_pago" in tool_names


def test_marcar_incobrable_artifact_muestra_cuanta_deuda_sale():
    """La tarjeta es la red de seguridad: antes de confirmar hay que ver a QUIÉN
    se castiga y cuánta plata sale del por cobrar."""
    c = {"rut_cliente": "76861668-K", "razon_social": "BIER BAR SPA",
         "estado": "activo", "deuda": 188750, "n_facturas": 1}
    art = tools_acciones.marcar_incobrable_artifact(c)
    assert art.tipo == "accion"
    assert art.payload["tipo_accion"] == "marcar_cliente_incobrable"
    assert art.payload["params"] == {"rut_cliente": "76861668-K"}
    assert "BIER BAR SPA" in art.payload["resumen"]
    assert "76861668-K" in art.payload["resumen"]
    assert "188.750" in art.payload["resumen"]


def test_reactivar_cliente_artifact_arma_payload():
    c = {"rut_cliente": "76861668-K", "razon_social": "BIER BAR SPA",
         "estado": "incobrable", "deuda": 188750, "n_facturas": 1}
    art = tools_acciones.reactivar_cliente_artifact(c)
    assert art.payload["tipo_accion"] == "reactivar_cliente"
    assert art.payload["params"] == {"rut_cliente": "76861668-K"}
    assert "BIER BAR SPA" in art.payload["resumen"]


def test_build_acciones_server_incluye_las_de_incobrable():
    _server, tool_names = tools_acciones.build_acciones_server(Collector())
    assert "mcp__acciones__proponer_marcar_cliente_incobrable" in tool_names
    assert "mcp__acciones__proponer_reactivar_cliente" in tool_names


def test_build_acciones_server_incluye_las_de_seguimiento():
    _server, tool_names = tools_acciones.build_acciones_server(Collector())
    assert "mcp__acciones__proponer_agregar_seguimiento" in tool_names
    assert "mcp__acciones__proponer_marcar_seguimiento" in tool_names
    # No rompe las existentes:
    assert "mcp__acciones__proponer_gasto" in tool_names
