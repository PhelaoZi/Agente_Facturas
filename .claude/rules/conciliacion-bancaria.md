---
paths:
  - "scripts/import_transferencias.py"
  - "scripts/conciliar_banco.py"
  - "scripts/flujo_caja.py"
  - "scripts/importar_pagos_excel.py"
  - "scripts/lint_estado_pago.py"
  - "transferencias/**"
  - "tests/test_conciliar_banco.py"
  - "tests/test_flujo_caja.py"
---

# Conciliación bancaria

Orden de los pasos (cada uno es una skill del proyecto):

```
1. Descargar ConsultaTransferencia.xlsx del Itaú → transferencias/
2. /importar-transferencias  →  movimientos_banco
3. /conciliar-banco          →  cruza transferencias con facturas, confirmar → fecha_pago
4. /flujo-caja               →  proyección 4 semanas (usa avg dias_pago + cuentas_por_pagar)
5. /agregar-gasto            →  registrar gastos futuros para mejorar proyección
```

RUTs en `movimientos_banco` se normalizan al formato `77126823-4` (con guión, sin puntos).

> El estado de pago sigue siendo `ventas.fecha_pago` — la tabla `conciliaciones`
> es solo evidencia bancaria de respaldo. Ver la sección "Estado de pago de
> facturas" en `.claude/CLAUDE.md`, que se carga siempre.
