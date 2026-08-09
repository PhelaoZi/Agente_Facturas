# tests/test_tools_negocio.py
from app.agent.tools_negocio import build_negocio_server


def test_negocio_server_registra_los_tools():
    server, names = build_negocio_server()
    assert server is not None
    assert len(names) == 16
    assert len(set(names)) == len(names), "hay nombres de tool duplicados"
    for esperado in [
        "mcp__negocio__margen_cliente",
        "mcp__negocio__margen_periodo",
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
    registro, _names = build_negocio_server()
    descripciones = {s["function"]["name"]: s["function"]["description"]
                     for s in registro.schemas_openai()}
    margenes = descripciones["mcp__negocio__margenes"].lower()
    assert "solo barriles" not in margenes
    assert "botella" in margenes


# ── Artefactos por referencia ─────────────────────────────────────────────────
# Hasta el 2026-08-06 las 55 filas de "que facturas tengo por cobrar" hacian
# este viaje: Postgres -> tool -> contexto del modelo -> el modelo las RE-ESCRIBE
# -> publicar_tabla -> pantalla. Los dos pasos del medio son peaje puro. Medido:
# entraban entre 1.600 y 5.500 tokens de filas al contexto, y la vuelta que
# publicaba terminaba en completion_tokens=1500 EXACTO (el techo, 2 de 2
# corridas). De ahi salian el corte, el costo, y la caida al turno de cierre que
# le escupia la sintaxis cruda al usuario.
#
# Ahora la tool publica la tabla con los datos que YA tiene en memoria y le
# devuelve al modelo solo el resumen. Si las filas no estan en su contexto, el
# modelo no puede volcarlas en el chat: el problema de UX se cierra por
# construccion, no por pedirselo en el prompt.

import asyncio

from app.agent import tools_negocio
from app.canvas.artifacts import Collector

FILAS_LARGAS = [[f"F{i}", f"Cliente {i}", f"${i}.000"] for i in range(20)]
LINEAS_LARGAS = [f"- F{i}: Cliente {i}" for i in range(20)]


def _texto_de(resultado):
    return resultado["content"][0]["text"]


def test_una_lista_larga_se_publica_en_el_lienzo_y_no_entra_al_contexto():
    col = Collector()
    r = tools_negocio.publicar_tabla_si_es_larga(
        col, "Facturas por cobrar", ["Folio", "Cliente", "Monto"],
        FILAS_LARGAS, "20 facturas, $8.883.587", LINEAS_LARGAS)

    assert len(col.items) == 1
    art = col.items[0]
    assert art.tipo == "tabla"
    assert art.titulo == "Facturas por cobrar"
    assert len(art.payload["filas"]) == 20, "el lienzo recibe TODAS las filas"

    texto = _texto_de(r)
    assert "20 facturas" in texto and "$8.883.587" in texto
    assert "Cliente 19" not in texto, "el detalle no debe entrar al contexto"
    assert texto.count("\n- F") <= tools_negocio.UMBRAL_TABLA, \
        "solo una muestra, para que el modelo pueda nombrar casos concretos"


def test_el_resumen_le_avisa_al_modelo_que_la_tabla_ya_esta_publicada():
    """Sin este aviso el modelo la publica de nuevo con publicar_tabla y el
    usuario ve la misma tabla dos veces."""
    texto = _texto_de(tools_negocio.publicar_tabla_si_es_larga(
        Collector(), "Facturas", ["A"], FILAS_LARGAS, "resumen", LINEAS_LARGAS))

    assert "lienzo" in texto.lower()
    assert "no la publiques" in texto.lower()


def test_una_lista_corta_va_en_texto_como_siempre():
    """Bajo el umbral el detalle se lee bien en el chat y el modelo lo necesita
    a mano: publicar una tabla de 3 filas seria ruido en pantalla."""
    col = Collector()
    filas = FILAS_LARGAS[:3]
    lineas = LINEAS_LARGAS[:3]

    texto = _texto_de(tools_negocio.publicar_tabla_si_es_larga(
        col, "Facturas", ["Folio"], filas, "3 facturas", lineas))

    assert col.items == []
    assert "Cliente 2" in texto, "el detalle corto SI va al contexto"
    assert "3 facturas" in texto


def test_sin_lienzo_nunca_publica_y_devuelve_el_detalle():
    """El lienzo es opcional: la tool tiene que seguir sirviendo sin el."""
    texto = _texto_de(tools_negocio.publicar_tabla_si_es_larga(
        None, "Facturas", ["Folio"], FILAS_LARGAS, "20 facturas", LINEAS_LARGAS))

    assert "Cliente 19" in texto


def test_build_negocio_server_acepta_el_lienzo_y_sigue_andando_sin_el():
    _server, names = tools_negocio.build_negocio_server(Collector())
    assert "mcp__negocio__facturas_vencidas" in names
    _server2, names2 = tools_negocio.build_negocio_server()
    assert names == names2


def _llamar(registro, nombre, args=None):
    """Invoca una tool como lo hace el orquestador: por el registro."""
    return asyncio.run(registro.ejecutar(f"mcp__negocio__{nombre}", args or {}))


def test_facturas_vencidas_publica_la_tabla_y_resume_por_cliente(monkeypatch):
    """La pregunta real del usuario ("cuantos clientes me deben y cuantas
    facturas cada uno") se responde con el AGREGADO, no con las 55 filas. El
    resumen que recibe el modelo tiene que traer esa forma ya hecha."""
    facturas = ([{"folio": 4700 + i, "cliente": "A & C SERVICIOS", "total": 124000.0,
                  "dias": 30 + i} for i in range(10)]
                + [{"folio": 4800 + i, "cliente": "VDT SPA", "total": 69990.0,
                    "dias": 10 + i} for i in range(5)])
    monkeypatch.setattr(tools_negocio, "_con_cursor",
                        lambda fn, *a, **k: facturas)

    col = Collector()
    server, _ = tools_negocio.build_negocio_server(col)
    texto = _llamar(server, "facturas_vencidas", {"dias": 0})

    assert len(col.items) == 1 and col.items[0].tipo == "tabla"
    assert len(col.items[0].payload["filas"]) == 15

    assert "15 facturas" in texto
    assert "2 clientes" in texto
    assert "A & C SERVICIOS" in texto and "10" in texto
    assert texto.count("- Folio") <= tools_negocio.UMBRAL_TABLA, \
        "del detalle solo va una muestra; las 15 filas estan en el lienzo"


def test_ranking_deudores_largo_se_publica(monkeypatch):
    deudores = [{"cliente": f"CLIENTE {i}", "deuda": 100000.0 - i, "n": 2}
                for i in range(19)]
    monkeypatch.setattr(tools_negocio, "_con_cursor", lambda fn, *a, **k: deudores)

    col = Collector()
    server, _ = tools_negocio.build_negocio_server(col)
    texto = _llamar(server, "ranking_deudores", {"limite": 20})

    assert len(col.items) == 1
    assert len(col.items[0].payload["filas"]) == 19
    assert "CLIENTE 18" not in texto
