// nube/functions/flujo.ts
// GET -> resultado de proyectarFlujo con datos de las views + sync_meta.
import { db } from "./_shared/db.ts";
import { requireUser } from "./_shared/auth.ts";
import { proyectarFlujo, type FacturaPendiente, type Gasto } from "./_shared/flujo.ts";

export default async function handler(req: Request): Promise<Response> {
  const rechazo = await requireUser(req);
  if (rechazo) return rechazo;
  const sql = db();

  const facturas = await sql`
    SELECT folio, fecha, rut_cliente, razon_social_receptor, monto
    FROM v_flujo_pendientes ORDER BY fecha`;
  const avgs = await sql`SELECT rut_cliente, avg_dias FROM v_dias_pago_cliente`;
  const gastos = await sql`
    SELECT descripcion, proveedor, monto, fecha_vencimiento, categoria,
           recurrente, periodicidad
    FROM cuentas_por_pagar WHERE pagado = FALSE`;
  const [meta] = await sql`
    SELECT valor FROM sync_meta WHERE clave = 'saldo_banco'`;

  const avgDias = Object.fromEntries(
    avgs.map((a) => [a.rut_cliente, Number(a.avg_dias)]),
  );
  const resultado = proyectarFlujo(
    facturas.map((f): FacturaPendiente => ({
      folio: Number(f.folio), fecha: new Date(f.fecha),
      rut_cliente: f.rut_cliente,
      razon_social_receptor: f.razon_social_receptor,
      monto: Number(f.monto),
    })),
    avgDias,
    gastos.map((g): Gasto => ({
      descripcion: g.descripcion, proveedor: g.proveedor,
      monto: Number(g.monto), fecha_vencimiento: new Date(g.fecha_vencimiento),
      categoria: g.categoria, recurrente: g.recurrente ?? false,
      periodicidad: g.periodicidad,
    })),
    Number(meta?.valor?.saldo ?? 0),
    new Date(),
  );
  return new Response(JSON.stringify(resultado), {
    headers: { "Content-Type": "application/json" },
  });
}
