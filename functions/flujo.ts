// functions/flujo.ts
// GET -> resultado de proyectarFlujo con datos de las views + sync_meta.
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
    const parts = token.split('.');
    if (parts.length !== 3) return sin401("token malformado");
    const headerObj = JSON.parse(atob(parts[0]));
    const alg = headerObj.alg;

    if (alg === "RS256") {
      const publicKeyPEM = Deno.env.get("JWT_PUBLIC_KEY");
      if (!publicKeyPEM) {
        console.error("RequireUser: JWT_PUBLIC_KEY not set in environment");
        return sin401("error de configuracion en servidor");
      }
      const publicKey = await importSPKI(publicKeyPEM, "RS256");
      await jwtVerify(token, publicKey);
    } else if (alg === "HS256") {
      const secretStr = Deno.env.get("INSFORGE_JWT_SECRET") ?? Deno.env.get("JWT_SECRET") ?? "";
      const secreto = new TextEncoder().encode(secretStr);
      await jwtVerify(token, secreto);
    } else {
      return sin401("algoritmo no soportado");
    }
    return null;
  } catch (err: any) {
    console.error("RequireUser: JWT verification failed:", err?.message || err);
    return sin401("token invalido");
  }
}

// --- LÓGICA DE PROYECCIÓN DE FLUJO AUTO-CONTENIDA ---
export const SEMANAS = 4;
export const AVG_DIAS_GLOBAL = 30;
const DIA_MS = 86_400_000;

export interface FacturaPendiente {
  folio: number;
  fecha: Date;
  rut_cliente: string;
  razon_social_receptor: string;
  monto: number;
}

export interface Gasto {
  descripcion: string;
  proveedor: string | null;
  monto: number;
  fecha_vencimiento: Date;
  categoria: string | null;
  recurrente?: boolean;
  periodicidad?: string | null;
}

export interface Semana {
  semana: number;
  label: string;
  ingresos: number;
  egresos: number;
  saldo_acumulado: number;
  riesgo: boolean;
}

export interface FlujoResult {
  saldo_inicial: number;
  semanas: Semana[];
  total_ingresos: number;
  total_egresos: number;
  fuera_horizonte: number;
}

const dias = (a: Date, b: Date) => Math.floor((a.getTime() - b.getTime()) / DIA_MS);
const masDias = (d: Date, n: number) => new Date(d.getTime() + n * DIA_MS);
const ddmm = (d: Date) =>
  `${String(d.getUTCDate()).padStart(2, "0")}/${String(d.getUTCMonth() + 1).padStart(2, "0")}`;

function proyectarRecurrente(g: Gasto, hoy: Date, horizonte: Date): Gasto[] {
  const diaMes = g.fecha_vencimiento.getUTCDate();
  const out: Gasto[] = [];
  for (let dm = 0; dm < 3; dm++) {
    const anio = hoy.getUTCFullYear() + Math.floor((hoy.getUTCMonth() + dm) / 12);
    const mes = (hoy.getUTCMonth() + dm) % 12;
    const ultimo = new Date(Date.UTC(anio, mes + 1, 0)).getUTCDate();
    const fecha = new Date(Date.UTC(anio, mes, Math.min(diaMes, ultimo)));
    if (fecha >= hoy && fecha <= horizonte) {
      out.push({ ...g, fecha_vencimiento: fecha });
    }
  }
  return out;
}

export function proyectarFlujo(
  facturas: FacturaPendiente[],
  avgDias: Record<string, number>,
  gastos: Gasto[],
  saldoInicial: number,
  hoy: Date,
  semanas = SEMANAS,
): FlujoResult {
  const horizonte = masDias(hoy, semanas * 7);

  const ingresosSemana: number[] = Array(semanas).fill(0);
  let fueraHorizonte = 0;
  for (const f of facturas) {
    const avg = Math.trunc(avgDias[f.rut_cliente] ?? AVG_DIAS_GLOBAL);
    let proyectada = masDias(f.fecha, avg);
    if (proyectada < hoy) proyectada = hoy;
    if (proyectada <= horizonte) {
      const sem = Math.max(0, Math.min(Math.floor(dias(proyectada, hoy) / 7), semanas - 1));
      ingresosSemana[sem] += f.monto;
    } else {
      fueraHorizonte += f.monto;
    }
  }

  const egresosSemana: number[] = Array(semanas).fill(0);
  const puntuales = gastos.filter((g) => !g.recurrente);
  const recurrentes = gastos
    .filter((g) => g.recurrente && g.periodicidad === "mensual")
    .flatMap((g) => proyectarRecurrente(g, hoy, horizonte));
  for (const g of [...puntuales, ...recurrentes]) {
    if (g.fecha_vencimiento < hoy || g.fecha_vencimiento > horizonte) continue;
    const sem = Math.max(0, Math.min(Math.floor(dias(g.fecha_vencimiento, hoy) / 7), semanas - 1));
    egresosSemana[sem] += g.monto;
  }

  let saldo = saldoInicial;
  let totalIn = 0, totalOut = 0;
  const out: Semana[] = [];
  for (let sem = 0; sem < semanas; sem++) {
    const inicio = masDias(hoy, sem * 7);
    const fin = masDias(inicio, 6);
    saldo += ingresosSemana[sem] - egresosSemana[sem];
    totalIn += ingresosSemana[sem];
    totalOut += egresosSemana[sem];
    out.push({
      semana: sem + 1,
      label: `${ddmm(inicio)}-${ddmm(fin)}`,
      ingresos: ingresosSemana[sem],
      egresos: egresosSemana[sem],
      saldo_acumulado: saldo,
      riesgo: saldo < 0,
    });
  }
  return {
    saldo_inicial: saldoInicial,
    semanas: out,
    total_ingresos: totalIn,
    total_egresos: totalOut,
    fuera_horizonte: fueraHorizonte,
  };
}

// --- HANDLER PRINCIPAL ---
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

  const facturas = await sqlClient`
    SELECT folio, fecha, rut_cliente, razon_social_receptor, monto
    FROM v_flujo_pendientes ORDER BY fecha`;
  const avgs = await sqlClient`SELECT rut_cliente, avg_dias FROM v_dias_pago_cliente`;
  const gastos = await sqlClient`
    SELECT descripcion, proveedor, monto, fecha_vencimiento, categoria,
           recurrente, periodicidad
    FROM cuentas_por_pagar WHERE pagado = FALSE`;
  const [meta] = await sqlClient`
    SELECT valor FROM sync_meta WHERE clave = 'saldo_banco'`;

  const avgDias = Object.fromEntries(
    avgs.map((a) => [a.rut_cliente, Number(a.avg_dias)]),
  );
  
  const resultado = proyectarFlujo(
    facturas.map((f): FacturaPendiente => ({
      folio: Number(f.folio),
      fecha: new Date(f.fecha),
      rut_cliente: f.rut_cliente,
      razon_social_receptor: f.razon_social_receptor,
      monto: Number(f.monto),
    })),
    avgDias,
    gastos.map((g): Gasto => ({
      descripcion: g.descripcion,
      proveedor: g.proveedor,
      monto: Number(g.monto),
      fecha_vencimiento: new Date(g.fecha_vencimiento),
      categoria: g.categoria,
      recurrente: g.recurrente ?? false,
      periodicidad: g.periodicidad,
    })),
    Number(meta?.valor?.saldo ?? 0),
    new Date(),
  );

  return new Response(JSON.stringify(resultado), {
    status: 200,
    headers: {
      ...corsHeaders,
      "Content-Type": "application/json",
    },
  });
}
