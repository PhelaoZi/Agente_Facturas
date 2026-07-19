---
name: conciliar-banco
description: >
  Concilia las transferencias bancarias importadas con las facturas pendientes de cobro.
  Usar cuando el usuario quiera marcar facturas como pagadas, cruzar transferencias con facturas,
  actualizar fechas de pago, o saber que facturas ya fueron cobradas.
  Ejemplos: "concilia el banco", "marca las facturas pagadas", "cruza las transferencias con facturas",
  "actualiza los pagos recibidos".
context: fork
disable-model-invocation: true
allowed-tools: Bash(python *)
---

# Conciliar Banco — Zigurat ERP

Cruza los movimientos bancarios sin conciliar con las facturas pendientes de cobro.
Muestra un reporte completo y pide confirmacion antes de guardar.

## Reglas

- NUNCA saltarse la confirmacion del usuario
- NUNCA continuar si el script falla con error de conexion
- Ejecutar DESPUES de `/importar-transferencias`

## Paso 1 — Ejecutar analisis y conciliacion

```bash
python scripts/conciliar_banco.py
```

El script se encarga de:
1. Analizar los movimientos sin conciliar
2. Mostrar el reporte completo (matches, sin match, sin cliente)
3. Pedir confirmacion interactiva al usuario
4. Si confirma: guardar en BD

Si falla: reportar error y detener.

## Paso 2 — Siguiente paso sugerido

Despues de completar, sugerir al usuario:
- `/flujo-caja` para ver la proyeccion actualizada

## Paso 3 — Actualizar wiki (no-bloqueante)

Si la conciliación fue exitosa (se guardaron cambios en BD), parsear los RUTs
de los clientes conciliados del output del script.

```bash
python scripts/wiki_update.py --ruts RUT1,RUT2,RUT3 --origen "conciliar-banco"
```

Si falla: mostrar warning "⚠️ No se pudo actualizar la wiki" pero NO fallar el proceso.
La conciliación ya se completó exitosamente.

## Paso final: replicar a la nube (no fatal)

Tras un sync/conciliación exitoso, ejecutar:

    python scripts/sync_nube.py

Si falla (sin internet, InsForge caído), mostrar el error como WARNING y
terminar normalmente: la réplica es secundaria, el pipeline local es lo
importante. NUNCA abortar ni reintentar por este paso.
