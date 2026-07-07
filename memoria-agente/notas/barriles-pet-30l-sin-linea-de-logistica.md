# Barriles PET 30L sin línea de logística

- tipo: negocio
- fecha: 2026-07-07

Los barriles PET de 30L NO llevan ítem de "Logistica" en la factura (a diferencia del resto de los formatos, que se facturan en doble línea: producto + Logistica). Para PET 30L, el precio real del barril es directamente COALESCE(monto_neto_ajustado, monto_neto) de esa única línea, sin sumar una segunda línea de logística.
