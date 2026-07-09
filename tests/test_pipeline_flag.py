# tests/test_pipeline_flag.py
"""El flag .changes_validated debe quedar ligado al CONTENIDO validado.

Antes el flag era solo un "ok": si se re-corría parse_dte.py (regenerando
changes.json) sin re-validar, sync_db.py aceptaba el archivo nuevo con el flag
viejo. Ahora el flag guarda el SHA-256 del changes.json validado y sync_db lo
verifica antes de escribir en la BD.
"""
from scripts.sync_db import flag_valido
from scripts.validate_changes import hash_changes


def _armar(tmp_path, contenido='{"documentos": []}'):
    changes = tmp_path / "changes.json"
    changes.write_text(contenido, encoding="utf-8")
    flag = tmp_path / ".changes_validated"
    return changes, flag


def test_hash_changes_es_estable_y_depende_del_contenido(tmp_path):
    changes, _ = _armar(tmp_path)
    h1 = hash_changes(changes)
    assert h1 == hash_changes(changes)  # determinista
    changes.write_text('{"documentos": [1]}', encoding="utf-8")
    assert hash_changes(changes) != h1  # cambia con el contenido


def test_flag_valido_acepta_changes_validado(tmp_path):
    changes, flag = _armar(tmp_path)
    flag.write_text(hash_changes(changes), encoding="utf-8")
    assert flag_valido(changes, flag) is True


def test_flag_valido_rechaza_changes_modificado_despues_de_validar(tmp_path):
    changes, flag = _armar(tmp_path)
    flag.write_text(hash_changes(changes), encoding="utf-8")
    changes.write_text('{"documentos": ["otro"]}', encoding="utf-8")
    assert flag_valido(changes, flag) is False


def test_flag_valido_rechaza_flag_ausente(tmp_path):
    changes, flag = _armar(tmp_path)
    assert flag_valido(changes, flag) is False


def test_flag_valido_rechaza_flag_formato_antiguo(tmp_path):
    # Un flag "ok" dejado por la versión anterior ya no basta: obliga a
    # re-validar una vez tras la migración.
    changes, flag = _armar(tmp_path)
    flag.write_text("ok", encoding="utf-8")
    assert flag_valido(changes, flag) is False
