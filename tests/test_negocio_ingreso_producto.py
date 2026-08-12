# tests/test_negocio_ingreso_producto.py
"""Lecturas de ingreso por producto desde la vista canónica.

Paso 4 del cierre (docs/debate-arquitectura/10-...): conectar los consumidores.

La regla que ordena este módulo: **una cifra de plata por producto nunca sale
sola**. Va siempre con su período y su cobertura. Sin eso, un $33 millones no
dice si son de un año o de tres, ni cuánto de eso se estimó — que es exactamente
como se llegó a este problema.
"""
from app.negocio import ingreso_producto


class FakeCursor:
    """Cursor falso estilo RealDictCursor. Devuelve un lote de filas por query."""

    def __init__(self, *lotes):
        self._lotes = list(lotes)
        self._actual = []

    def execute(self, sql, params=None):
        self._actual = self._lotes.pop(0) if self._lotes else []

    def fetchall(self):
        return self._actual

    def fetchone(self):
        return self._actual[0] if self._actual else None


def _fila(cerveza, ingreso, unidades=10, det=None, est=0):
    return {"cerveza": cerveza, "ingreso": ingreso, "unidades": unidades,
            "determinista": ingreso if det is None else det, "estimado": est}


# ─── Ranking ──────────────────────────────────────────────────────────────────

def test_ranking_ordena_por_ingreso():
    cur = FakeCursor([_fila("Cream Ale", 33_368_079, 631),
                      _fila("Scotch Ale", 24_151_236, 465)])

    r = ingreso_producto.ranking(cur)

    assert [c["cerveza"] for c in r["cervezas"]] == ["Cream Ale", "Scotch Ale"]
    assert r["cervezas"][0]["ingreso"] == 33_368_079.0


def test_el_ranking_declara_el_periodo_consultado():
    """Sin período, una cifra de plata no significa nada."""
    cur = FakeCursor([_fila("Cream Ale", 1_000)])

    r = ingreso_producto.ranking(cur, desde="2026-01-01", hasta="2026-07-31")

    assert r["desde"] == "2026-01-01"
    assert r["hasta"] == "2026-07-31"
    assert "2026-01-01" in r["alcance"] and "2026-07-31" in r["alcance"]


def test_sin_filtro_de_fecha_el_alcance_lo_dice():
    """El caso que más se equivoca: preguntar "cuánto vendí de Cream Ale" y
    recibir el total de dos años y medio creyendo que es del año."""
    r = ingreso_producto.ranking(FakeCursor([_fila("Cream Ale", 1_000)]))

    assert r["desde"] is None
    assert "todo el histórico" in r["alcance"].lower()


# ─── Cobertura ────────────────────────────────────────────────────────────────

def test_informa_que_parte_del_monto_es_estimada():
    """Una cifra determinística y una estimada no pueden verse iguales."""
    cur = FakeCursor([_fila("Cream Ale", 100_000, det=70_000, est=30_000)])

    r = ingreso_producto.ranking(cur)

    assert r["cervezas"][0]["pct_estimado"] == 30.0
    assert "30" in r["cobertura"]


def test_una_cifra_totalmente_evidenciada_tambien_lo_dice():
    cur = FakeCursor([_fila("Cream Ale", 100_000, det=100_000, est=0)])

    r = ingreso_producto.ranking(cur)

    assert r["cervezas"][0]["pct_estimado"] == 0.0


def test_sin_datos_no_inventa_cobertura():
    r = ingreso_producto.ranking(FakeCursor([]))

    assert r["cervezas"] == []
    assert r["cobertura"]          # dice algo, no revienta ni miente


# ─── Detalle de una cerveza ───────────────────────────────────────────────────

def test_detalle_de_una_cerveza_trae_sus_clientes():
    cur = FakeCursor(
        [{"ingreso": 3_860_544, "unidades": 80, "determinista": 1_200_000,
          "estimado": 2_660_544, "n_documentos": 42}],
        [{"razon_social": "A & C", "ingreso": 3_860_544, "unidades": 80}],
    )

    r = ingreso_producto.por_cerveza(cur, "Cream Ale")

    assert r["cerveza"] == "Cream Ale"
    assert r["ingreso"] == 3_860_544.0
    assert r["clientes"][0]["cliente"] == "A & C"


def test_una_cerveza_sin_ventas_no_revienta():
    r = ingreso_producto.por_cerveza(FakeCursor([], []), "Porter")

    assert r["ingreso"] == 0
    assert r["clientes"] == []
