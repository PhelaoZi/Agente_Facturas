# tests/test_negocio_gastos.py
import pytest
from app.negocio import gastos


class FakeCursor:
    """Cursor falso estilo RealDictCursor. Captura sql/params; fetchone devuelve
    `row`, fetchall devuelve `rows` (o [row] si solo se pasó row)."""

    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def test_validar_gasto_normaliza_monto_chileno():
    r = gastos.validar_gasto("Luz", "185.000", "2026-06-30")
    assert r == {"descripcion": "Luz", "monto": 185000.0,
                 "fecha": "2026-06-30", "proveedor": None, "categoria": None}


def test_validar_gasto_acepta_monto_numerico_y_campos_opcionales():
    r = gastos.validar_gasto("Arriendo", 850000, "2026-07-05",
                             proveedor="Prop SA", categoria="arriendo")
    assert r["monto"] == 850000.0
    assert r["proveedor"] == "Prop SA"
    assert r["categoria"] == "arriendo"


def test_validar_gasto_rechaza_descripcion_vacia():
    with pytest.raises(ValueError):
        gastos.validar_gasto("   ", "1000", "2026-06-30")


def test_validar_gasto_rechaza_monto_no_numerico():
    with pytest.raises(ValueError):
        gastos.validar_gasto("Luz", "abc", "2026-06-30")


def test_validar_gasto_rechaza_monto_cero_o_negativo():
    with pytest.raises(ValueError):
        gastos.validar_gasto("Luz", "0", "2026-06-30")


def test_validar_gasto_rechaza_fecha_mala():
    with pytest.raises(ValueError):
        gastos.validar_gasto("Luz", "1000", "30/06/2026")


def test_registrar_gasto_devuelve_id_y_usa_parametros():
    cur = FakeCursor({"id": 42})
    new_id = gastos.registrar_gasto(cur, "Luz", 185000.0, "2026-06-30", None, "servicios")
    assert new_id == 42
    # El INSERT va parametrizado, en el orden de columnas de cuentas_por_pagar
    assert cur.params == ("Luz", None, 185000.0, "2026-06-30", "servicios")
    assert "cuentas_por_pagar" in cur.sql
    assert "RETURNING id" in cur.sql


def test_obtener_gasto_devuelve_dict():
    fila = {"id": 5, "descripcion": "Contadora", "monto": 50000,
            "fecha_vencimiento": "2026-06-30", "proveedor": None,
            "categoria": None, "pagado": False}
    cur = FakeCursor(row=fila)
    r = gastos.obtener_gasto(cur, 5)
    assert r["descripcion"] == "Contadora"
    assert cur.params == (5,)
    assert "cuentas_por_pagar" in cur.sql


def test_obtener_gasto_inexistente_devuelve_none():
    assert gastos.obtener_gasto(FakeCursor(row=None), 999) is None


def test_listar_excluye_pagados_por_defecto():
    filas = [{"id": 3, "descripcion": "Gas", "monto": 200000,
              "fecha_vencimiento": "2026-06-30", "proveedor": None,
              "categoria": None, "pagado": False}]
    cur = FakeCursor(rows=filas)
    r = gastos.listar(cur)
    assert len(r) == 1 and r[0]["descripcion"] == "Gas"
    assert "pagado = FALSE" in cur.sql


def test_listar_con_filtro_usa_ilike():
    cur = FakeCursor(rows=[])
    gastos.listar(cur, filtro="luz")
    assert "ILIKE" in cur.sql
    assert cur.params == ("%luz%",)


def test_validar_borrar_acepta_id_string_o_int():
    assert gastos.validar_borrar({"id": "5"}) == {"id": 5}
    assert gastos.validar_borrar({"id": 5}) == {"id": 5}


def test_validar_borrar_rechaza_id_malo():
    for malo in ({"id": "abc"}, {"id": 0}, {"id": -2}, {}):
        with pytest.raises(ValueError):
            gastos.validar_borrar(malo)


def test_borrar_gasto_devuelve_mensaje_y_borra():
    cur = FakeCursor(row={"descripcion": "Contadora"})
    r = gastos.borrar_gasto(cur, 5)
    assert r == {"id": 5, "descripcion": "Contadora", "mensaje": "Gasto borrado: Contadora"}
    assert "DELETE" in cur.sql and "cuentas_por_pagar" in cur.sql
    assert cur.params == (5,)


def test_borrar_gasto_inexistente_lanza_valueerror():
    with pytest.raises(ValueError):
        gastos.borrar_gasto(FakeCursor(row=None), 999)


def test_validar_marcar_pagado_fecha_por_defecto_hoy():
    from datetime import date
    r = gastos.validar_marcar_pagado({"id": 3})
    assert r["id"] == 3
    assert r["fecha_pago"] == date.today().isoformat()


def test_validar_marcar_pagado_con_fecha_explicita():
    r = gastos.validar_marcar_pagado({"id": 3, "fecha_pago": "2026-06-01"})
    assert r == {"id": 3, "fecha_pago": "2026-06-01"}


def test_validar_marcar_pagado_rechaza_fecha_mala():
    import pytest
    with pytest.raises(ValueError):
        gastos.validar_marcar_pagado({"id": 3, "fecha_pago": "01/06/2026"})


def test_marcar_gasto_pagado_actualiza_y_devuelve_mensaje():
    cur = FakeCursor(row={"descripcion": "Contadora"})
    r = gastos.marcar_gasto_pagado(cur, 5, "2026-06-01")
    assert r["mensaje"] == "Gasto marcado como pagado: Contadora"
    assert "UPDATE" in cur.sql and "pagado = TRUE" in cur.sql
    assert cur.params == ("2026-06-01", 5)


def test_marcar_gasto_pagado_inexistente_lanza_valueerror():
    import pytest
    with pytest.raises(ValueError):
        gastos.marcar_gasto_pagado(FakeCursor(row=None), 999, "2026-06-01")


def test_validar_editar_normaliza_monto():
    r = gastos.validar_editar({"id": 4, "monto": "180.000"})
    assert r == {"id": 4, "cambios": {"monto": 180000.0}}


def test_validar_editar_mapea_fecha_a_vencimiento():
    r = gastos.validar_editar({"id": 4, "fecha": "2026-07-01"})
    assert r["cambios"] == {"fecha_vencimiento": "2026-07-01"}


def test_validar_editar_sin_campos_lanza_valueerror():
    import pytest
    with pytest.raises(ValueError):
        gastos.validar_editar({"id": 4})


def test_validar_editar_rechaza_monto_malo():
    import pytest
    with pytest.raises(ValueError):
        gastos.validar_editar({"id": 4, "monto": "abc"})


def test_editar_gasto_arma_update_parametrizado():
    cur = FakeCursor(row={"descripcion": "Gas"})
    r = gastos.editar_gasto(cur, 4, {"monto": 180000.0})
    assert r["mensaje"] == "Gasto actualizado: Gas"
    assert "UPDATE cuentas_por_pagar SET monto = %s" in cur.sql
    assert cur.params == (180000.0, 4)


def test_editar_gasto_inexistente_lanza_valueerror():
    import pytest
    with pytest.raises(ValueError):
        gastos.editar_gasto(FakeCursor(row=None), 999, {"monto": 1.0})
