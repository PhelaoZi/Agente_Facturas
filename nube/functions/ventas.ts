// nube/functions/ventas.ts
// GET ?meses=N (default 6) -> { serie_mensual, ranking_clientes,
//                              ranking_productos }
import { db } from "./_shared/db.ts";
import { requireUser } from "./_shared/auth.ts";

export default async function handler(req: Request): Promise<Response> {
  const rechazo = await requireUser(req);
  if (rechazo) return rechazo;
  const url = new URL(req.url);
  const meses = Math.min(Math.max(Number(url.searchParams.get("meses")) || 6, 1), 24);
  const sql = db();

  const serie = await sql`
    SELECT date_trunc('month', fecha)::date AS mes,
           SUM(total_real) AS total, COUNT(*) AS n_facturas
    FROM v_ventas_reales
    WHERE fecha >= CURRENT_DATE - make_interval(months => ${meses})
    GROUP BY 1 ORDER BY 1`;
  const clientes = await sql`
    SELECT rut_cliente, razon_social_receptor AS razon_social,
           SUM(total_real) AS total
    FROM v_ventas_reales
    WHERE fecha >= CURRENT_DATE - make_interval(months => ${meses})
    GROUP BY 1, 2 ORDER BY total DESC LIMIT 10`;
  const productos = await sql`
    SELECT nombre_producto, SUM(cantidad) AS unidades
    FROM v_ventas_producto
    WHERE fecha >= CURRENT_DATE - make_interval(months => ${meses})
    GROUP BY 1 ORDER BY unidades DESC LIMIT 10`;

  return new Response(
    JSON.stringify({ serie_mensual: serie, ranking_clientes: clientes,
                     ranking_productos: productos }),
    { headers: { "Content-Type": "application/json" } },
  );
}
