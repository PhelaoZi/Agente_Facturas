# tests/test_dashboard_post_importacion.py
"""Lo que tiene que pasar SOLO después de importar facturas.

Importar deja las facturas en `ventas` y `productos`, pero el dinero por cerveza
no vive ahí: se deduce (`scripts/calcular_atribucion.py`) y esa capa derivada no
se entera de que llegaron facturas nuevas.

Medido el 2026-08-16: Christian importó 13 facturas de agosto a las 08:31. La
tarea programada de la nube corre sola todos los días a las 08:00 y copia lo que
haya, esté al día o no. Sin este encadenamiento, al día siguiente el teléfono
habría mostrado las ventas totales CON agosto y el ranking de cervezas SIN
agosto: dos cifras contradictorias en la misma pantalla, sin ningún aviso.

Las dos reglas:

1. **Recalcular antes de publicar.** Si la atribución falla, NO se sincroniza:
   la nube trunca y recarga todo, así que subiría `ventas` nueva con la
   atribución vieja — exactamente la inconsistencia que esto viene a evitar.
   Mejor que el teléfono quede una versión atrás completo que media versión.
2. **Nada de esto puede voltear la importación.** Las facturas ya están
   guardadas cuando esto corre. Si falla, se avisa y se sigue.
"""
import pytest

from app import dashboard


class ProcesoFalso:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def corridas(monkeypatch):
    """Captura qué scripts se lanzaron, sin ejecutar ninguno."""
    lanzados = []

    def falso_run(cmd, **kwargs):
        lanzados.append(cmd)
        return ProcesoFalso()

    monkeypatch.setattr(dashboard.subprocess, "run", falso_run, raising=False)
    return lanzados


def _script(cmd):
    """Nombre del .py lanzado, sin la ruta."""
    return next((p.split("\\")[-1].split("/")[-1] for p in cmd if p.endswith(".py")), None)


# ─── El encadenamiento ───────────────────────────────────────────────────────

def test_recalcula_la_atribucion_despues_de_importar(corridas):
    r = dashboard._actualizar_atribucion()

    assert r["ok"] is True
    assert [_script(c) for c in corridas] == ["calcular_atribucion.py"]


def test_publica_a_la_nube_despues_de_recalcular(corridas):
    r = dashboard._publicar_en_la_nube()

    assert r["ok"] is True
    assert [_script(c) for c in corridas] == ["sync_nube.py"]


def test_si_la_atribucion_falla_no_se_publica_nada(monkeypatch):
    """La regla que evita la pantalla contradictoria."""
    lanzados = []

    def falso_run(cmd, **kwargs):
        lanzados.append(_script(cmd))
        if _script(cmd) == "calcular_atribucion.py":
            return ProcesoFalso(returncode=1, stderr="el lote no cuadra")
        return ProcesoFalso()

    monkeypatch.setattr(dashboard.subprocess, "run", falso_run, raising=False)
    monkeypatch.setattr(dashboard, "_actualizar_wiki", lambda ruts: {"ok": True})

    resultado = dashboard._tareas_post_importacion([])

    assert "sync_nube.py" not in lanzados, "no se publica una atribución vieja"
    assert resultado["atribucion"]["ok"] is False
    assert resultado["nube"]["ok"] is False
    assert "atribuc" in resultado["nube"]["detalle"].lower()


def test_con_todo_bien_corre_la_cadena_completa(monkeypatch):
    lanzados = []
    monkeypatch.setattr(dashboard.subprocess, "run",
                        lambda cmd, **k: (lanzados.append(_script(cmd)), ProcesoFalso())[1],
                        raising=False)
    monkeypatch.setattr(dashboard, "_actualizar_wiki", lambda ruts: {"ok": True})

    resultado = dashboard._tareas_post_importacion(["77.126.823-4"])

    assert lanzados == ["calcular_atribucion.py", "sync_nube.py"]
    assert resultado["atribucion"]["ok"] and resultado["nube"]["ok"]


# ─── Nada de esto puede voltear la importación ───────────────────────────────

@pytest.mark.parametrize("funcion", ["_actualizar_atribucion", "_publicar_en_la_nube"])
def test_un_script_que_revienta_no_lanza_excepcion(monkeypatch, funcion):
    """Las facturas ya están guardadas cuando esto corre: se avisa, no se cae."""
    def explota(cmd, **kwargs):
        raise OSError("no se pudo lanzar el proceso")

    monkeypatch.setattr(dashboard.subprocess, "run", explota, raising=False)

    r = getattr(dashboard, funcion)()

    assert r["ok"] is False
    assert r["detalle"]


def test_un_script_colgado_se_corta_y_lo_dice(monkeypatch):
    import subprocess as sp

    def se_cuelga(cmd, **kwargs):
        raise sp.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(dashboard.subprocess, "run", se_cuelga, raising=False)

    r = dashboard._actualizar_atribucion()

    assert r["ok"] is False
    assert "tard" in r["detalle"].lower()
