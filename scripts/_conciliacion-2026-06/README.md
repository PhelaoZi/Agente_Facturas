# Conciliación de cobranza — junio 2026 (scripts de un solo uso)

Estos scripts son **cirugía de datos puntual** de la limpieza de cobranza de
junio 2026, que corrigió el por-cobrar inflado (~$27M → ~$5M, banco sin
importar). **No son parte del pipeline de producción** y no deben re-ejecutarse:
ya cumplieron su función y la base de datos quedó en su estado correcto.

Se archivan aquí como registro histórico de qué se tocó y por qué.

| Script | Qué hizo |
|---|---|
| `reconciliar_fifo_banco.py` / `reconciliar_exacto.py` / `limpieza_fifo.py` | Conciliación determinista factura-a-factura contra el banco (FIFO). |
| `backfill_pagos_2024.py` | Marcó como pagadas las facturas 2024 sin `fecha_pago`. |
| `ajuste_pagos_2025_lote1.py` / `marcar_faciles_2025.py` / `marcar_al_dia_revision.py` | Marcado masivo de pagos 2025 por lotes. |
| `revertir_faciles_2026.py` | Corrige a `marcar_faciles_2025.py`, que por error marcó también facturas 2026 (las devuelve a pendiente). |
| `cerrar_amadeus.py` / `cerrar_bar_original.py` / `cerrar_ubuntu.py` | Cierre de facturas de clientes que pagan desde otro RUT (el cruce por RUT no los veía). |
| `parse_compras_temp.py` | Parser temporal de XMLs de compras. |

> La fuente de verdad del estado de pago es `ventas.fecha_pago` (ver CLAUDE.md).
> Para conciliación recurrente usar el pipeline: `/importar-transferencias` →
> `/conciliar-banco`.
