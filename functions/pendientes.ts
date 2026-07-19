// nube/functions/pendientes.ts
// GET -> { pendientes: [{folio, fecha, rut_cliente, razon_social, total,
//                        dias_desde_emision}], total }
import { db } from "./_shared/db.ts";
import { requireUser } from "./_shared/auth.ts";

export default async function handler(req: Request): Promise<Response> {
  const rechazo = await requireUser(req);
  if (rechazo) return rechazo;
  const sql = db();
  const filas = await sql`
    SELECT folio, fecha, rut_cliente, razon_social, total, dias_desde_emision
    FROM v_pendientes ORDER BY fecha`;
  const total = filas.reduce((s, f) => s + Number(f.total), 0);
  return new Response(JSON.stringify({ pendientes: filas, total }), {
    headers: { "Content-Type": "application/json" },
  });
}
