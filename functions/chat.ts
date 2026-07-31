// functions/chat.ts
// POST {mensaje, sesion_id?} -> {respuesta, sesion_id, uso}
// Unica function con escritura en la nube: chat_sesiones (historial) y
// chat_uso (log de costo, base del tope diario). Fuente modular: se despliega
// empaquetada con `deno bundle` (ver scripts de deploy en el plan Fase 4).
import { corsHeaders } from "./_shared/cors.ts";
import { db } from "./_shared/db.ts";
import { requireUser } from "./_shared/auth.ts";
import { TOOLS, ejecutarTool, type SqlCliente } from "./_shared/chat_tools.ts";
import { correrChatOpenAi, llamarModeloGatewayInsforge, llamarModeloOpenRouter, type OpenAiMessage } from "./_shared/openai_chat_loop.ts";
import { promptChat } from "./_shared/chat_prompt.ts";

const MAX_LARGO_MENSAJE = 2000;
const MAX_HISTORIAL_API = 20;   // mensajes enviados a la API (el historial completo queda en BD)
const MODELO_DEFAULT = "google/gemini-2.5-flash";
// Host del proyecto InsForge (mismo default hardcodeado que usa la PWA en api.ts).
const AI_URL_DEFAULT = "https://z86cmn8g.us-west.insforge.app";

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

  // Proveedor del modelo. Con OPENROUTER_API_KEY se va directo a OpenRouter,
  // contra la cuenta del negocio. El gateway de InsForge queda de respaldo:
  // cobra sobre una credencial que este proyecto no administra y, al agotarse
  // los creditos del plan, responde 401 AI_INVALID_API_KEY sin mas aviso.
  const orKey = Deno.env.get("OPENROUTER_API_KEY");
  const gatewayKey = Deno.env.get("INSFORGE_AI_KEY");
  if (!orKey && !gatewayKey) {
    return json({ error: "falta OPENROUTER_API_KEY o INSFORGE_AI_KEY en el servidor" }, 500);
  }
  const aiUrl = Deno.env.get("INSFORGE_AI_URL") ?? AI_URL_DEFAULT;

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
  // Best-effort (chequear-y-luego-insertar, sin lock): requests concurrentes
  // pueden sobrepasarlo por lo que este en vuelo — aceptado con un solo
  // usuario; el tope duro real es el limite mensual del proveedor.
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
  // Validado arriba: si no hay orKey, gatewayKey existe.
  const llamarModelo = orKey
    ? llamarModeloOpenRouter(orKey, modelo)
    : llamarModeloGatewayInsforge(aiUrl, gatewayKey!, modelo);

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
      llamarModelo,
      // El tipo Sql del driver postgres es estructuralmente mas rico que el
      // SqlCliente minimo que declaran las tools; el cast queda acotado aqui.
      ejecutarTool: (nombre, input) => ejecutarTool(sql as unknown as SqlCliente, nombre, input, new Date()),
    }));
  } catch (e) {
    console.error("chat: fallo el loop:", (e as Error).message);
    return json({ error: `El chat fallo: ${(e as Error).message}` }, 502);
  }

  // Costo estimado (para el tope diario; precios por MTok configurables).
  // Defaults = precio de gemini-2.5-flash en el catalogo del gateway InsForge
  // (GET /api/ai/models): input US$0.30/MTok, output US$2.50/MTok.
  const precioIn = Number(Deno.env.get("CHAT_PRECIO_IN_USD_MTOK") ?? "0.30");
  const precioOut = Number(Deno.env.get("CHAT_PRECIO_OUT_USD_MTOK") ?? "2.50");
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
