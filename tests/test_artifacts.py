# tests/test_artifacts.py
import pytest
from app.canvas.artifacts import Artifact, Collector, merge_artifacts


def test_artifact_valida_tipo():
    with pytest.raises(ValueError):
        Artifact(tipo="zzz", titulo="x", payload={})


def test_artifact_requiere_titulo():
    with pytest.raises(ValueError):
        Artifact(tipo="kpi", titulo="", payload={})


def test_artifact_genera_id_y_fecha():
    a = Artifact(tipo="kpi", titulo="Ventas", payload={"valor": "10"})
    assert a.id
    assert a.creado_en


def test_collector_acumula_en_orden():
    c = Collector()
    c.add(Artifact(tipo="kpi", titulo="A", payload={}))
    c.add(Artifact(tipo="kpi", titulo="B", payload={}))
    assert [a.titulo for a in c.items] == ["A", "B"]


def test_merge_evita_duplicados_por_id():
    a = Artifact(tipo="kpi", titulo="A", payload={})
    canvas = [a]
    assert len(merge_artifacts(canvas, [a])) == 1  # mismo id, no duplica
    b = Artifact(tipo="kpi", titulo="B", payload={})
    assert len(merge_artifacts(canvas, [b])) == 2


def test_permite_tipo_accion():
    from app.canvas.artifacts import Artifact
    art = Artifact(tipo="accion", titulo="Confirmar gasto", payload={"x": 1})
    assert art.tipo == "accion"
    assert art.titulo == "Confirmar gasto"


def test_rechaza_tipo_desconocido():
    import pytest
    from app.canvas.artifacts import Artifact
    with pytest.raises(ValueError):
        Artifact(tipo="inventado", titulo="x", payload={})
