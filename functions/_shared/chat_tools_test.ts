// functions/_shared/chat_tools_test.ts
import { assertEquals, assertStringIncludes } from "jsr:@std/assert@1";
import { TOOLS, ejecutarTool, formatearPesos, type SqlCliente } from "./chat_tools.ts";

const HOY = new Date("2026-07-20");

/** Tagged template falso: devuelve resultados en orden, una query por llamada. */
function fakeSql(...resultados: unknown[][]): SqlCliente {
  const cola = [...resultados];
  return ((..._args: unknown[]) =>
    Promise.resolve(cola.shift() ?? [])) as unknown as SqlCliente;
}

Deno.test("formatearPesos usa puntos de miles chilenos", () => {
  assertEquals(formatearPesos(4267294), "$4.267.294");
  assertEquals(formatearPesos(0), "$0");
  assertEquals(formatearPesos(null), "$0");
  assertEquals(formatearPesos("55370.00"), "$55.370");
});

Deno.test("TOOLS: 10 tools con nombres unicos y schema de objeto", () => {
  assertEquals(TOOLS.length, 10);
  const nombres = TOOLS.map((t) => (t as { name: string }).name);
  assertEquals(new Set(nombres).size, 10);
  for (const t of TOOLS) {
    assertEquals((t as { input_schema: { type: string } }).input_schema.type, "object");
  }
});

Deno.test("deuda_total suma y separa por antiguedad", async () => {
  const sql = fakeSql([
    { total: 100000, dias_desde_emision: 10 },
    { total: 200000, dias_desde_emision: 45 },
    { total: 50000, dias_desde_emision: 120 },
  ]);
  const r = await ejecutarTool(sql, "deuda_total", {}, HOY);
  assertStringIncludes(r, "$350.000");
  assertStringIncludes(r, "3 facturas");
  assertStringIncludes(r, "$100.000");  // bucket 0-30
  assertStringIncludes(r, "$50.000");   // bucket +90
});

Deno.test("deuda_cliente sin filas responde sin deuda", async () => {
  const r = await ejecutarTool(fakeSql([]), "deuda_cliente", { nombre: "VDT" }, HOY);
  assertStringIncludes(r, "sin deuda pendiente");
});

Deno.test("ventas_total con rango", async () => {
  const sql = fakeSql([{ n: 6, total: 756409 }]);
  const r = await ejecutarTool(sql, "ventas_total",
    { desde: "2026-06-01", hasta: "2026-06-30" }, HOY);
  assertStringIncludes(r, "$756.409");
  assertStringIncludes(r, "6 facturas");
});

Deno.test("flujo_caja proyecta con las 4 queries", async () => {
  const sql = fakeSql(
    [{ folio: 1, fecha: "2026-07-10", rut_cliente: "1-9",
       razon_social_receptor: "Bar Uno", monto: 100000 }],  // v_flujo_pendientes
    [{ rut_cliente: "1-9", avg_dias: 15 }],                 // v_dias_pago_cliente
    [],                                                     // cuentas_por_pagar
    [{ valor: { saldo: 500000, fecha: "2026-07-18" } }],    // sync_meta saldo_banco
  );
  const r = await ejecutarTool(sql, "flujo_caja", {}, HOY);
  assertStringIncludes(r, "Semana 1");
  assertStringIncludes(r, "$100.000");
});

Deno.test("tool desconocida devuelve error legible", async () => {
  const r = await ejecutarTool(fakeSql([]), "borrar_todo", {}, HOY);
  assertStringIncludes(r, "Herramienta desconocida");
});
