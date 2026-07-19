// functions/_shared/openai_chat_loop_test.ts
// Tests unitarios para el loop de chat de OpenAI / OpenRouter.
import { assertEquals, assertStringIncludes } from "jsr:@std/assert@1";
import {
  correrChatOpenAi,
  MAX_ITERACIONES,
  normalizarRespuestaGateway,
  type OpenAiRespuesta,
} from "./openai_chat_loop.ts";

function modeloFalso(respuestas: OpenAiRespuesta[]) {
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

const fin = (texto: string): OpenAiRespuesta => ({
  choices: [{
    message: { role: "assistant", content: texto },
    finish_reason: "stop"
  }],
  usage: { prompt_tokens: 100, completion_tokens: 50 }
});

const pideTool = (nombre: string, args: Record<string, unknown>): OpenAiRespuesta => ({
  choices: [{
    message: {
      role: "assistant",
      content: null,
      tool_calls: [{
        id: "call_1",
        type: "function",
        function: {
          name: nombre,
          arguments: JSON.stringify(args)
        }
      }]
    },
    finish_reason: "tool_calls"
  }],
  usage: { prompt_tokens: 200, completion_tokens: 30 }
});

Deno.test("OpenAI loop: respuesta directa sin tools", async () => {
  const { fn } = modeloFalso([fin("Hola, soy el analista.")]);
  const r = await correrChatOpenAi({
    system: "s", mensajes: [{ role: "user", content: "hola" }], tools: [],
    llamarModelo: fn, ejecutarTool: () => Promise.resolve("")
  });
  assertEquals(r.texto, "Hola, soy el analista.");
  assertEquals(r.uso.input_tokens, 100);
  assertEquals(r.uso.n_llamadas_tools, 0);
});

Deno.test("OpenAI loop: una tool, ejecuta, enhebra tool_result y suma uso", async () => {
  const { fn, llamadas } = modeloFalso([
    pideTool("ventas_total", {}),
    fin("Las ventas son $350.000.")
  ]);
  const ejecutadas: string[] = [];
  const r = await correrChatOpenAi({
    system: "s", mensajes: [{ role: "user", content: "cuanto vendimos" }], tools: [{ name: "ventas_total", description: "Ventas...", input_schema: { type: "object" } }],
    llamarModelo: fn,
    ejecutarTool: (nombre) => { ejecutadas.push(nombre); return Promise.resolve("Ventas: $350.000"); }
  });
  assertEquals(ejecutadas, ["ventas_total"]);
  assertEquals(r.texto, "Las ventas son $350.000.");
  assertEquals(r.uso.input_tokens, 300);
  assertEquals(r.uso.n_llamadas_tools, 1);

  // La 2da llamada al modelo contiene el historial de mensajes correcto (incluyendo el tool result)
  const mensajes2 = llamadas[1].messages as any[];
  assertEquals(mensajes2.length, 4); // system + user + assistant(tool_call) + tool(result)
  assertEquals(mensajes2[3].role, "tool");
  assertEquals(mensajes2[3].tool_call_id, "call_1");
  assertEquals(mensajes2[3].content, "Ventas: $350.000");
});

Deno.test("Gateway: normaliza respuesta de solo texto a formato OpenAI", () => {
  const r = normalizarRespuestaGateway({
    text: "El total es $4.267.294.",
    metadata: { model: "google/gemini-2.5-flash",
                usage: { promptTokens: 62, completionTokens: 27, totalTokens: 89 } },
  });
  assertEquals(r.choices[0].message.content, "El total es $4.267.294.");
  assertEquals(r.choices[0].finish_reason, "stop");
  assertEquals(r.choices[0].message.tool_calls, undefined);
  assertEquals(r.usage.prompt_tokens, 62);
  assertEquals(r.usage.completion_tokens, 27);
});

Deno.test("Gateway: normaliza tool_calls (texto vacio pasa a null)", () => {
  const r = normalizarRespuestaGateway({
    text: "",
    tool_calls: [{ id: "tool_deuda_total_x", type: "function",
                   function: { name: "deuda_total", arguments: "{}" } }],
    metadata: { usage: { promptTokens: 34, completionTokens: 4 } },
  });
  assertEquals(r.choices[0].finish_reason, "tool_calls");
  assertEquals(r.choices[0].message.content, null);
  assertEquals(r.choices[0].message.tool_calls?.[0].function.name, "deuda_total");
  assertEquals(r.usage.prompt_tokens, 34);
});

Deno.test("Gateway: usage ausente normaliza a ceros", () => {
  const r = normalizarRespuestaGateway({ text: "hola" });
  assertEquals(r.usage.prompt_tokens, 0);
  assertEquals(r.usage.completion_tokens, 0);
});

Deno.test("OpenAI loop: tope de iteraciones corta el loop", async () => {
  const respuestas = Array.from({ length: MAX_ITERACIONES }, () => pideTool("ventas_total", {}));
  const { fn } = modeloFalso(respuestas);
  const r = await correrChatOpenAi({
    system: "s", mensajes: [{ role: "user", content: "x" }], tools: [{ name: "ventas_total", description: "Ventas...", input_schema: { type: "object" } }],
    llamarModelo: fn,
    ejecutarTool: () => Promise.resolve("dato")
  });
  assertStringIncludes(r.texto, "tope de pasos");
  assertEquals(r.uso.n_llamadas_tools, MAX_ITERACIONES);
});
