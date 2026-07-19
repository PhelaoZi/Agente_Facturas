// functions/_shared/openai_chat_loop.ts
// Tool-use loop compatible con el estándar de OpenAI / OpenRouter.
// Permite usar modelos más económicos de diversos proveedores (Google, Llama, Mistral, etc.)
// a través del Model Gateway de InsForge.

export interface OpenAiMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string | null;
  name?: string;
  tool_calls?: {
    id: string;
    type: "function";
    function: {
      name: string;
      arguments: string;
    };
  }[];
  tool_call_id?: string;
}

export interface OpenAiRespuesta {
  choices: {
    message: {
      role: "assistant";
      content: string | null;
      tool_calls?: {
        id: string;
        type: "function";
        function: {
          name: string;
          arguments: string;
        };
      }[];
    };
    finish_reason: string;
  }[];
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
  };
}

export interface UsoChat {
  input_tokens: number;
  output_tokens: number;
  n_llamadas_tools: number;
}

export const MAX_ITERACIONES = 8;
const MAX_TOKENS = 1024;

export async function correrChatOpenAi(opts: {
  system: string;
  mensajes: OpenAiMessage[];
  tools: any[];
  llamarModelo: (body: Record<string, unknown>) => Promise<OpenAiRespuesta>;
  ejecutarTool: (nombre: string, input: Record<string, unknown>) => Promise<string>;
  maxIteraciones?: number;
  maxTokens?: number;
}): Promise<{ texto: string; uso: UsoChat }> {
  // Convertir dinámicamente las herramientas del formato Anthropic (name, description, input_schema)
  // al formato de funciones de OpenAI.
  const openAiTools = opts.tools.map((t) => ({
    type: "function",
    function: {
      name: t.name,
      description: t.description,
      parameters: t.input_schema,
    },
  }));

  const mensajes: OpenAiMessage[] = [
    { role: "system", content: opts.system },
    ...opts.mensajes,
  ];

  const uso: UsoChat = { input_tokens: 0, output_tokens: 0, n_llamadas_tools: 0 };
  const tope = opts.maxIteraciones ?? MAX_ITERACIONES;

  for (let i = 0; i < tope; i++) {
    const resp = await opts.llamarModelo({
      messages: [...mensajes],
      tools: openAiTools.length > 0 ? openAiTools : undefined,
      max_tokens: opts.maxTokens ?? MAX_TOKENS,
    });

    uso.input_tokens += resp.usage.prompt_tokens;
    uso.output_tokens += resp.usage.completion_tokens;

    const choice = resp.choices[0];
    if (!choice) {
      throw new Error("La respuesta del modelo no contiene choices.");
    }
    const msg = choice.message;
    mensajes.push(msg);

    // Si no solicita llamadas a herramientas o finaliza normalmente, devolvemos el texto
    if (choice.finish_reason !== "tool_calls" || !msg.tool_calls || msg.tool_calls.length === 0) {
      return { texto: msg.content ?? "", uso };
    }

    // Procesar cada llamada a herramienta solicitada por el modelo
    for (const tc of msg.tool_calls) {
      uso.n_llamadas_tools++;
      let input: Record<string, unknown> = {};
      try {
        // En OpenAI, los argumentos vienen serializados como una cadena JSON.
        input = JSON.parse(tc.function.arguments);
      } catch (e) {
        console.error(`Error parseando argumentos de ${tc.function.name}:`, (e as Error).message);
      }

      let contenido: string;
      try {
        contenido = await opts.ejecutarTool(tc.function.name, input);
      } catch (e) {
        contenido = `Error ejecutando ${tc.function.name}: ${(e as Error).message}`;
      }

      mensajes.push({
        role: "tool",
        tool_call_id: tc.id,
        name: tc.function.name,
        content: contenido,
      });
    }
  }

  return {
    texto: "No alcancé a terminar la consulta (tope de pasos). Intenta una pregunta más acotada.",
    uso,
  };
}

// --- AI Gateway de InsForge (proveedor elegido, 2026-07-19) ---
// El gateway del proyecto expone POST {base}/api/ai/chat/completion: acepta el
// body estilo OpenAI (messages con roles assistant/tool, tools) pero responde
// un formato propio plano: {text, tool_calls?, metadata.usage.promptTokens...}.
// Este adaptador lo normaliza a OpenAiRespuesta para reutilizar el mismo loop.

export interface GatewayRespuesta {
  text: string | null;
  tool_calls?: OpenAiMessage["tool_calls"];
  metadata?: {
    model?: string;
    usage?: { promptTokens?: number; completionTokens?: number; totalTokens?: number };
  };
}

export function normalizarRespuestaGateway(g: GatewayRespuesta): OpenAiRespuesta {
  const toolCalls = g.tool_calls && g.tool_calls.length > 0 ? g.tool_calls : undefined;
  return {
    choices: [{
      message: { role: "assistant", content: g.text || null, tool_calls: toolCalls },
      finish_reason: toolCalls ? "tool_calls" : "stop",
    }],
    usage: {
      prompt_tokens: g.metadata?.usage?.promptTokens ?? 0,
      completion_tokens: g.metadata?.usage?.completionTokens ?? 0,
    },
  };
}

/** Cliente del AI Gateway de InsForge. Reintenta 429/5xx (esperas 1s y 3s). */
export function llamarModeloGatewayInsforge(baseUrl: string, apiKey: string, modelo: string) {
  const url = `${baseUrl.replace(/\/$/, "")}/api/ai/chat/completion`;
  return async (body: Record<string, unknown>): Promise<OpenAiRespuesta> => {
    const esperas = [0, 1000, 3000];
    let ultimo = "";
    for (const espera of esperas) {
      if (espera) await new Promise((r) => setTimeout(r, espera));
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ model: modelo, ...body }),
      });
      if (res.ok) return normalizarRespuestaGateway(await res.json() as GatewayRespuesta);
      ultimo = `${res.status}: ${(await res.text()).slice(0, 300)}`;
      // 4xx distinto de 429 no se reintenta
      if (res.status !== 429 && res.status < 500) break;
    }
    throw new Error(`AI Gateway de InsForge falló (${ultimo})`);
  };
}

/** Cliente de OpenRouter directo (alternativa; el gateway es el camino por defecto). */
export function llamarModeloOpenRouter(apiKey: string, modelo: string) {
  return async (body: Record<string, unknown>): Promise<OpenAiRespuesta> => {
    const esperas = [0, 1000, 3000];
    let ultimo = "";
    for (const espera of esperas) {
      if (espera) await new Promise((r) => setTimeout(r, espera));
      const res = await fetch("https://openrouter.ai/api/v1/chat/completions", {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ model: modelo, ...body }),
      });
      if (res.ok) return await res.json() as OpenAiRespuesta;
      ultimo = `${res.status}: ${(await res.text()).slice(0, 300)}`;
      // 4xx distinto de 429 no se reintenta
      if (res.status !== 429 && res.status < 500) break;
    }
    throw new Error(`OpenRouter API falló (${ultimo})`);
  };
}
