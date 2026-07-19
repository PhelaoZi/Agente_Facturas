// nube/functions/kpis.ts
// GET -> { ventas_mes, por_cobrar, n_pendientes, n_vencidas, monto_vencido,
//          saldo_banco, ultimo_sync }
import { db } from "./_shared/db.ts";
import { requireUser } from "./_shared/auth.ts";

export default async function handler(req: Request): Promise<Response> {
  const rechazo = await requireUser(req);
  if (rechazo) return rechazo;
  const sql = db();

  const [ventasMes] = await sql`
    SELECT COALESCE(SUM(total_real), 0) AS total
    FROM v_ventas_reales
    WHERE date_trunc('month', fecha) = date_trunc('month', CURRENT_DATE)`;
  const [cobrar] = await sql`
    SELECT COALESCE(SUM(total), 0) AS total, COUNT(*) AS n,
           COUNT(*) FILTER (WHERE dias_desde_emision > 30) AS n_vencidas,
           COALESCE(SUM(total) FILTER (WHERE dias_desde_emision > 30), 0) AS monto_vencido
    FROM v_pendientes`;
  const meta = await sql`SELECT clave, valor FROM sync_meta`;
  const porClave = Object.fromEntries(meta.map((m) => [m.clave, m.valor]));

  return json({
    ventas_mes: Number(ventasMes.total),
    por_cobrar: Number(cobrar.total),
    n_pendientes: Number(cobrar.n),
    n_vencidas: Number(cobrar.n_vencidas),
    monto_vencido: Number(cobrar.monto_vencido),
    saldo_banco: porClave.saldo_banco ?? null,
    ultimo_sync: porClave.ultimo_sync ?? null,
  });
}

function json(data: unknown): Response {
  return new Response(JSON.stringify(data), {
    headers: { "Content-Type": "application/json" },
  });
}
