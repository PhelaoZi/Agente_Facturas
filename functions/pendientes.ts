// functions/pendientes.ts
// GET -> { pendientes: [{folio, fecha, rut_cliente, razon_social, total,
//                        dias_desde_emision}], total }
import postgres from "npm:postgres@3.4.5";
import { jwtVerify, importSPKI } from "npm:jose@5";

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
    const parts = token.split(".");
    if (parts.length !== 3) return sin401("token malformado");
    const headerObj = JSON.parse(atob(parts[0]));
    const alg = headerObj.alg;

    if (alg === "RS256") {
      const publicKeyPEM = Deno.env.get("JWT_PUBLIC_KEY");
      if (!publicKeyPEM) {
        console.error("requireUser: falta JWT_PUBLIC_KEY en el servidor");
        return sin401("error de configuracion en servidor");
      }
      const publicKey = await importSPKI(publicKeyPEM, "RS256");
      await jwtVerify(token, publicKey, { algorithms: ["RS256"] });
    } else if (alg === "HS256") {
      const secretStr = Deno.env.get("INSFORGE_JWT_SECRET") ?? Deno.env.get("JWT_SECRET") ?? "";
      if (!secretStr) {
        console.error("requireUser: falta INSFORGE_JWT_SECRET en el servidor");
        return sin401("error de configuracion en servidor");
      }
      await jwtVerify(token, new TextEncoder().encode(secretStr), { algorithms: ["HS256"] });
    } else {
      return sin401("algoritmo no soportado");
    }
    return null;
  } catch (err) {
    console.error("requireUser: token invalido:", (err as Error)?.message ?? err);
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
