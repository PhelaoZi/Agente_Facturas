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
    assert orden.index("movimientos_banco") < orden.index("conciliaciones")


def test_sql_insert_construye_columnas():
    sql = sync_nube.sql_insert("clientes", ["rut_cliente", "razon_social"])
    assert sql == "INSERT INTO clientes (rut_cliente, razon_social) VALUES %s"


def test_sync_trunca_todo_en_una_sentencia_y_replica(monkeypatch):
    cur_nube = CursorFalso()
    conn_local = ConexionFalsa(CursorFalso())
    conn_nube = ConexionFalsa(cur_nube)

    monkeypatch.setattr(sync_nube, "leer_tabla",
                        lambda cur, t: (["a"], [(1,)]))
    monkeypatch.setattr(sync_nube, "leer_vista",
                        lambda cur, vista, cols: [(1,)])
    llamadas = []
    monkeypatch.setattr(sync_nube, "replicar_tabla",
                        lambda cur, t, cols, filas: llamadas.append(t))
    monkeypatch.setattr(sync_nube, "obtener_saldo_banco",
                        lambda cur: (1000.0, None))

    total = sync_nube.sync(conn_local, conn_nube)

    esperadas = sync_nube.TABLAS_ORDEN + list(sync_nube.VISTAS_REPLICADAS)
    truncates = [s for s in cur_nube.ejecutado if s.startswith("TRUNCATE")]
    assert len(truncates) == 1                      # una sola sentencia
    for tabla in esperadas:                         # tablas + vistas replicadas
        assert tabla in truncates[0]
    assert llamadas == esperadas                    # replica en orden de FKs
    assert conn_nube.commits == 1                   # una sola transaccion
    assert total == {t: 1 for t in esperadas}
    metas = [s for s in cur_nube.ejecutado if "sync_meta" in s]
    assert metas, "debe registrar metadatos del sync"


def test_main_es_no_fatal(monkeypatch):
    def explota():
        raise RuntimeError("sin internet")
    monkeypatch.setattr(sync_nube, "conectar_nube", explota)
    monkeypatch.setattr(sync_nube, "conectar_local", explota)
    assert sync_nube.main([]) == 1                  # informa error, no lanza


def test_los_tests_no_escriben_en_el_log_de_produccion(monkeypatch):
    """logs/sync_nube.log es la señal de monitoreo del sync a la nube: dice si
    el celular tiene datos frescos.

    Hasta el 2026-08-09 este archivo de tests le escribia errores FALSOS: el
    test de arriba llama a main() con las conexiones rotas a proposito, main()
    loguea el error, y log() abre LOG_FILE — la ruta real — porque ningun test
    la redirigia. Resultado medido: 95 de 141 lineas del log de produccion eran
    "sin internet" inventados por pytest, contra 26 syncs de verdad. El log
    dejo de servir para lo unico que existe: distinguir una caida real de una
    corrida de tests.
    """
    real = Path(__file__).resolve().parent.parent / "logs" / "sync_nube.log"
    antes = real.stat().st_size if real.exists() else 0

    def explota():
        raise RuntimeError("error de mentira, de un test")
    monkeypatch.setattr(sync_nube, "conectar_nube", explota)
    monkeypatch.setattr(sync_nube, "conectar_local", explota)
    sync_nube.main([])

    despues = real.stat().st_size if real.exists() else 0
    assert despues == antes, "un test escribio en el log de produccion"


def test_limpiar_meta_comandos_quita_lineas_backslash():
    crudo = "\\restrict abc123\nCREATE TABLE x (id int);\n  \\unrestrict abc123\nSELECT 1;"
    limpio = sync_nube.limpiar_meta_comandos(crudo)
    assert "\\restrict" not in limpio
    assert "\\unrestrict" not in limpio
    assert "CREATE TABLE x (id int);" in limpio
    assert "SELECT 1;" in limpio


# --- Fase 4: migracion de las tablas del chat ---
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent


class _FakeCursorChat:
    def __init__(self, registro):
        self.registro = registro

    def execute(self, sql, params=None):
        self.registro.append(sql)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConnChat:
    def __init__(self):
        self.ejecutado = []

    def cursor(self):
        return _FakeCursorChat(self.ejecutado)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_migracion_chat_es_idempotente():
    sql = (_RAIZ / "scripts" / "migrate_nube_chat.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS chat_sesiones" in sql
    assert "CREATE TABLE IF NOT EXISTS chat_uso" in sql


def test_tablas_chat_fuera_del_sync():
    # Si entraran a TABLAS_ORDEN, el sync las truncaria y borraria el historial.
    for tabla in ("chat_sesiones", "chat_uso"):
        assert tabla not in sync_nube.TABLAS_ORDEN


def test_aplicar_migraciones_chat_ejecuta_el_sql():
    conn = _FakeConnChat()
    sync_nube.aplicar_migraciones_chat(conn)
    assert any("chat_sesiones" in s for s in conn.ejecutado)


# --- Paridad de consultas del chat movil (2026-07-19) ---

def test_vistas_replicadas_costo_sku():
    fuente, columnas = sync_nube.VISTAS_REPLICADAS["costo_sku"]
    assert fuente == "vista_costo_sku"
    assert columnas == ["codigo", "nombre_cerveza", "formato",
                        "costo_liquido_unitario", "costo_envasado_unitario",
                        "costo_total_unitario"]
    assert "costo_sku" not in sync_nube.TABLAS_ORDEN


def test_leer_vista_selecciona_columnas_explicitas():
    cur = CursorFalso(respuestas=[[(1,)]])
    filas = sync_nube.leer_vista(cur, "vista_costo_sku", ["a", "b"])
    assert filas == [(1,)]
    assert cur.ejecutado == ["SELECT a, b FROM vista_costo_sku"]


def test_migraciones_de_cada_corrida_incluyen_views():
    # Las views y la tabla costo_sku se autoreparan en CADA sync, no solo --init.
    conn = _FakeConnChat()
    sync_nube.aplicar_migraciones_chat(conn)
    assert any("v_factura_cabecera" in s for s in conn.ejecutado)
    assert any("costo_sku" in s for s in conn.ejecutado)


def test_sql_views_define_lo_nuevo_idempotente():
    sql = (_RAIZ / "scripts" / "migrate_nube_views.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS costo_sku" in sql
    assert "CREATE OR REPLACE VIEW v_factura_cabecera" in sql
    assert "CREATE OR REPLACE VIEW v_lineas_factura" in sql
    assert "tipo_linea" in sql
    assert "tiene_nc" in sql
