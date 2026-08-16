// functions/_shared/chat_prompt_test.ts
// El prompt es un template literal gigante y hasta el 2026-08-16 NINGUN test de
// Deno lo importaba: los tests de Python lo leen como TEXTO, asi que un error de
// sintaxis de TypeScript pasaba entero. Uno se colo (un backtick dentro del
// template literal, que corta el string), `deno bundle` fallo, y el deploy
// subio el bundle ANTERIOR sin avisar. El chat quedo desplegado con la version
// vieja y todo parecia bien.
//
// Con solo importar este modulo el test ya cubre lo que fallo: si el archivo no
// compila, no hay suite que pase.
import { assertEquals, assertStringIncludes } from "jsr:@std/assert@1";
import { promptChat } from "./chat_prompt.ts";

Deno.test("el prompt compila y produce un string", () => {
  const p = promptChat("2026-08-16", "2026-08-16 09:00");
  assertEquals(typeof p, "string");
  assertEquals(p.length > 500, true);
});

Deno.test("el prompt situa al modelo: fecha, replica y ultimo sync", () => {
  const p = promptChat("2026-08-16", "2026-08-16 09:00");
  assertStringIncludes(p, "2026-08-16");
  assertStringIncludes(p, "REPLICA");
  assertStringIncludes(p, "09:00");
});

Deno.test("sin registro de sync el prompt manda advertir, no callar", () => {
  // Una cifra vieja presentada como fresca es peor que una advertencia.
  const p = promptChat("2026-08-16", null);
  assertStringIncludes(p.toLowerCase(), "desactualizado");
});

Deno.test("el prompt nombra las dos fuentes por producto y las separa", () => {
  // El defecto que costo semanas: unidades y dinero salen de vistas distintas.
  const p = promptChat("2026-08-16", null);
  assertStringIncludes(p, "v_ingreso_producto");   // dinero
  assertStringIncludes(p, "v_lineas_producto");    // unidades
  assertStringIncludes(p, "nunca para dinero");
});
