// functions/_shared/chat_tools.ts
// Las 10 herramientas de SOLO LECTURA del chat. Queries fijas sobre las views
// canonicas (las reglas de negocio viven en el SQL de las views, no aqui).
// El texto devuelto es lo que el modelo cita: pesos chilenos con puntos.
import {
  proyectarFlujo,
  type FacturaPendiente,
  type Gasto,
} from "./flujo.ts";

export type SqlCliente = (
  strings: TemplateStringsArray,
  ...vals: unknown[]
) => Promise<Record<string, unknown>[]>;

export function formatearPesos(n: number | string | null | undefined): string {
  const v = Math.round(Number(n ?? 0));
  const signo = v < 0 ? "-" : "";
  const digitos = String(Math.abs(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${signo}$${digitos}`;
}

const num = (x: unknown) => Number(x ?? 0);

export const TOOLS = [
  { name: "deuda_total",
    description: "Deuda total pendiente de cobro, con desglose por antiguedad (dias desde la emision de la factura).",
    input_schema: { type: "object", properties: {} } },
  { name: "deuda_cliente",
    description: "Deuda pendiente de un cliente especifico, por nombre (parcial) o RUT.",
    input_schema: { type: "object", properties: {
      nombre: { type: "string", description: "Nombre parcial o RUT del cliente" } },
      required: ["nombre"] } },
  { name: "ranking_deudores",
    description: "Top N clientes ordenados por deuda pendiente.",
    input_schema: { type: "object", properties: {
      limite: { type: "integer", description: "Cuantos clientes mostrar (default 5)" } } } },
  { name: "facturas_vencidas",
    description: "Facturas pendientes de pago con mas de N dias desde su emision (default 30).",
    input_schema: { type: "object", properties: {
      dias: { type: "integer", description: "Umbral de dias (default 30)" } } } },
  { name: "ventas_total",
    description: "Total vendido (neto de notas de credito). Opcional: rango desde/hasta en formato YYYY-MM-DD (ambos o ninguno).",
    input_schema: { type: "object", properties: {
      desde: { type: "string" }, hasta: { type: "string" } } } },
  { name: "ranking_clientes",
    description: "Top N clientes ordenados por ventas historicas totales.",
    input_schema: { type: "object", properties: {
      limite: { type: "integer", description: "Cuantos clientes mostrar (default 10)" } } } },
  { name: "ventas_cliente",
    description: "Ventas historicas de un cliente por nombre, con sus ultimas facturas.",
    input_schema: { type: "object", properties: {
      nombre: { type: "string" } }, required: ["nombre"] } },
  { name: "ventas_producto",
    description: "Lineas de venta que coinciden con un nombre de producto (ya excluye Logistica y envases PET).",
    input_schema: { type: "object", properties: {
      nombre: { type: "string" } }, required: ["nombre"] } },
  { name: "flujo_caja",
    description: "Proyeccion de caja a 4 semanas: cobros esperados por cliente menos gastos programados, partiendo del saldo bancario del ultimo sync.",
    input_schema: { type: "object", properties: {} } },
  { name: "listar_gastos",
    description: "Gastos pendientes de pago (cuentas por pagar) con monto y vencimiento. Opcional: filtro de texto sobre la descripcion.",
    input_schema: { type: "object", properties: {
      filtro: { type: "string" } } } },
  { name: "crear_tarea",
    description: "Crea una nueva tarea o compromiso en la agenda.",
    input_schema: {
      type: "object",
      properties: {
        descripcion: { type: "string", description: "Descripción detallada del compromiso o tarea" },
        fecha: { type: "string", description: "Fecha del compromiso en formato YYYY-MM-DD" }
      },
      required: ["descripcion", "fecha"]
    } },
  { name: "listar_tareas",
    description: "Lista las tareas y compromisos de la agenda. Opcionalmente filtra por fecha (YYYY-MM-DD) o estado (completada true/false).",
    input_schema: {
      type: "object",
      properties: {
        fecha: { type: "string", description: "Filtrar por fecha en formato YYYY-MM-DD (opcional)" },
        completada: { type: "boolean", description: "Filtrar por completadas (true) o pendientes (false) (opcional)" }
      }
    } },
  { name: "marcar_tarea_completada",
    description: "Marca una tarea específica de la agenda como completada usando su ID.",
    input_schema: {
      type: "object",
      properties: {
        id: { type: "integer", description: "ID único de la tarea a marcar como completada" }
      },
      required: ["id"]
    } },
];

interface FilaPendiente { total: unknown; dias_desde_emision: unknown }

export function resumenDeuda(filas: FilaPendiente[]): string {
  if (!filas.length) return "No hay deuda pendiente de cobro.";
  const buckets = { d0_30: 0, d31_60: 0, d61_90: 0, d90_mas: 0 };
  let total = 0;
  for (const f of filas) {
    const t = num(f.total);
    const d = num(f.dias_desde_emision);
    total += t;
    if (d <= 30) buckets.d0_30 += t;
    else if (d <= 60) buckets.d31_60 += t;
    else if (d <= 90) buckets.d61_90 += t;
    else buckets.d90_mas += t;
  }
  return `Deuda total pendiente: ${formatearPesos(total)} en ${filas.length} facturas. ` +
    `Por antiguedad: 0-30d ${formatearPesos(buckets.d0_30)}, ` +
    `31-60d ${formatearPesos(buckets.d31_60)}, ` +
    `61-90d ${formatearPesos(buckets.d61_90)}, ` +
    `+90d ${formatearPesos(buckets.d90_mas)}.`;
}

export async function ejecutarTool(
  sql: SqlCliente,
  nombre: string,
  input: Record<string, unknown>,
  hoy: Date,
): Promise<string> {
  switch (nombre) {
    case "deuda_total": {
      const filas = await sql`SELECT total, dias_desde_emision FROM v_pendientes`;
      return resumenDeuda(filas as unknown as FilaPendiente[]);
    }
    case "deuda_cliente": {
      const nombreCliente = String(input.nombre ?? "");
      const q = `%${nombreCliente}%`;
      const filas = await sql`
        SELECT folio, fecha, razon_social, total, dias_desde_emision
        FROM v_pendientes
        WHERE razon_social ILIKE ${q} OR rut_cliente = ${nombreCliente}
        ORDER BY fecha`;
      if (!filas.length) return `${nombreCliente}: sin deuda pendiente.`;
      const total = filas.reduce((s, f) => s + num(f.total), 0);
      const lineas = filas.map((f) =>
        `- Folio ${f.folio} (${String(f.fecha).slice(0, 10)}): ${formatearPesos(num(f.total))}, ${num(f.dias_desde_emision)}d`);
      return `${filas[0].razon_social}: ${formatearPesos(total)} en ${filas.length} facturas.\n${lineas.join("\n")}`;
    }
    case "ranking_deudores": {
      const limite = num(input.limite) || 5;
      const filas = await sql`
        SELECT razon_social, SUM(total) AS deuda, COUNT(*) AS n
        FROM v_pendientes GROUP BY razon_social
        ORDER BY deuda DESC LIMIT ${limite}`;
      if (!filas.length) return "No hay deuda pendiente.";
      return filas.map((f, i) =>
        `${i + 1}. ${f.razon_social}: ${formatearPesos(num(f.deuda))} (${num(f.n)} facturas)`).join("\n");
    }
    case "facturas_vencidas": {
      const dias = num(input.dias) || 30;
      const filas = await sql`
        SELECT folio, razon_social, total, dias_desde_emision
        FROM v_pendientes WHERE dias_desde_emision > ${dias}
        ORDER BY dias_desde_emision DESC`;
      if (!filas.length) return `Ninguna factura pendiente con mas de ${dias} dias.`;
      return `${filas.length} facturas con mas de ${dias} dias:\n` + filas.map((f) =>
        `- Folio ${f.folio} ${f.razon_social}: ${formatearPesos(num(f.total))}, ${num(f.dias_desde_emision)}d`).join("\n");
    }
    case "ventas_total": {
      const desde = input.desde ? String(input.desde) : null;
      const hasta = input.hasta ? String(input.hasta) : null;
      const filas = (desde && hasta)
        ? await sql`SELECT COUNT(*) AS n, COALESCE(SUM(total_real), 0) AS total
                    FROM v_ventas_reales WHERE fecha BETWEEN ${desde} AND ${hasta}`
        : await sql`SELECT COUNT(*) AS n, COALESCE(SUM(total_real), 0) AS total
                    FROM v_ventas_reales`;
      const f = filas[0];
      const periodo = (desde && hasta) ? ` entre ${desde} y ${hasta}` : " historicas";
      return `Ventas${periodo}: ${formatearPesos(num(f.total))} en ${num(f.n)} facturas.`;
    }
    case "ranking_clientes": {
      const limite = num(input.limite) || 10;
      const filas = await sql`
        SELECT rut_cliente, MAX(razon_social_receptor) AS cliente,
               SUM(total_real) AS total
        FROM v_ventas_reales GROUP BY rut_cliente
        ORDER BY total DESC LIMIT ${limite}`;
      if (!filas.length) return "Sin ventas registradas.";
      return filas.map((f, i) =>
        `${i + 1}. ${f.cliente}: ${formatearPesos(num(f.total))}`).join("\n");
    }
    case "ventas_cliente": {
      const q = `%${String(input.nombre ?? "")}%`;
      const filas = await sql`
        SELECT folio, fecha, razon_social_receptor, total_real
        FROM v_ventas_reales WHERE razon_social_receptor ILIKE ${q}
        ORDER BY fecha DESC`;
      if (!filas.length) return `Sin ventas que coincidan con '${input.nombre}'.`;
      const total = filas.reduce((s, f) => s + num(f.total_real), 0);
      const ultimas = filas.slice(0, 5).map((f) =>
        `- Folio ${f.folio} (${String(f.fecha).slice(0, 10)}): ${formatearPesos(num(f.total_real))}`);
      return `${filas[0].razon_social_receptor}: ${formatearPesos(total)} en ${filas.length} facturas.\n` +
        `Ultimas:\n${ultimas.join("\n")}`;
    }
    case "ventas_producto": {
      const q = `%${String(input.nombre ?? "")}%`;
      const filas = await sql`
        SELECT nombre_producto, cantidad, fecha
        FROM v_ventas_producto WHERE nombre_producto ILIKE ${q}
        ORDER BY fecha DESC`;
      if (!filas.length) return `Sin ventas que coincidan con '${input.nombre}'.`;
      const unidades = filas.reduce((s, f) => s + num(f.cantidad), 0);
      return `'${input.nombre}': ${filas.length} lineas de venta, ${unidades} unidades ` +
        `(ultima el ${String(filas[0].fecha).slice(0, 10)}).`;
    }
    case "flujo_caja": {
      // Mismas queries que functions/flujo.ts (paridad con el endpoint /flujo).
      const facturas = await sql`
        SELECT folio, fecha, rut_cliente, razon_social_receptor, monto
        FROM v_flujo_pendientes ORDER BY fecha`;
      const avgs = await sql`SELECT rut_cliente, avg_dias FROM v_dias_pago_cliente`;
      const gastos = await sql`
        SELECT descripcion, proveedor, monto, fecha_vencimiento, categoria,
               recurrente, periodicidad
        FROM cuentas_por_pagar WHERE pagado = FALSE`;
      const meta = await sql`SELECT valor FROM sync_meta WHERE clave = 'saldo_banco'`;
      const avgDias = Object.fromEntries(
        avgs.map((a) => [String(a.rut_cliente), Number(a.avg_dias)]));
      const saldoInicial = num((meta[0]?.valor as { saldo?: unknown })?.saldo);
      const r = proyectarFlujo(
        facturas.map((f): FacturaPendiente => ({
          folio: num(f.folio), fecha: new Date(String(f.fecha)),
          rut_cliente: String(f.rut_cliente),
          razon_social_receptor: String(f.razon_social_receptor),
          monto: num(f.monto),
        })),
        avgDias,
        gastos.map((g): Gasto => ({
          descripcion: String(g.descripcion),
          proveedor: g.proveedor === null ? null : String(g.proveedor),
          monto: num(g.monto),
          fecha_vencimiento: new Date(String(g.fecha_vencimiento)),
          categoria: g.categoria === null ? null : String(g.categoria),
          recurrente: Boolean(g.recurrente),
          periodicidad: g.periodicidad === null ? null : String(g.periodicidad),
        })),
        saldoInicial, hoy,
      );
      const lineas = r.semanas.map((s) =>
        `- Semana ${s.semana} (${s.label}): ingresos ${formatearPesos(s.ingresos)}, ` +
        `egresos ${formatearPesos(s.egresos)}, saldo ${formatearPesos(s.saldo_acumulado)}` +
        (s.riesgo ? " [RIESGO]" : ""));
      return `Flujo de caja 4 semanas (saldo inicial ${formatearPesos(r.saldo_inicial)}):\n` +
        lineas.join("\n") +
        `\nTotales: ingresos ${formatearPesos(r.total_ingresos)}, egresos ${formatearPesos(r.total_egresos)}. ` +
        `Fuera del horizonte: ${formatearPesos(r.fuera_horizonte)}.`;
    }
    case "listar_gastos": {
      const filtro = input.filtro ? String(input.filtro) : null;
      const filas = filtro
        ? await sql`SELECT id, descripcion, proveedor, monto, fecha_vencimiento
                    FROM cuentas_por_pagar
                    WHERE pagado = FALSE AND descripcion ILIKE ${"%" + filtro + "%"}
                    ORDER BY fecha_vencimiento`
        : await sql`SELECT id, descripcion, proveedor, monto, fecha_vencimiento
                    FROM cuentas_por_pagar WHERE pagado = FALSE
                    ORDER BY fecha_vencimiento`;
      if (!filas.length) {
        return filtro
          ? `No hay gastos pendientes que coincidan con '${filtro}'.`
          : "No hay gastos pendientes.";
      }
      return filas.map((g) =>
        `- ${g.descripcion}: ${formatearPesos(num(g.monto))}, vence ${String(g.fecha_vencimiento).slice(0, 10)}` +
        (g.proveedor ? ` (${g.proveedor})` : "")).join("\n");
    }
    case "crear_tarea": {
      const desc = String(input.descripcion ?? "").trim();
      const fecha = String(input.fecha ?? "").trim();
      if (!desc || !fecha) return "Error: descripción y fecha son requeridas.";
      const [nueva] = await sql`
        INSERT INTO chat_tareas (descripcion, fecha)
        VALUES (${desc}, ${fecha})
        RETURNING id, descripcion, fecha`;
      return `Tarea creada con éxito. ID: ${nueva.id}. Compromiso: "${nueva.descripcion}" para el ${String(nueva.fecha).slice(0, 10)}.`;
    }
    case "listar_tareas": {
      const fecha = input.fecha ? String(input.fecha).trim() : null;
      const completada = input.completada !== undefined ? Boolean(input.completada) : null;
      
      let filas;
      if (fecha !== null && completada !== null) {
        filas = await sql`
          SELECT id, descripcion, fecha, completada
          FROM chat_tareas
          WHERE fecha = ${fecha} AND completada = ${completada}
          ORDER BY fecha, id`;
      } else if (fecha !== null) {
        filas = await sql`
          SELECT id, descripcion, fecha, completada
          FROM chat_tareas
          WHERE fecha = ${fecha}
          ORDER BY fecha, id`;
      } else if (completada !== null) {
        filas = await sql`
          SELECT id, descripcion, fecha, completada
          FROM chat_tareas
          WHERE completada = ${completada}
          ORDER BY fecha, id`;
      } else {
        filas = await sql`
          SELECT id, descripcion, fecha, completada
          FROM chat_tareas
          ORDER BY fecha, id`;
      }

      if (!filas.length) return "No hay tareas registradas que coincidan con los filtros.";
      const lineas = filas.map((t) =>
        `- [ID: ${t.id}] ${String(t.fecha).slice(0, 10)}: "${t.descripcion}" [${t.completada ? "COMPLETADA" : "PENDIENTE"}]`
      );
      return `Tareas agendadas:\n${lineas.join("\n")}`;
    }
    case "marcar_tarea_completada": {
      const id = Number(input.id);
      if (isNaN(id)) return "Error: ID de tarea no válido.";
      const [res] = await sql`
        UPDATE chat_tareas
        SET completada = TRUE, actualizado = now()
        WHERE id = ${id}
        RETURNING id, descripcion, completada`;
      if (!res) return `No se encontró ninguna tarea con el ID ${id}.`;
      return `Tarea ID ${res.id} ("${res.descripcion}") marcada como COMPLETADA con éxito.`;
    }
    default:
      return `Herramienta desconocida: ${nombre}.`;
  }
}
