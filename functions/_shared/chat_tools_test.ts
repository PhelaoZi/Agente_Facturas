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

Deno.test("TOOLS: 18 tools con nombres unicos y schema de objeto", () => {
  assertEquals(TOOLS.length, 18);
  const nombres = TOOLS.map((t) => (t as { name: string }).name);
  assertEquals(new Set(nombres).size, 18);
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

Deno.test("crear_tarea ejecuta la insercion", async () => {
  const sql = fakeSql([{ id: 42, descripcion: "Reunion", fecha: "2026-07-25" }]);
  const r = await ejecutarTool(sql, "crear_tarea", { descripcion: "Reunion", fecha: "2026-07-25" }, HOY);
  assertStringIncludes(r, "Tarea creada con éxito");
  assertStringIncludes(r, "ID: 42");
  assertStringIncludes(r, "Reunion");
});

Deno.test("listar_tareas devuelve listado", async () => {
  const sql = fakeSql([
    { id: 1, descripcion: "Llamar distribuidor", fecha: "2026-07-22", completada: false },
    { id: 2, descripcion: "Pagar internet", fecha: "2026-07-23", completada: true }
  ]);
  const r = await ejecutarTool(sql, "listar_tareas", {}, HOY);
  assertStringIncludes(r, "Tareas agendadas");
  assertStringIncludes(r, "[ID: 1] 2026-07-22: \"Llamar distribuidor\" [PENDIENTE]");
  assertStringIncludes(r, "[ID: 2] 2026-07-23: \"Pagar internet\" [COMPLETADA]");
});

Deno.test("marcar_tarea_completada ejecuta el update", async () => {
  const sql = fakeSql([{ id: 7, descripcion: "Pagar agua", completada: true }]);
  const r = await ejecutarTool(sql, "marcar_tarea_completada", { id: 7 }, HOY);
  assertStringIncludes(r, "marcada como COMPLETADA con éxito");
  assertStringIncludes(r, "ID 7");
  assertStringIncludes(r, "Pagar agua");
});

Deno.test("tool desconocida devuelve error legible", async () => {
  const r = await ejecutarTool(fakeSql([]), "borrar_todo", {}, HOY);
  assertStringIncludes(r, "Herramienta desconocida");
});

// --- Paridad de consultas (2026-07-19) ---

Deno.test("ultimas_facturas lista con estado de pago", async () => {
  const sql = fakeSql([
    { folio: 4720, fecha: "2026-07-18", razon_social_receptor: "Bar Central",
      total_real: 69990, fecha_pago: null },
    { folio: 4719, fecha: "2026-07-15", razon_social_receptor: "Santa Cebada",
      total_real: 139980, fecha_pago: "2026-07-17" },
  ]);
  const r = await ejecutarTool(sql, "ultimas_facturas", {}, HOY);
  assertStringIncludes(r, "Folio 4720 (2026-07-18) Bar Central: $69.990 — PENDIENTE");
  assertStringIncludes(r, "Folio 4719 (2026-07-15) Santa Cebada: $139.980 — pagada el 2026-07-17");
});

Deno.test("detalle_factura etiqueta lineas y avisa la NC", async () => {
  const sql = fakeSql(
    [{ folio: 4664, tipo_documento: 33, fecha: "2026-06-30", rut_cliente: "76111222-3",
       razon_social_receptor: "Bar Central", neto_real: 90370, total_real: 111543,
       total_original: 121543, tiene_nc: true, fecha_pago: null }],
    [
      { nombre_producto: "Barril 30L Cream Ale", cantidad: 1, precio_unitario: 20000,
        subtotal: 20000, tipo_linea: "producto" },
      { nombre_producto: "Logistica Cream Ale", cantidad: 1, precio_unitario: 35370,
        subtotal: 35370, tipo_linea: "logistica" },
      { nombre_producto: "Barril Pet 30L", cantidad: 1, precio_unitario: 35000,
        subtotal: 35000, tipo_linea: "envase_pet" },
    ],
  );
  const r = await ejecutarTool(sql, "detalle_factura", { folio: 4664 }, HOY);
  assertStringIncludes(r, "Factura folio 4664 — Bar Central (76111222-3)");
  assertStringIncludes(r, "PENDIENTE de pago");
  assertStringIncludes(r, "[Logistica: parte del precio de la cerveza]");
  assertStringIncludes(r, "[Envase PET: costo del envase traspasado");
  assertStringIncludes(r, "nota de credito aplicada (total original $121.543 -> $111.543)");
});

Deno.test("detalle_factura de folio inexistente lo dice", async () => {
  const r = await ejecutarTool(fakeSql([]), "detalle_factura", { folio: 99999 }, HOY);
  assertStringIncludes(r, "No existe ningun documento con folio 99999");
});

Deno.test("detalle_factura reconoce una nota de credito", async () => {
  const sql = fakeSql(
    [{ folio: 120, tipo_documento: 61, fecha: "2026-05-02", rut_cliente: "1-9",
       razon_social_receptor: "Bar Uno", neto_real: -20000, total_real: -23800,
       total_original: -23800, tiene_nc: false, fecha_pago: null }],
    [],
  );
  const r = await ejecutarTool(sql, "detalle_factura", { folio: 120 }, HOY);
  assertStringIncludes(r, "NOTA DE CREDITO (tipo 61)");
});

Deno.test("costos_sku formatea liquido + envasado = total", async () => {
  const sql = fakeSql([
    { codigo: "CREAM-330-C12", nombre_cerveza: "Cream Ale", formato: "Botella 330ml",
      costo_liquido_unitario: 250, costo_envasado_unitario: 400, costo_total_unitario: 650 },
  ]);
  const r = await ejecutarTool(sql, "costos_sku", { receta: "cream" }, HOY);
  assertStringIncludes(r, "Cream Ale · Botella 330ml (CREAM-330-C12)");
  assertStringIncludes(r, "liquido $250 + envasado $400 = $650 por unidad");
});

Deno.test("margenes calcula barril y deja botella sin precio", async () => {
  const sql = fakeSql([
    { codigo: "CREAM-B30", nombre_cerveza: "Cream Ale", formato: "Barril 30L",
      costo_total_unitario: 25370 },
    { codigo: "CREAM-330", nombre_cerveza: "Cream Ale", formato: "Botella 330ml",
      costo_total_unitario: 650 },
  ]);
  const r = await ejecutarTool(sql, "margenes", {}, HOY);
  assertStringIncludes(r, "precio $55.370 - costo $25.370 = margen $30.000 (54.2%)");
  assertStringIncludes(r, "Botella 330ml: costo $650, sin precio de venta confirmado");
});

interface RegistroTxn { opciones?: string; sqls: string[] }

// Fake con begin(): registra opciones y sentencias ejecutadas en la txn.
function fakeSqlConBegin(
  filas: Record<string, unknown>[],
  registro: RegistroTxn,
  falla?: Error,
): SqlCliente {
  const base = ((..._a: unknown[]) =>
    Promise.resolve([])) as unknown as SqlCliente;
  base.begin = (opciones, fn) => {
    registro.opciones = opciones;
    const t = ((..._a: unknown[]) =>
      Promise.resolve([])) as unknown as import("./chat_tools.ts").SqlTransaccion;
    (t as { unsafe: (q: string) => Promise<unknown> }).unsafe = (q: string) => {
      registro.sqls.push(q);
      if (falla && !q.startsWith("SET")) return Promise.reject(falla);
      return Promise.resolve(q.startsWith("SET") ? [] : filas);
    };
    return fn(t);
  };
  return base;
}

Deno.test("consulta_sql rechaza escrituras y multi-sentencia sin tocar la BD", async () => {
  const registro: RegistroTxn = { sqls: [] };
  const sql = fakeSqlConBegin([], registro);
  for (const mala of [
    "INSERT INTO ventas VALUES (1)",
    "UPDATE ventas SET folio = 1",
    "DELETE FROM ventas",
    "DROP TABLE ventas",
    "SELECT 1; DROP TABLE ventas",
  ]) {
    const r = await ejecutarTool(sql, "consulta_sql", { consulta: mala }, HOY);
    assertStringIncludes(r, "Error:");
  }
  assertEquals(registro.sqls, []);   // ninguna llego a ejecutarse
});

Deno.test("consulta_sql corre en READ ONLY con timeout y tolera ; final", async () => {
  const registro: RegistroTxn = { sqls: [] };
  const sql = fakeSqlConBegin([{ n: 42 }], registro);
  const r = await ejecutarTool(sql, "consulta_sql",
    { consulta: "SELECT COUNT(*) AS n FROM ventas;" }, HOY);
  assertEquals(registro.opciones, "read only");
  assertEquals(registro.sqls[0], "SET LOCAL statement_timeout = 8000");
  assertEquals(registro.sqls[1], "SELECT COUNT(*) AS n FROM ventas");
  assertStringIncludes(r, "n=42");
});

Deno.test("consulta_sql trunca filas y reporta el total", async () => {
  const muchas = Array.from({ length: 150 }, (_, i) => ({ id: i }));
  const sql = fakeSqlConBegin(muchas, { sqls: [] });
  const r = await ejecutarTool(sql, "consulta_sql", { consulta: "SELECT id FROM ventas" }, HOY);
  assertStringIncludes(r, "150 fila(s)");
  assertStringIncludes(r, "(mostrando 100 de 150)");
});

Deno.test("consulta_sql devuelve el error SQL como texto", async () => {
  const sql = fakeSqlConBegin([], { sqls: [] }, new Error("relation \"venta\" does not exist"));
  const r = await ejecutarTool(sql, "consulta_sql", { consulta: "SELECT * FROM venta" }, HOY);
  assertStringIncludes(r, "Error de SQL");
  assertStringIncludes(r, "does not exist");
});
