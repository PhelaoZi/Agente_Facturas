# tests/test_tools_required.py
"""Que cada tool declare obligatorio SOLO lo que de verdad lo es.

Motivo (diseño del 2026-08-09): el atajo del SDK de Anthropic marcaba TODOS los
parametros como obligatorios, sin forma de decir que uno era opcional. Asi, ante
"cuanto hemos vendido en total", el modelo estaba OBLIGADO a inventar un rango
de fechas y devolvia un total parcial que parecia el total. No fallaba: mentia
en silencio.

Esta tabla es la fuente de verdad del diseño. Si una tool cambia sus parametros,
este test obliga a decidir a proposito que es obligatorio, en vez de heredarlo.
"""
import asyncio

import pytest

from app.agent import memoria
from app.agent.publish_tools import build_lienzo_server
from app.agent.tools_acciones import build_acciones_server
from app.agent.tools_negocio import build_negocio_server
from app.canvas.artifacts import Collector
from app.agent.orchestrator import ResultadosSQL

# nombre completo -> obligatorios esperados (en cualquier orden)
REQUIRED_ESPERADO = {
    # ── negocio ───────────────────────────────────────────────────────────────
    "mcp__negocio__deuda_total": [],
    "mcp__negocio__deuda_cliente": ["nombre"],
    "mcp__negocio__ranking_deudores": [],
    "mcp__negocio__facturas_vencidas": [],
    "mcp__negocio__ventas_total": [],
    "mcp__negocio__ranking_clientes": [],
    "mcp__negocio__ventas_cliente": ["nombre"],
    "mcp__negocio__ventas_producto": ["nombre"],
    "mcp__negocio__flujo_caja": [],
    "mcp__negocio__costos_sku": [],
    "mcp__negocio__margenes": [],
    "mcp__negocio__margen_periodo": ["desde", "hasta"],
    "mcp__negocio__margen_cliente": ["cliente"],
    "mcp__negocio__listar_gastos": [],
    "mcp__negocio__clientes_en_riesgo": [],
    "mcp__negocio__listar_seguimiento": [],
    # ── lienzo ────────────────────────────────────────────────────────────────
    "mcp__lienzo__publicar_kpi": ["etiqueta", "valor"],
    "mcp__lienzo__publicar_grafico": ["titulo", "chart_type", "x", "y"],
    "mcp__lienzo__publicar_tabla": ["titulo", "columnas", "filas"],
    "mcp__lienzo__publicar_informe": ["titulo", "markdown"],
    "mcp__lienzo__publicar_consulta": ["ref", "titulo"],
    # ── acciones ──────────────────────────────────────────────────────────────
    # Ninguna escribe: todas proponen una tarjeta que el usuario confirma. Por
    # eso un dato faltante se resuelve con un mensaje de error del validador, no
    # marcandolo obligatorio "por si acaso".
    "mcp__acciones__proponer_gasto": ["descripcion", "monto", "fecha"],
    "mcp__acciones__proponer_borrar_gasto": ["id"],
    "mcp__acciones__proponer_marcar_gasto_pagado": ["id"],
    "mcp__acciones__proponer_editar_gasto": ["id"],
    "mcp__acciones__proponer_agregar_seguimiento": ["rut_cliente", "motivo"],
    "mcp__acciones__proponer_marcar_seguimiento": ["id", "estado"],
    "mcp__acciones__proponer_marcar_factura_pagada": ["folio"],
    # `fecha` SI es obligatoria: corregir una fecha sin decir a cual no existe.
    "mcp__acciones__proponer_corregir_fecha_pago": ["folio", "fecha"],
    "mcp__acciones__proponer_marcar_cliente_incobrable": ["cliente"],
    "mcp__acciones__proponer_reactivar_cliente": ["cliente"],
    # ── memoria ───────────────────────────────────────────────────────────────
    "mcp__memoria__guardar_nota": ["titulo", "contenido"],
    "mcp__memoria__leer_nota": ["nombre"],
}


