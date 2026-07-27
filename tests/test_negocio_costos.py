# tests/test_negocio_costos.py
from app.negocio import costos


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_costos_sku_mapea():
    rows = [
        {"codigo": "CREAM-B30", "nombre_cerveza": "Cream Ale", "formato": "Barril 30L acero",
         "costo_liquido_unitario": 18000, "costo_envasado_unitario": 0,
         "costo_total_unitario": 18000},
    ]
    r = costos.costos_sku(FakeCursor(rows))
    assert r[0]["codigo"] == "CREAM-B30"
    assert r[0]["costo_total"] == 18000.0


def test_margenes_calcula_para_barril_con_precio():
    # Sin facturas: usa el precio confirmado por el productor (respaldo).
    rows = [
        {"codigo": "CREAM-B30", "nombre_cerveza": "Cream Ale", "formato": "Barril 30L acero",
         "costo_liquido_unitario": 18000, "costo_envasado_unitario": 0,
         "costo_total_unitario": 18000},
    ]
    r = costos.margenes(FakeCursorPrecios(rows, []))
    assert r[0]["precio_venta"] == 55370.0
    assert r[0]["margen"] == 55370.0 - 18000.0


def test_margenes_stout_cafe_cacao_casa_su_precio():
    # Regresión: _norm("Stout Café/Cacao") = "stout cafe/cacao"; la clave
    # exacta "stout cafe" no casaba (dict.get). Ahora busca por subcadena.
    rows = [
        {"codigo": "STOUT-B30", "nombre_cerveza": "Stout Café/Cacao",
         "formato": "Barril 30L acero", "costo_liquido_unitario": 40000,
         "costo_envasado_unitario": 5000, "costo_total_unitario": 45000},
    ]
    r = costos.margenes(FakeCursorPrecios(rows, [], recetas=("Stout Café/Cacao",)))
    assert r[0]["precio_venta"] == 75000.0
    assert r[0]["margen"] == 75000.0 - 45000.0


class FakeCursorPrecios:
    """costos.margenes() consulta la vista de costos y, aparte,
    precios_venta.precios_por_formato() hace sus dos consultas. Este cursor
    responde en ese orden: costos, recetas, lineas de factura."""

    def __init__(self, filas_costos, lineas_factura, recetas=("Cream Ale",)):
        self._respuestas = [
            filas_costos,
            [{"nombre_cerveza": r} for r in recetas],
            lineas_factura,
        ]
        self._actual = []

    def execute(self, sql, params=None):
        self._actual = self._respuestas.pop(0) if self._respuestas else []

    def fetchall(self):
        return self._actual


BOTELLA_CREAM = {
    "codigo": "CREAM-330", "nombre_cerveza": "Cream Ale", "formato": "Botella 330ml",
    "costo_liquido_unitario": 626, "costo_envasado_unitario": 265,
    "costo_total_unitario": 891,
}


def test_margenes_botella_usa_el_precio_deducido_de_las_facturas():
    """Antes devolvia None porque la lista escrita a mano solo tenia barriles.
    Ahora el precio sale de la factura: 400 de producto + 900 de logistica."""
    from datetime import date
    lineas = [{"folio": 4743, "fecha": date(2026, 7, 22), "monto_neto": 15600,
               "nombre_producto": "Botella 330cc Cream Ale",
               "cantidad": 12, "total_linea": 4800}]
    r = costos.margenes(FakeCursorPrecios([BOTELLA_CREAM], lineas))
    assert r[0]["precio_venta"] == 1300.0
    assert r[0]["margen"] == 1300.0 - 891.0
    assert r[0]["origen"] == "facturas"
    assert r[0]["n_facturas"] == 1


def test_margenes_cae_a_la_lista_cuando_no_hay_facturas():
    """Un SKU sin ventas todavia: el precio confirmado por el productor sigue
    sirviendo de respaldo, pero marcado como tal."""
    barril = {"codigo": "CREAM-B30", "nombre_cerveza": "Cream Ale",
              "formato": "Barril 30L acero", "costo_liquido_unitario": 18000,
              "costo_envasado_unitario": 0, "costo_total_unitario": 18000}
    r = costos.margenes(FakeCursorPrecios([barril], []))
    assert r[0]["precio_venta"] == 55370.0
    assert r[0]["origen"] == "lista"


def test_el_envase_pet_no_se_descuenta_del_margen():
    """El barril PET se factura con su propia línea por el costo del envase, así
    que el cliente ya lo pagó. Descontarlo otra vez del margen daba una pérdida
    falsa (-10% en la Cream). El margen del PET debe ser el mismo que el del
    barril de acero: el envase se cancela contra su propia línea."""
    acero = {"codigo": "CREAM-B30-A", "nombre_cerveza": "Cream Ale",
             "formato": "Barril 30L acero", "costo_liquido_unitario": 39322,
             "costo_envasado_unitario": 0, "costo_total_unitario": 39322}
    pet = {"codigo": "CREAM-B30-P", "nombre_cerveza": "Cream Ale",
           "formato": "Barril 30L PET", "costo_liquido_unitario": 39322,
           "costo_envasado_unitario": 15328, "costo_total_unitario": 54650}
    r = costos.margenes(FakeCursorPrecios([acero, pet], []))
    por_codigo = {m["codigo"]: m for m in r}

    assert por_codigo["CREAM-B30-P"]["margen"] == por_codigo["CREAM-B30-A"]["margen"]
    assert por_codigo["CREAM-B30-P"]["margen"] > 0
    assert por_codigo["CREAM-B30-P"]["envase_pass_through"] is True
    # El costo total real no se falsea: se sigue informando completo.
    assert por_codigo["CREAM-B30-P"]["costo_total"] == 54650.0
    # La botella NO es pass-through: su envase sí es costo propio.
    r2 = costos.margenes(FakeCursorPrecios([BOTELLA_CREAM], []))
    assert r2[0]["envase_pass_through"] is False


def test_margenes_sin_facturas_ni_lista_no_inventa_un_margen():
    lata = {"codigo": "SOUR-LATA", "nombre_cerveza": "Sour Berries",
            "formato": "Lata 470cc", "costo_liquido_unitario": 500,
            "costo_envasado_unitario": 200, "costo_total_unitario": 700}
    r = costos.margenes(FakeCursorPrecios([lata], [], recetas=("Sour Berries",)))
    assert r[0]["precio_venta"] is None
    assert r[0]["margen"] is None
    assert r[0]["origen"] is None
