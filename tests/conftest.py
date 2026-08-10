# -*- coding: utf-8 -*-
"""Configuración común de la suite.

Aísla los efectos secundarios que los tests podrían dejar en archivos reales
del proyecto.
"""
import sys

import pytest


@pytest.fixture(autouse=True)
def _sync_nube_no_toca_el_log_real(monkeypatch, tmp_path):
    """Ningún test escribe en logs/sync_nube.log.

    Ese log es la señal de monitoreo del sync a la nube: dice si el celular
    tiene datos frescos. `sync_nube.main()` loguea sus errores ahí, y los tests
    lo llaman a propósito con las conexiones rotas para verificar que un fallo
    no voltea el pipeline. Sin este redirect, cada corrida de pytest dejaba un
    "sin internet" inventado en el log de producción — 95 de 141 líneas,
    medido el 2026-08-09, contra 26 syncs de verdad.

    Va acá y no en el test puntual porque el problema no es de ese test: es
    que la ruta del log es alcanzable desde la suite. Cualquier test que llame
    a `log()` mañana tiene el mismo agujero.

    Solo actúa si el módulo ya está importado, para no cargarlo en toda la
    suite por gusto.
    """
    mod = sys.modules.get("sync_nube")
    if mod is not None and hasattr(mod, "LOG_FILE"):
        monkeypatch.setattr(mod, "LOG_FILE", tmp_path / "sync_nube.log")