def _todos_los_schemas():
    col = Collector()
    registros = [
        build_lienzo_server(col, ResultadosSQL())[0],
        build_negocio_server(col)[0],
        build_acciones_server(col)[0],
        memoria.build_memoria_server()[0],
    ]
    return {s["function"]["name"]: s["function"]["parameters"]
            for r in registros for s in r.schemas_openai()}


def test_el_catalogo_es_exactamente_el_de_la_tabla():
    """Una tool nueva sin fila en la tabla no pasa: obliga a decidir su
    `required` a proposito."""
    assert sorted(_todos_los_schemas()) == sorted(REQUIRED_ESPERADO)


@pytest.mark.parametrize("nombre", sorted(REQUIRED_ESPERADO))
def test_required_declarado_coincide_con_el_diseno(nombre):
    schema = _todos_los_schemas()[nombre]

    assert sorted(schema.get("required", [])) == sorted(REQUIRED_ESPERADO[nombre])


@pytest.mark.parametrize("nombre", sorted(REQUIRED_ESPERADO))
def test_todo_obligatorio_existe_como_parametro(nombre):
    """Un `required` que nombra un parametro inexistente es un schema roto."""
    schema = _todos_los_schemas()[nombre]

    assert set(schema.get("required", [])) <= set(schema.get("properties", {}))


# ── Que ninguna tool reviente al omitir sus opcionales ────────────────────────
# La red de seguridad del cambio: si marque opcional algo que el codigo lee con
# args["x"], la tool explota con KeyError en produccion. Aca se llama a cada una
# SIN sus opcionales y se verifica que responde.

def _sin_bd(monkeypatch):
    """Corta todo acceso a Postgres: estas pruebas miran el manejo de
    argumentos, no las consultas."""
    from app.agent import tools_acciones, tools_negocio

    monkeypatch.setattr(tools_negocio, "_con_cursor", lambda fn, *a, **k: [])
    for helper in ("_obtener_gasto", "_obtener_seguimiento", "_obtener_factura"):
        monkeypatch.setattr(tools_acciones, helper, lambda *a, **k: None)
    monkeypatch.setattr(tools_acciones, "_buscar_clientes", lambda *a, **k: [])


VALOR_DE_PRUEBA = {"nombre": "VDT", "cliente": "VDT", "desde": "2026-01-01",
                   "hasta": "2026-01-31", "titulo": "T", "etiqueta": "E",
                   "valor": "1", "markdown": "x", "ref": "q1", "columnas": ["c"],
                   "filas": [["1"]], "chart_type": "bar", "x": ["a"], "y": [1],
                   "descripcion": "d", "monto": "1000", "fecha": "2026-01-01",
                   "id": 1, "folio": 1, "estado": "contactado",
                   "rut_cliente": "76.123.456-7", "motivo": "m",
                   "contenido": "c"}


@pytest.mark.parametrize("nombre", sorted(REQUIRED_ESPERADO))
def test_ninguna_tool_revienta_sin_sus_opcionales(nombre, monkeypatch, tmp_path):
    _sin_bd(monkeypatch)
    # La memoria escribe en disco: se la manda a un directorio temporal para no
    # ensuciar memoria-agente/ al correr los tests.
    monkeypatch.setattr(memoria, "MEMORIA_DIR", tmp_path)
    monkeypatch.setattr(memoria, "NOTAS_DIR", tmp_path / "notas")
    monkeypatch.setattr(memoria, "INDICE", tmp_path / "MEMORIA.md")
    col = Collector()
    registros = [
        build_lienzo_server(col, ResultadosSQL())[0],
        build_negocio_server(col)[0],
        build_acciones_server(col)[0],
        memoria.build_memoria_server()[0],
    ]
    registro = next(r for r in registros if r.tiene(nombre))
    args = {p: VALOR_DE_PRUEBA[p] for p in REQUIRED_ESPERADO[nombre]}

    texto = asyncio.run(registro.ejecutar(nombre, args))

    assert "KeyError" not in texto, f"{nombre} lee un opcional con args[...]"
    assert texto, f"{nombre} no devolvio nada"
