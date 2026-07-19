// functions/kpis.ts
// GET -> { ventas_mes, por_cobrar, n_pendientes, n_vencidas, monto_vencido,
//          saldo_banco, ultimo_sync }
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

  const [ventasMes] = await sqlClient`
    SELECT COALESCE(SUM(total_real), 0) AS total
    FROM v_ventas_reales
    WHERE date_trunc('month', fecha) = date_trunc('month', CURRENT_DATE)`;
  const [cobrar] = await sqlClient`
    SELECT COALESCE(SUM(total), 0) AS total, COUNT(*) AS n,
           COUNT(*) FILTER (WHERE dias_desde_emision > 30) AS n_vencidas,
           COALESCE(SUM(total) FILTER (WHERE dias_desde_emision > 30), 0) AS monto_vencido
    FROM v_pendientes`;
  const meta = await sqlClient`SELECT clave, valor FROM sync_meta`;
  const porClave = Object.fromEntries(meta.map((m) => [m.clave, m.valor]));

  return new Response(
    JSON.stringify({
      ventas_mes: Number(ventasMes.total),
      por_cobrar: Number(cobrar.total),
      n_pendientes: Number(cobrar.n),
      n_vencidas: Number(cobrar.n_vencidas),
      monto_vencido: Number(cobrar.monto_vencido),
      saldo_banco: porClave.saldo_banco ?? null,
      ultimo_sync: porClave.ultimo_sync ?? null,
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
