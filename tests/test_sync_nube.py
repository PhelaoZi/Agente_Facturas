# -*- coding: utf-8 -*-
"""Tests de scripts/sync_nube.py: orden de tablas, SQL generado y no-fatalidad."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sync_nube


class CursorFalso:
    """Registra cada execute; fetchall devuelve lo encolado."""
    def __init__(self, respuestas=None):
        self.ejecutado = []
        self.respuestas = list(respuestas or [])
        self.description = None

    def execute(self, sql, params=None):
        self.ejecutado.append(sql.strip())

    def fetchall(self):
        return self.respuestas.pop(0) if self.respuestas else []

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ConexionFalsa:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def cursor(self, **kwargs):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *a):
        if exc_type is None:
            self.commits += 1
        return False


def test_orden_de_tablas_respeta_fks():
    orden = sync_nube.TABLAS_ORDEN
    assert orden.index("clientes") < orden.index("ventas")
    assert orden.index("ventas") < orden.index("productos")
    assert orden.index("ventas") < orden.index("conciliaciones")


def test_sql_insert_construye_columnas():
    sql = sync_nube.sql_insert("clientes", ["rut_cliente", "razon_social"])
    assert sql == "INSERT INTO clientes (rut_cliente, razon_social) VALUES %s"


def test_sync_trunca_todo_en_una_sentencia_y_replica(monkeypatch):
    cur_nube = CursorFalso()
    conn_local = ConexionFalsa(CursorFalso())
    conn_nube = ConexionFalsa(cur_nube)

    monkeypatch.setattr(sync_nube, "leer_tabla",
                        lambda cur, t: (["a"], [(1,)]))
    llamadas = []
    monkeypatch.setattr(sync_nube, "replicar_tabla",
                        lambda cur, t, cols, filas: llamadas.append(t))
    monkeypatch.setattr(sync_nube, "obtener_saldo_banco",
                        lambda cur: (1000.0, None))

    total = sync_nube.sync(conn_local, conn_nube)

    truncates = [s for s in cur_nube.ejecutado if s.startswith("TRUNCATE")]
    assert len(truncates) == 1                      # una sola sentencia
    for tabla in sync_nube.TABLAS_ORDEN:            # todas las tablas en ella
        assert tabla in truncates[0]
    assert llamadas == sync_nube.TABLAS_ORDEN       # replica en orden de FKs
    assert conn_nube.commits == 1                   # una sola transaccion
    assert total == {t: 1 for t in sync_nube.TABLAS_ORDEN}
    metas = [s for s in cur_nube.ejecutado if "sync_meta" in s]
    assert metas, "debe registrar metadatos del sync"


def test_main_es_no_fatal(monkeypatch):
    def explota():
        raise RuntimeError("sin internet")
    monkeypatch.setattr(sync_nube, "conectar_nube", explota)
    monkeypatch.setattr(sync_nube, "conectar_local", explota)
    assert sync_nube.main([]) == 1                  # informa error, no lanza


def test_limpiar_meta_comandos_quita_lineas_backslash():
    crudo = "\\restrict abc123\nCREATE TABLE x (id int);\n  \\unrestrict abc123\nSELECT 1;"
    limpio = sync_nube.limpiar_meta_comandos(crudo)
    assert "\\restrict" not in limpio
    assert "\\unrestrict" not in limpio
    assert "CREATE TABLE x (id int);" in limpio
    assert "SELECT 1;" in limpio
