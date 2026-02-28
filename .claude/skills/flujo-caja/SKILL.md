---
name: flujo-caja
description: >
  Proyecta el flujo de caja de las proximas 4 semanas basandose en facturas pendientes
  de cobro y gastos programados. Usar cuando el usuario quiera saber cuando va a cobrar,
  proyectar ingresos, ver el flujo de caja, o saber si habra problemas de liquidez.
  Ejemplos: "proyecta el flujo de caja", "cuanto voy a cobrar esta semana",
  "habra plata para pagar el arriendo?", "muestra el flujo de caja", "proyeccion de pagos".
disable-model-invocation: false
allowed-tools: Bash(python *)
---

# Flujo de Caja — Zigurat ERP

Genera la proyeccion de flujo de caja de las proximas 4 semanas.

## Reglas

- NUNCA pedir confirmacion antes de ejecutar
- Si el usuario menciona un saldo especifico, pasar `--saldo-inicial MONTO`
- Presentar el output con analisis breve al final

## Paso 1 — Ejecutar proyeccion

Sin saldo manual:
```bash
python scripts/flujo_caja.py
```

Con saldo manual (si el usuario lo indica):
```bash
python scripts/flujo_caja.py --saldo-inicial MONTO
```

Si falla: reportar error y detener.

## Paso 2 — Presentar analisis

Despues de mostrar el output del script, agregar un breve analisis:
- Semana con mayor ingreso proyectado
- Si hay alguna semana con saldo negativo proyectado (riesgo de liquidez)
- Clientes con facturas mas atrasadas (emitidas hace >30 dias sin pago)
- Recordar que `/agregar-gasto` permite registrar gastos para mejorar la precision
