# tests/test_tools_alcance.py
"""Cada tool con filtro opcional dice CON QUE filtro respondio.

Es la otra mitad de haber arreglado el `required` (diseño del 2026-08-09).
Mientras el filtro era obligatorio, la respuesta siempre nombraba el periodo o
la receta. Al poder omitirlo, "Ventas: $47.230.000" pasa a ser el caso normal —
y esa cifra no dice si cubre el mes, el año o toda la historia del negocio.

La cabecera la arma Python con los argumentos que DE VERDAD recibio, nunca el
modelo: si se la pidieramos por prompt, algun dia se le olvida.
"""
import asyncio

import pytest

from app.agent import tools_negocio
from app.agent.tools_negocio import build_negocio_server


def _llamar(monkeypatch, datos, tool, args=None):
    monkeypatch.setattr(tools_negocio, "_con_cursor", lambda fn, *a, **k: datos)
    registro, _ = build_negocio_server()
    return asyncio.run(registro.ejecutar(f"mcp__negocio__{tool}", args or {}))


# ── ventas_total ──────────────────────────────────────────────────────────────

def test_ventas_total_sin_fechas_dice_que_es_el_historico(monkeypatch):
    """El caso peligroso: una cifra parcial que parece el total. Antes de este
    cambio el modelo no podia siquiera pedir el historico."""
    datos = {"n": 312, "total": 47230000.0, "desde": None, "hasta": None}

    texto = _llamar(monkeypatch, datos, "ventas_total")

    assert "histórico" in texto.lower()
    assert "47.230.000" in texto


def test_ventas_total_con_fechas_nombra_el_rango(monkeypatch):
    datos = {"n": 12, "total": 3000000.0, "desde": "2026-06-01", "hasta": "2026-06-30"}

    texto = _llamar(monkeypatch, datos, "ventas_total",
                    {"desde": "2026-06-01", "hasta": "2026-06-30"})

    assert "2026-06-01" in texto and "2026-06-30" in texto
    assert "histórico" not in texto.lower()


# ── rankings: avisan que la lista viene cortada ───────────────────────────────

def test_ranking_deudores_avisa_que_puede_haber_mas(monkeypatch):
    """Si devolvio exactamente el limite, el usuario esta viendo una punta. Sin
    este aviso ve 5 deudores y cree que son todos."""
    datos = [{"cliente": f"CLIENTE {i}", "deuda": 100000.0, "n": 1} for i in range(5)]

    texto = _llamar(monkeypatch, datos, "ranking_deudores", {"limite": 5})

    assert "puede haber más" in texto.lower()


def test_ranking_deudores_dice_que_son_todos_si_no_llego_al_limite(monkeypatch):
    datos = [{"cliente": f"CLIENTE {i}", "deuda": 100000.0, "n": 1} for i in range(3)]

    texto = _llamar(monkeypatch, datos, "ranking_deudores", {"limite": 5})

    assert "son todos" in texto.lower()
    assert "puede haber más" not in texto.lower()


def test_ranking_clientes_avisa_que_puede_haber_mas(monkeypatch):
    datos = [{"cliente": f"C{i}", "rut": "1-9", "total": 100000.0} for i in range(10)]

    texto = _llamar(monkeypatch, datos, "ranking_clientes", {"limite": 10})

    assert "puede haber más" in texto.lower()


# ── facturas_vencidas: el umbral cambia por completo la lista ─────────────────

def test_facturas_vencidas_nombra_el_umbral_de_dias(monkeypatch):
    datos = [{"folio": 4700, "cliente": "VDT SPA", "total": 69990.0, "dias": 75}]

    texto = _llamar(monkeypatch, datos, "facturas_vencidas", {"dias": 60})

    assert "60" in texto


def test_facturas_vencidas_sin_argumento_nombra_el_umbral_por_defecto(monkeypatch):
    datos = [{"folio": 4700, "cliente": "VDT SPA", "total": 69990.0, "dias": 75}]

    texto = _llamar(monkeypatch, datos, "facturas_vencidas")

    assert "30" in texto


# ── catalogo: costos y margenes ───────────────────────────────────────────────

SKUS = [{"codigo": "CA30", "cerveza": "Cream Ale", "formato": "barril 30L",
         "costo_total": 20000.0}]


def test_costos_sku_sin_receta_dice_que_es_todo_el_catalogo(monkeypatch):
    texto = _llamar(monkeypatch, SKUS, "costos_sku")

    assert "todo el catálogo" in texto.lower()


