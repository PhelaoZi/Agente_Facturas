// functions/chat.ts
// POST {mensaje, sesion_id?} -> {respuesta, sesion_id, uso}
// Unica function con escritura en la nube: chat_sesiones (historial) y
// chat_uso (log de costo, base del tope diario). Fuente modular: se despliega
// empaquetada con `deno bundle` (ver scripts de deploy en el plan Fase 4).
import { corsHeaders } from "./_shared/cors.ts";
import { db } from "./_shared/db.ts";
import { requireUser } from "./_shared/auth.ts";
import { TOOLS, ejecutarTool } from "./_shared/chat_tools.ts";
import { correrChatOpenAi, llamarModeloOpenRouter, type OpenAiMessage } from "./_shared/openai_chat_loop.ts";
import { promptChat } from "./_shared/chat_prompt.ts";

const MAX_LARGO_MENSAJE = 2000;
const MAX_HISTORIAL_API = 20;   // mensajes enviados a la API (el historial completo queda en BD)
const MODELO_DEFAULT = "google/gemini-2.5-flash";

interface MensajeGuardado { role: "user" | "assistant"; content: string }

function json(cuerpo: unknown, status: number): Response {
  return new Response(JSON.stringify(cuerpo), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

export default async function handler(req: Request): Promise<Response> {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders });
  if (req.method !== "POST") return json({ error: "solo POST" }, 405);

  const rechazo = await requireUser(req);
  if (rechazo) return rechazo;

  const apiKey = Deno.env.get("OPENROUTER_API_KEY");
  if (!apiKey) return json({ error: "falta OPENROUTER_API_KEY en el servidor" }, 500);

  let cuerpo: { mensaje?: string; sesion_id?: number };
  try {
    cuerpo = await req.json();
  } catch {
    return json({ error: "body invalido: se espera JSON {mensaje, sesion_id?}" }, 400);
  }
  const mensaje = (cuerpo.mensaje ?? "").trim();
  if (!mensaje) return json({ error: "mensaje vacio" }, 400);
  if (mensaje.length > MAX_LARGO_MENSAJE) return json({ error: "mensaje demasiado largo" }, 400);

  const sql = db();

  // Tope de gasto diario: red de seguridad ante loops o uso descontrolado.
  const limiteDiario = Number(Deno.env.get("CHAT_LIMITE_DIARIO_USD") ?? "1.0");
  const [gasto] = await sql`
    SELECT COALESCE(SUM(costo_usd), 0) AS hoy
    FROM chat_uso WHERE creado >= date_trunc('day', now())`;
  if (Number(gasto.hoy) >= limiteDiario) {
    return json({
      error: "limite_diario",
      detalle: `Tope diario de US$${limiteDiario} alcanzado. Vuelve manana ` +
        `o sube CHAT_LIMITE_DIARIO_USD en la configuracion de la function.`,
    }, 429);
  }

  // Sesion: cargar la existente o crear una nueva.
  let sesionId = cuerpo.sesion_id ?? null;
  let historial: MensajeGuardado[] = [];
  if (sesionId) {
    const [s] = await sql`SELECT mensajes FROM chat_sesiones WHERE id = ${sesionId}`;
    if (s) {
      const rawMensajes = s.mensajes;
      if (Array.isArray(rawMensajes)) {
        historial = rawMensajes as MensajeGuardado[];
      } else if (typeof rawMensajes === "string") {
        try {
          historial = JSON.parse(rawMensajes) as MensajeGuardado[];
        } catch {
          historial = [];
        }
      }
    } else {
      sesionId = null;   // id desconocido (ej: replica recreada): sesion nueva
    }
  }
  if (!sesionId) {
    const [s] = await sql`INSERT INTO chat_sesiones (mensajes) VALUES ('[]'::jsonb) RETURNING id`;
    sesionId = Number(s.id);
  }

  const meta = await sql`SELECT valor FROM sync_meta WHERE clave = 'ultimo_sync'`;
  const ultimoSync = (meta[0]?.valor as { momento?: string } | undefined)?.momento ?? null;
  const hoy = new Date().toISOString().slice(0, 10);
  const modelo = Deno.env.get("CHAT_MODELO") ?? MODELO_DEFAULT;

  const mensajesAPI: OpenAiMessage[] = [
    ...historial.slice(-MAX_HISTORIAL_API).map((h) => ({
      role: h.role,
      content: h.content,
    })),
    { role: "user", content: mensaje },
  ];

  let texto: string;
  let uso: { input_tokens: number; output_tokens: number; n_llamadas_tools: number };
  try {
    ({ texto, uso } = await correrChatOpenAi({
      system: promptChat(hoy, ultimoSync),
      mensajes: mensajesAPI,
      tools: TOOLS,
      llamarModelo: llamarModeloOpenRouter(apiKey, modelo),
      ejecutarTool: (nombre, input) => ejecutarTool(sql as any, nombre, input, new Date()),
    }));
  } catch (e) {
    console.error("chat: fallo el loop:", (e as Error).message);
    return json({ error: `El chat fallo: ${(e as Error).message}` }, 502);
  }

  // Costo estimado (para el tope diario; precios por MTok configurables).
  // Los defaults son para Gemini 2.5 Flash: ~US$0.075 por Millón de tokens (usamos US$0.15 y US$0.60 como margen de seguridad)
  const precioIn = Number(Deno.env.get("CHAT_PRECIO_IN_USD_MTOK") ?? "0.15");
  const precioOut = Number(Deno.env.get("CHAT_PRECIO_OUT_USD_MTOK") ?? "0.60");
  const costo = (uso.input_tokens * precioIn + uso.output_tokens * precioOut) / 1_000_000;

  // Persistir: historial completo en la sesion + fila de uso.
  const nuevoHistorial: MensajeGuardado[] = [
    ...historial,
    { role: "user", content: mensaje },
    { role: "assistant", content: texto },
  ];
  await sql`
    UPDATE chat_sesiones
    SET mensajes = ${JSON.stringify(nuevoHistorial)}::jsonb, actualizado = now()
    WHERE id = ${sesionId}`;
  await sql`
    INSERT INTO chat_uso (sesion_id, modelo, input_tokens, output_tokens,
                          n_llamadas_tools, costo_usd)
    VALUES (${sesionId}, ${modelo}, ${uso.input_tokens}, ${uso.output_tokens},
            ${uso.n_llamadas_tools}, ${costo})`;

  return json({ respuesta: texto, sesion_id: sesionId, uso: { ...uso, costo_usd: costo } }, 200);
}
