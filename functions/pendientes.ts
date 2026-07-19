// functions/pendientes.ts
// GET -> { pendientes: [{folio, fecha, rut_cliente, razon_social, total,
//                        dias_desde_emision}], total }
import postgres from "npm:postgres@3.4.5";
import { jwtVerify } from "npm:jose@5";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With",
};

let sql: ReturnType<typeof postgres> | null = null;

function db() {
  if (!sql) {
    const url = Deno.env.get("INSFORGE_DB_URL");
    if (!url) throw new Error("Falta el secret INSFORGE_DB_URL");
    sql = postgres(url, { max: 1, prepare: false });
  }
  return sql;
}

function sin401(detalle: string): Response {
  return new Response(JSON.stringify({ error: "no autorizado", detalle }), {
    status: 401,
    headers: {
      ...corsHeaders,
      "Content-Type": "application/json",
    },
  });
}

async function requireUser(req: Request): Promise<Response | null> {
  const header = req.headers.get("Authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : null;
  if (!token) return sin401("falta token");
  try {
    const secreto = new TextEncoder().encode(
      Deno.env.get("INSFORGE_JWT_SECRET") ?? "",
    );
    await jwtVerify(token, secreto);
    return null;
  } catch {
    return sin401("token invalido");
  }
}

export default async function handler(req: Request): Promise<Response> {
  if (req.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: corsHeaders,
    });
  }

  const rechazo = await requireUser(req);
  if (rechazo) return rechazo;
  const sqlClient = db();

  const filas = await sqlClient`
    SELECT folio, fecha, rut_cliente, razon_social, total, dias_desde_emision
    FROM v_pendientes ORDER BY fecha`;
  const total = filas.reduce((s, f) => s + Number(f.total), 0);

  return new Response(
    JSON.stringify({ pendientes: filas, total }),
    {
      status: 200,
      headers: {
        ...corsHeaders,
        "Content-Type": "application/json",
      },
    }
  );
}