def test_costos_sku_con_receta_nombra_el_filtro(monkeypatch):
    # El filtro NO aparece en los datos falsos a proposito: si apareciera, el
    # test pasaria por el nombre de la cerveza y no por la cabecera.
    texto = _llamar(monkeypatch, SKUS, "costos_sku", {"receta": "porter"})

    assert "porter" in texto.lower()
    assert "todo el catálogo" not in texto.lower()


MARGENES = [{"cerveza": "Cream Ale", "formato": "barril 30L", "margen": 30000.0,
             "margen_pct": 55.0, "costo_total": 20000.0, "costo_comparable": 25000.0,
             "precio_venta": 55370.0, "origen": "facturas", "n_facturas": 12,
             "precio_promedio": 55370.0, "envase_pass_through": False}]


def test_margenes_sin_receta_dice_que_es_todo_el_catalogo(monkeypatch):
    texto = _llamar(monkeypatch, MARGENES, "margenes")

    assert "todo el catálogo" in texto.lower()


def test_margenes_con_receta_nombra_el_filtro(monkeypatch):
    texto = _llamar(monkeypatch, MARGENES, "margenes", {"receta": "stout"})

    assert "stout" in texto.lower()


def test_margen_cliente_nombra_siempre_al_cliente(monkeypatch):
    """Un margen sin decir de quien es no se puede interpretar: los descuentos
    por cliente son reales y grandes."""
    datos = [{"cerveza": "Cream Ale", "formato": "barril 30L", "precio_cliente": 50000.0,
              "costo": 25000.0, "margen": 25000.0, "margen_pct": 50.0,
              "precio_general": 55370.0, "margen_pct_general": 55.0, "n_facturas": 4}]

    texto = _llamar(monkeypatch, datos, "margen_cliente", {"cliente": "VDT SPA"})

    assert "VDT SPA" in texto


# ── listados ──────────────────────────────────────────────────────────────────

GASTOS = [{"id": 1, "descripcion": "Arriendo", "monto": 500000.0,
           "fecha_vencimiento": "2026-08-15", "proveedor": None}]


def test_listar_gastos_sin_filtro_dice_que_son_todos(monkeypatch):
    texto = _llamar(monkeypatch, GASTOS, "listar_gastos")

    assert "todos" in texto.lower()


def test_listar_gastos_con_filtro_lo_nombra(monkeypatch):
    # Filtro ausente de los datos falsos, por lo mismo que en costos_sku.
    texto = _llamar(monkeypatch, GASTOS, "listar_gastos", {"filtro": "peaje"})

    assert "peaje" in texto.lower()


SEGUIMIENTOS = [{"id": 1, "prioridad": "alta", "razon_social": "VDT SPA",
                 "rut_cliente": "76.1-9", "motivo": "se enfrió"}]


@pytest.mark.parametrize("estado", ["pendiente", "contactado"])
def test_listar_seguimiento_nombra_el_estado(monkeypatch, estado):
    texto = _llamar(monkeypatch, SEGUIMIENTOS, "listar_seguimiento",
                    {"estado": estado} if estado != "pendiente" else {})

    assert estado in texto.lower()


# ── ingreso_producto ──────────────────────────────────────────────────────────

def test_ingreso_producto_sin_fechas_dice_que_es_el_historico(monkeypatch):
    """El caso que motivo toda la reparacion: "cuanto vendi de Cream Ale" y
    recibir el total de dos anios y medio creyendo que es del anio."""
    datos = {"cervezas": [{"cerveza": "Cream Ale", "ingreso": 33368079.0,
                           "unidades": 1087, "pct_estimado": 35.5}],
             "desde": None, "hasta": None,
             "alcance": "Ingreso por cerveza (todo el histórico, sin filtro de fecha)",
             "cobertura": "67.8% determinístico y 32.2% estimado"}

    texto = _llamar(monkeypatch, datos, "ingreso_producto")

    assert "histórico" in texto.lower()
    assert "33.368.079" in texto


def test_ingreso_producto_declara_cuanto_se_estimo(monkeypatch):
    """Una cifra determinista y una estimada no pueden verse iguales: la mitad
    del ingreso de una factura con dos cervezas se reparte a prorrata, y eso
    nadie lo puede verificar contra el documento."""
    datos = {"cervezas": [{"cerveza": "Cream Ale", "ingreso": 1000.0,
                           "unidades": 1, "pct_estimado": 43.7}],
             "desde": "2026-01-01", "hasta": None,
             "alcance": "Ingreso por cerveza (desde el 2026-01-01)",
             "cobertura": "56.3% determinístico y 43.7% estimado"}

    texto = _llamar(monkeypatch, datos, "ingreso_producto", {"desde": "2026-01-01"})

    assert "43.7% estimado" in texto or "44% estimado" in texto
    assert "2026-01-01" in texto
