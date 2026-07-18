// nube/functions/_shared/flujo.ts
// Puerto fiel de app/negocio/flujo.py::proyectar_flujo. Si cambia la logica
// alla, replicar aqui (el test de paridad de Task 8 detecta divergencias).

export const SEMANAS = 4;
export const AVG_DIAS_GLOBAL = 30;
const DIA_MS = 86_400_000;

export interface FacturaPendiente {
  folio: number; fecha: Date; rut_cliente: string;
  razon_social_receptor: string; monto: number;
}
export interface Gasto {
  descripcion: string; proveedor: string | null; monto: number;
  fecha_vencimiento: Date; categoria: string | null;
  recurrente?: boolean; periodicidad?: string | null;
}
export interface Semana {
  semana: number; label: string; ingresos: number; egresos: number;
  saldo_acumulado: number; riesgo: boolean;
}
export interface FlujoResult {
  saldo_inicial: number; semanas: Semana[];
  total_ingresos: number; total_egresos: number; fuera_horizonte: number;
}

const dias = (a: Date, b: Date) => Math.floor((a.getTime() - b.getTime()) / DIA_MS);
const masDias = (d: Date, n: number) => new Date(d.getTime() + n * DIA_MS);
const ddmm = (d: Date) =>
  `${String(d.getUTCDate()).padStart(2, "0")}/${String(d.getUTCMonth() + 1).padStart(2, "0")}`;

/** Ocurrencias de un gasto mensual en [hoy, horizonte], mismo dia del mes
 * (recortado al ultimo dia si el mes es mas corto), 3 meses hacia adelante. */
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
      semana: sem + 1, label: `${ddmm(inicio)}-${ddmm(fin)}`,
      ingresos: ingresosSemana[sem], egresos: egresosSemana[sem],
      saldo_acumulado: saldo, riesgo: saldo < 0,
    });
  }
  return {
    saldo_inicial: saldoInicial, semanas: out,
    total_ingresos: totalIn, total_egresos: totalOut,
    fuera_horizonte: fueraHorizonte,
  };
}
