// functions/ventas.ts
// GET ?meses=N (default 6) -> { serie_mensual, ranking_clientes,
//                              ranking_productos }
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
  
  const url = new URL(req.url);
  const meses = Math.min(Math.max(Number(url.searchParams.get("meses")) || 6, 1), 24);
  const sqlClient = db();

  const serie = await sqlClient`
    SELECT date_trunc('month', fecha)::date AS mes,
           SUM(total_real) AS total, COUNT(*) AS n_facturas
    FROM v_ventas_reales
    WHERE fecha >= CURRENT_DATE - make_interval(months => ${meses})
    GROUP BY 1 ORDER BY 1`;
  const clientes = await sqlClient`
    SELECT rut_cliente, razon_social_receptor AS razon_social,
           SUM(total_real) AS total
    FROM v_ventas_reales
    WHERE fecha >= CURRENT_DATE - make_interval(months => ${meses})
    GROUP BY 1, 2 ORDER BY total DESC LIMIT 10`;
  const productos = await sqlClient`
    SELECT nombre_producto, SUM(cantidad) AS unidades
    FROM v_ventas_producto
    WHERE fecha >= CURRENT_DATE - make_interval(months => ${meses})
    GROUP BY 1 ORDER BY unidades DESC LIMIT 10`;

  return new Response(
    JSON.stringify({
      serie_mensual: serie,
      ranking_clientes: clientes,
      ranking_productos: productos
    }),
    {
      status: 200,
      headers: {
        ...corsHeaders,
        "Content-Type": "application/json",
      },
    }
  );
}
