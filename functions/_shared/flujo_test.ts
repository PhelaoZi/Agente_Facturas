// nube/functions/_shared/flujo_test.ts
// La logica debe ser espejo de app/negocio/flujo.py (paridad de cifras).
import { assertEquals } from "jsr:@std/assert@1";
import { proyectarFlujo } from "./flujo.ts";

const HOY = new Date("2026-07-20");

Deno.test("factura con promedio conocido cae en la semana correcta", () => {
  const r = proyectarFlujo(
    [{ folio: 1, fecha: new Date("2026-07-10"), rut_cliente: "1-9",
       razon_social_receptor: "X", monto: 100 }],
    { "1-9": 15 },   // proyectada = 10 jul + 15d = 25 jul -> semana 0
    [], 0, HOY,
  );
  assertEquals(r.semanas[0].ingresos, 100);
});

Deno.test("proyeccion en el pasado se mueve a hoy (semana 0)", () => {
  const r = proyectarFlujo(
    [{ folio: 2, fecha: new Date("2026-05-01"), rut_cliente: "1-9",
       razon_social_receptor: "X", monto: 50 }],
    { "1-9": 10 },   // proyectada 11 may < hoy -> hoy
    [], 0, HOY,
  );
  assertEquals(r.semanas[0].ingresos, 50);
});

Deno.test("cliente sin historial usa 30 dias globales", () => {
  const r = proyectarFlujo(
    [{ folio: 3, fecha: new Date("2026-07-15"), rut_cliente: "2-7",
       razon_social_receptor: "Y", monto: 80 }],
    {},              // sin avg -> 30d -> 14 ago -> semana 3
    [], 0, HOY,
  );
  assertEquals(r.semanas[3].ingresos, 80);
});

Deno.test("gasto recurrente mensual se proyecta dentro del horizonte", () => {
  const r = proyectarFlujo([], {},
    [{ descripcion: "arriendo", proveedor: "Z", monto: 500,
       fecha_vencimiento: new Date("2026-01-05"), categoria: "fijo",
       recurrente: true, periodicidad: "mensual" }],
    1000, HOY,
  );
  // dia 5 del mes: 5 ago cae en semana 2 del horizonte 20 jul - 17 ago
  assertEquals(r.semanas[2].egresos, 500);
  assertEquals(r.semanas[3].saldo_acumulado, 500);
});
