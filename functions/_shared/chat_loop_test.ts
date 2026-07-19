// functions/_shared/chat_loop_test.ts
// El loop se testea con un modelo falso: respuestas en secuencia.
import { assertEquals, assertStringIncludes } from "jsr:@std/assert@1";
import { correrChat, MAX_ITERACIONES, type RespuestaModelo } from "./chat_loop.ts";

function modeloFalso(respuestas: RespuestaModelo[]) {
  const cola = [...respuestas];
  const llamadas: Record<string, unknown>[] = [];
  const fn = (body: Record<string, unknown>) => {
    llamadas.push(body);
    const r = cola.shift();
    if (!r) throw new Error("modelo falso sin respuestas");
    return Promise.resolve(r);
  };
  return { fn, llamadas };
}

const fin = (texto: string): RespuestaModelo => ({
  content: [{ type: "text", text: texto }],
  stop_reason: "end_turn",
  usage: { input_tokens: 100, output_tokens: 50 },
});

const pideTool = (nombre: string, input: Record<string, unknown>): RespuestaModelo => ({
  content: [{ type: "tool_use", id: "tu_1", name: nombre, input }],
  stop_reason: "tool_use",
  usage: { input_tokens: 200, output_tokens: 30 },
});

Deno.test("respuesta directa sin tools", async () => {
  const { fn } = modeloFalso([fin("Hola, soy el analista.")]);
  const r = await correrChat({
    system: "s", mensajes: [{ role: "user", content: "hola" }], tools: [],
    llamarModelo: fn, ejecutarTool: () => Promise.resolve(""),
  });
  assertEquals(r.texto, "Hola, soy el analista.");
  assertEquals(r.uso.input_tokens, 100);
  assertEquals(r.uso.n_llamadas_tools, 0);
});

Deno.test("una tool: ejecuta, enhebra tool_result y suma uso", async () => {
  const { fn, llamadas } = modeloFalso([
    pideTool("deuda_total", {}),
    fin("Te deben $350.000."),
  ]);
  const ejecutadas: string[] = [];
  const r = await correrChat({
    system: "s", mensajes: [{ role: "user", content: "cuanto me deben" }], tools: [],
    llamarModelo: fn,
    ejecutarTool: (nombre: string) => { ejecutadas.push(nombre); return Promise.resolve("Deuda: $350.000"); },
  });
  assertEquals(ejecutadas, ["deuda_total"]);
  assertEquals(r.texto, "Te deben $350.000.");
  assertEquals(r.uso.input_tokens, 300);
  assertEquals(r.uso.n_llamadas_tools, 1);
  // La 2a llamada al modelo lleva el tool_result enhebrado.
  const mensajes2 = llamadas[1].messages as { role: string; content: unknown }[];
  assertEquals(mensajes2.length, 3);  // user + assistant(tool_use) + user(tool_result)
  const ultimo = mensajes2[2].content as { type: string; tool_use_id: string }[];
  assertEquals(ultimo[0].type, "tool_result");
  assertEquals(ultimo[0].tool_use_id, "tu_1");
});

Deno.test("error de una tool va como tool_result is_error y el loop sigue", async () => {
  const { fn } = modeloFalso([
    pideTool("deuda_total", {}),
    fin("No pude consultar la deuda."),
  ]);
  const r = await correrChat({
    system: "s", mensajes: [{ role: "user", content: "x" }], tools: [],
    llamarModelo: fn,
    ejecutarTool: () => Promise.reject(new Error("BD caida")),
  });
  assertEquals(r.texto, "No pude consultar la deuda.");
});

Deno.test("tope de iteraciones corta el loop", async () => {
  const respuestas = Array.from({ length: MAX_ITERACIONES }, () => pideTool("deuda_total", {}));
  const { fn } = modeloFalso(respuestas);
  const r = await correrChat({
    system: "s", mensajes: [{ role: "user", content: "x" }], tools: [],
    llamarModelo: fn,
    ejecutarTool: () => Promise.resolve("dato"),
  });
  assertStringIncludes(r.texto, "tope de pasos");
  assertEquals(r.uso.n_llamadas_tools, MAX_ITERACIONES);
});
