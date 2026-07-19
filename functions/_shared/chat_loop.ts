// functions/_shared/chat_loop.ts
// Tool-use loop generico sobre la Messages API de Anthropic. `llamarModelo` es
// inyectable: los tests usan un modelo falso; produccion usa llamarModeloReal
// (fetch directo, sin SDK: menos dependencias en el runtime edge).

export interface BloqueContenido {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
  tool_use_id?: string;
  content?: unknown;
  is_error?: boolean;
}

export interface RespuestaModelo {
  content: BloqueContenido[];
  stop_reason: string;
  usage: { input_tokens: number; output_tokens: number };
}

export interface MensajeAPI {
  role: "user" | "assistant";
  content: string | BloqueContenido[];
}

export interface UsoChat {
  input_tokens: number;
  output_tokens: number;
  n_llamadas_tools: number;
}

export const MAX_ITERACIONES = 8;
const MAX_TOKENS = 1024;

export async function correrChat(opts: {
  system: string;
  mensajes: MensajeAPI[];
  tools: unknown[];
  llamarModelo: (body: Record<string, unknown>) => Promise<RespuestaModelo>;
  ejecutarTool: (nombre: string, input: Record<string, unknown>) => Promise<string>;
  maxIteraciones?: number;
  maxTokens?: number;
}): Promise<{ texto: string; uso: UsoChat }> {
  const mensajes = [...opts.mensajes];
  const uso: UsoChat = { input_tokens: 0, output_tokens: 0, n_llamadas_tools: 0 };
  const tope = opts.maxIteraciones ?? MAX_ITERACIONES;

  for (let i = 0; i < tope; i++) {
    const resp = await opts.llamarModelo({
      system: opts.system,
      tools: opts.tools,
      messages: mensajes,
      max_tokens: opts.maxTokens ?? MAX_TOKENS,
    });
    uso.input_tokens += resp.usage.input_tokens;
    uso.output_tokens += resp.usage.output_tokens;

    if (resp.stop_reason !== "tool_use") {
      const texto = resp.content
        .filter((b) => b.type === "text")
        .map((b) => b.text ?? "")
        .join("\n").trim();
      return { texto, uso };
    }

    mensajes.push({ role: "assistant", content: resp.content });
    const resultados: BloqueContenido[] = [];
    for (const b of resp.content) {
      if (b.type !== "tool_use") continue;
      uso.n_llamadas_tools++;
      let contenido: string;
      let esError = false;
      try {
        contenido = await opts.ejecutarTool(b.name ?? "", b.input ?? {});
      } catch (e) {
        contenido = `Error ejecutando ${b.name}: ${(e as Error).message}`;
        esError = true;
      }
      resultados.push({
        type: "tool_result",
        tool_use_id: b.id ?? "",
        content: contenido,
        ...(esError ? { is_error: true } : {}),
      });
    }
    mensajes.push({ role: "user", content: resultados });
  }

  return {
    texto: "No alcance a terminar la consulta (tope de pasos). Intenta una pregunta mas acotada.",
    uso,
  };
}

/** Cliente real de la Messages API. Reintenta 429/5xx/529 (esperas 1s y 3s). */
export function llamarModeloReal(apiKey: string, modelo: string) {
  return async (body: Record<string, unknown>): Promise<RespuestaModelo> => {
    const esperas = [0, 1000, 3000];
    let ultimo = "";
    for (const espera of esperas) {
      if (espera) await new Promise((r) => setTimeout(r, espera));
      const res = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "x-api-key": apiKey,
          "anthropic-version": "2023-06-01",
          "content-type": "application/json",
        },
        body: JSON.stringify({ model: modelo, ...body }),
      });
      if (res.ok) return await res.json() as RespuestaModelo;
      ultimo = `${res.status}: ${(await res.text()).slice(0, 300)}`;
      // 4xx distinto de 429 no se reintenta (key mala, request invalido...).
      if (res.status !== 429 && res.status < 500) break;
    }
    throw new Error(`La API de Anthropic fallo (${ultimo})`);
  };
}
