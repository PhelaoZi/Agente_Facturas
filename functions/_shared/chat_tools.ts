// functions/_shared/chat_tools.ts
// Las 10 herramientas de SOLO LECTURA del chat. Queries fijas sobre las views
// canonicas (las reglas de negocio viven en el SQL de las views, no aqui).
// El texto devuelto es lo que el modelo cita: pesos chilenos con puntos.
import {
  proyectarFlujo,
  type FacturaPendiente,
  type Gasto,
} from "./flujo.ts";

// Subconjunto del cliente postgres.js que usan las tools: tagged template
// para las queries fijas y begin() para la transaccion READ ONLY que blinda
// consulta_sql (begin es opcional para que los fakes de test sigan simples).
export interface SqlTransaccion {
  (strings: TemplateStringsArray, ...vals: unknown[]): Promise<Record<string, unknown>[]>;
  unsafe(consulta: string): Promise<Record<string, unknown>[]>;
}

export type SqlCliente = ((
  strings: TemplateStringsArray,
  ...vals: unknown[]
) => Promise<Record<string, unknown>[]>) & {
  begin?: (
    opciones: string,
    fn: (t: SqlTransaccion) => Promise<unknown>,
  ) => Promise<unknown>;
};

export function formatearPesos(n: number | string | null | undefined): string {
  const v = Math.round(Number(n ?? 0));
  const signo = v < 0 ? "-" : "";
  const digitos = String(Math.abs(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${signo}$${digitos}`;
}

const num = (x: unknown) => Number(x ?? 0);

// Precios de venta netos confirmados por barril 30L (espejo de
// app/negocio/costos.py: si cambian alla, cambiarlos aqui tambien).
// La clave es un patron: se busca como subcadena en el nombre normalizado
// del SKU, asi "Stout Café/Cacao" (normaliza a "stout cafe/cacao") casa con
// "stout cafe". El PRIMER patron que calza gana (orden = prioridad).
const PRECIOS_VENTA_NETO: Array<[patron: string, precio: number]> = [
  ["cream ale", 55370],
  ["scotch ale", 55370],
  ["stout cafe", 75000],
  ["stout cacao", 75000],
  ["stout", 75000],           // "Stout Café/Cacao" y variantes
  ["paint it black", 98000],
];

// Espejo de _norm de app/negocio/costos.py: minusculas, sin tildes,
// espacios simples (para casar nombres de cerveza con los precios).
export function normalizar(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().split(/\s+/).filter(Boolean).join(" ");
}

// Precio de venta confirmado para un SKU: solo barriles 30L (acero o PET
// comparten el mismo precio de venta; lo que cambia es el costo). null si
// no es barril o la cerveza no tiene precio confirmado.
export function precioVentaSku(nombreCerveza: string, formato: string): number | null {
  if (!normalizar(formato).includes("barril")) return null;
  const nombre = normalizar(nombreCerveza);
  for (const [patron, precio] of PRECIOS_VENTA_NETO) {
    if (nombre.includes(patron)) return precio;
  }
  return null;
}

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
    description: "UNIDADES vendidas y fechas de un producto por nombre (ya excluye Logistica y envases PET). NO sirve para pesos: para dinero usa ingreso_producto.",
    input_schema: { type: "object", properties: {
      nombre: { type: "string" } }, required: ["nombre"] } },
  { name: "ingreso_producto",
    description: "Dinero por cerveza: cuanto ingreso neto dejo cada una. UNICA fuente de plata por producto (suma la linea del producto MAS la logistica que le corresponde, que es cerca de la mitad del precio del barril). Sin argumentos da el ranking completo; con 'cerveza' da esa sola y sus principales clientes. Rango opcional desde/hasta en YYYY-MM-DD.",
    input_schema: { type: "object", properties: {
      cerveza: { type: "string", description: "Nombre parcial de una cerveza; omitir para el ranking" },
      desde: { type: "string" }, hasta: { type: "string" },
      limite: { type: "integer", description: "Cuantas cervezas en el ranking (default 10)" } } } },
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
  { name: "ultimas_facturas",
    description: "Las N facturas mas recientes registradas, con folio, fecha, cliente, total y estado de pago. Default 5, maximo 20.",
    input_schema: { type: "object", properties: {
      limite: { type: "integer", description: "Cuantas facturas mostrar (default 5, max 20)" } } } },
  { name: "detalle_factura",
    description: "Detalle completo de una factura por su folio: cabecera, estado de pago, nota de credito aplicada si la hay, y todas sus lineas (producto, Logistica y envase PET etiquetados).",
    input_schema: { type: "object", properties: {
      folio: { type: "integer", description: "Folio de la factura" } },
      required: ["folio"] } },
  { name: "costos_sku",
    description: "Costo unitario de produccion por SKU (cerveza x formato): liquido, envasado y total. Filtros opcionales por nombre de cerveza o codigo de SKU.",
    input_schema: { type: "object", properties: {
      receta: { type: "string", description: "Nombre parcial de la cerveza" },
      sku: { type: "string", description: "Codigo exacto del SKU" } } } },
  { name: "margenes",
    description: "Margen por SKU de barril 30L: precio de venta neto confirmado menos costo total. Formatos sin precio confirmado (botellas) quedan sin margen. Filtro opcional por cerveza.",
    input_schema: { type: "object", properties: {
      receta: { type: "string", description: "Nombre parcial de la cerveza" } } } },
  { name: "consulta_sql",
    description: "ULTIMO RECURSO: una consulta SQL de SOLO LECTURA (una sola sentencia SELECT o WITH) sobre la replica, para preguntas que ninguna herramienta fija cubre. Sigue las REGLAS SQL del prompt.",
    input_schema: { type: "object", properties: {
      consulta: { type: "string", description: "Una sentencia SELECT o WITH" } },
      required: ["consulta"] } },
];

// ── Dinero por producto ──────────────────────────────────────────────────────
// Espejo de app/negocio/ingreso_producto.py. La regla que ordena esto: una
// cifra de plata por producto NUNCA sale sola — va siempre con su periodo y su
// cobertura. Un "$33 millones" no dice si son de un ano o de tres, ni cuanto de
// eso se estimo, y asi fue como se llego a este problema.
const CALIDAD_ESTIMADA = "estimada";

/** Frase de periodo. La arma el codigo con los filtros que de verdad llegaron,
 *  nunca el modelo, que puede olvidarlos. */
export function alcanceFechas(desde: string | null, hasta: string | null): string {
  if (desde && hasta) return `del ${desde} al ${hasta}`;
  if (desde) return `desde el ${desde}`;
  if (hasta) return `hasta el ${hasta}`;
  return "todo el historico, sin filtro de fecha";
}

/** Que parte del monto se pudo verificar contra el documento. */
export function coberturaAtribucion(determinista: number, estimado: number): string {
  const total = determinista + estimado;
  if (!total) return "sin ventas en el periodo consultado";
  const pct = Math.round(1000 * estimado / total) / 10;
  if (!pct) return "100% deterministico (una sola cerveza por factura)";
  return `${(100 - pct).toFixed(1)}% deterministico y ${pct.toFixed(1)}% estimado ` +
    "(facturas con varias cervezas, logistica repartida a prorrata)";
}

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
      // Agrupa por el nombre CANONICO: el productor escribe el nombre a mano y
      // hay 84 formas de escribir 27 cervezas. Por el nombre crudo, "Barril 30L
      // APA" y "Barril 30L  APA" (doble espacio) salen como dos productos.
      const q = `%${String(input.nombre ?? "")}%`;
      const filas = await sql`
        SELECT cerveza, formato, SUM(cantidad) AS unidades, MAX(fecha) AS fecha
        FROM v_lineas_producto
        WHERE clase = 'cerveza' AND tipo_documento != 61
          AND (cerveza ILIKE ${q} OR nombre_producto ILIKE ${q})
        GROUP BY cerveza, formato
        ORDER BY unidades DESC`;
      if (!filas.length) return `Sin ventas que coincidan con '${input.nombre}'.`;
      const total = filas.reduce((s, f) => s + num(f.unidades), 0);
      const detalle = filas.map((f) =>
        `- ${f.cerveza} · ${f.formato ?? "s/formato"}: ${num(f.unidades)} unidades ` +
        `(ultima el ${String(f.fecha).slice(0, 10)})`);
      return `'${input.nombre}': ${total} unidades en ${filas.length} formato(s).\n` +
        detalle.join("\n");
    }
    case "ingreso_producto": {
      const cerveza = input.cerveza ? String(input.cerveza) : null;
      // Los filtros de fecha van como parametros que pueden ser NULL, en vez de
      // ramificar la query en cuatro variantes: el rango es opcional en los dos
      // extremos y las ramas se multiplican por cada query.
      const desde = input.desde ? String(input.desde) : null;
      const hasta = input.hasta ? String(input.hasta) : null;
      const periodo = alcanceFechas(desde, hasta);

      if (!cerveza) {
        const limite = num(input.limite) || 10;
        const filas = await sql`
          SELECT cerveza,
                 SUM(ingreso_neto_atribuido) AS ingreso,
                 SUM(unidades)               AS unidades,
                 SUM(CASE WHEN calidad <> ${CALIDAD_ESTIMADA}
                          THEN ABS(ingreso_neto_atribuido) ELSE 0 END) AS determinista,
                 SUM(CASE WHEN calidad =  ${CALIDAD_ESTIMADA}
                          THEN ABS(ingreso_neto_atribuido) ELSE 0 END) AS estimado
          FROM v_ingreso_producto
          WHERE (${desde}::date IS NULL OR fecha_evento >= ${desde}::date)
            AND (${hasta}::date IS NULL OR fecha_evento <= ${hasta}::date)
          GROUP BY cerveza
          ORDER BY ingreso DESC
          LIMIT ${limite}`;
        if (!filas.length) return `Sin ventas atribuidas (${periodo}).`;
        let det = 0, est = 0;
        const lineas = filas.map((f, i) => {
          det += num(f.determinista);
          est += num(f.estimado);
          return `${i + 1}. ${f.cerveza}: ${formatearPesos(num(f.ingreso))} ` +
            `(${num(f.unidades)} unidades)`;
        });
        return `Ingreso por cerveza (${periodo}):\n${lineas.join("\n")}\n` +
          `Cobertura: ${coberturaAtribucion(det, est)}.`;
      }

      const q = `%${cerveza}%`;
      const totales = await sql`
        SELECT SUM(ingreso_neto_atribuido) AS ingreso,
               SUM(unidades)               AS unidades,
               SUM(CASE WHEN calidad <> ${CALIDAD_ESTIMADA}
                        THEN ABS(ingreso_neto_atribuido) ELSE 0 END) AS determinista,
               SUM(CASE WHEN calidad =  ${CALIDAD_ESTIMADA}
                        THEN ABS(ingreso_neto_atribuido) ELSE 0 END) AS estimado,
               COUNT(DISTINCT folio)       AS n_documentos
        FROM v_ingreso_producto
        WHERE cerveza ILIKE ${q}
          AND (${desde}::date IS NULL OR fecha_evento >= ${desde}::date)
          AND (${hasta}::date IS NULL OR fecha_evento <= ${hasta}::date)`;
      const t = totales[0];
      // Sin filas o con la suma en NULL es lo mismo: no hubo ventas. Decirlo,
      // nunca devolver $0 (se lee como "vendio cero", no como "no hay dato").
      if (!t || t.ingreso == null) return `Sin ventas de '${cerveza}' (${periodo}).`;

      const clientes = await sql`
        SELECT razon_social,
               SUM(ingreso_neto_atribuido) AS ingreso,
               SUM(unidades)               AS unidades
        FROM v_ingreso_producto
        WHERE cerveza ILIKE ${q}
          AND (${desde}::date IS NULL OR fecha_evento >= ${desde}::date)
          AND (${hasta}::date IS NULL OR fecha_evento <= ${hasta}::date)
        GROUP BY razon_social
        ORDER BY ingreso DESC
        LIMIT 5`;
      const top = clientes.map((c) =>
        `- ${c.razon_social}: ${formatearPesos(num(c.ingreso))} (${num(c.unidades)} unidades)`);
      return `${cerveza} (${periodo}): ${formatearPesos(num(t.ingreso))} de ingreso, ` +
        `${num(t.unidades)} unidades en ${num(t.n_documentos)} documentos.\n` +
        (top.length ? `Principales clientes:\n${top.join("\n")}\n` : "") +
        `Cobertura: ${coberturaAtribucion(num(t.determinista), num(t.estimado))}.`;
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
    case "ultimas_facturas": {
      const limite = Math.min(num(input.limite) || 5, 20);
      const filas = await sql`
        SELECT folio, fecha, razon_social_receptor, total_real, fecha_pago
        FROM v_ventas_reales
        ORDER BY fecha DESC, folio DESC LIMIT ${limite}`;
      if (!filas.length) return "No hay facturas registradas.";
      return `Ultimas ${filas.length} facturas:\n` + filas.map((f) =>
        `- Folio ${f.folio} (${String(f.fecha).slice(0, 10)}) ${f.razon_social_receptor}: ` +
        `${formatearPesos(num(f.total_real))} — ` +
        (f.fecha_pago ? `pagada el ${String(f.fecha_pago).slice(0, 10)}` : "PENDIENTE")).join("\n");
    }
    case "detalle_factura": {
      const folio = num(input.folio);
      if (!folio) return "Error: indica el folio de la factura.";
      const cabeceras = await sql`
        SELECT folio, tipo_documento, fecha, rut_cliente, razon_social_receptor,
               neto_real, total_real, total_original, tiene_nc, fecha_pago
        FROM v_factura_cabecera WHERE folio = ${folio}
        ORDER BY tipo_documento`;
      if (!cabeceras.length) return `No existe ningun documento con folio ${folio}.`;
      // Si el folio existe como factura (33) y como NC (61), preferir la factura.
      const c = cabeceras.find((x) => num(x.tipo_documento) === 33) ?? cabeceras[0];
      const esNC = num(c.tipo_documento) === 61;
      const lineas = await sql`
        SELECT nombre_producto, cantidad, precio_unitario, subtotal, tipo_linea
        FROM v_lineas_factura
        WHERE folio = ${folio} AND tipo_documento = ${c.tipo_documento}`;
      const etiqueta: Record<string, string> = {
        logistica: " [Logistica: parte del precio de la cerveza]",
        envase_pet: " [Envase PET: costo del envase traspasado, no es cerveza]",
        producto: "",
      };
      const det = lineas.map((l) =>
        `- ${l.nombre_producto}: ${num(l.cantidad)} x ${formatearPesos(num(l.precio_unitario))} = ` +
        `${formatearPesos(num(l.subtotal))}${etiqueta[String(l.tipo_linea)] ?? ""}`);
      const estado = c.fecha_pago
        ? `pagada el ${String(c.fecha_pago).slice(0, 10)}`
        : "PENDIENTE de pago";
      const nc = c.tiene_nc
        ? `\nOJO: tiene nota de credito aplicada (total original ` +
          `${formatearPesos(num(c.total_original))} -> ${formatearPesos(num(c.total_real))}).`
        : "";
      return `${esNC ? "NOTA DE CREDITO (tipo 61)" : "Factura"} folio ${c.folio} — ` +
        `${c.razon_social_receptor} (${c.rut_cliente})\n` +
        `Fecha: ${String(c.fecha).slice(0, 10)} · Neto ${formatearPesos(num(c.neto_real))} · ` +
        `Total ${formatearPesos(num(c.total_real))} · ${estado}${nc}\n` +
        (det.length ? `Lineas:\n${det.join("\n")}` : "Sin lineas de detalle registradas.");
    }
    case "costos_sku": {
      const receta = input.receta ? `%${String(input.receta)}%` : null;
      const codigo = input.sku ? String(input.sku) : null;
      let filas;
      if (codigo && receta) {
        filas = await sql`
          SELECT codigo, nombre_cerveza, formato, costo_liquido_unitario,
                 costo_envasado_unitario, costo_total_unitario
          FROM costo_sku WHERE codigo = ${codigo} AND nombre_cerveza ILIKE ${receta}
          ORDER BY nombre_cerveza, formato, codigo`;
      } else if (codigo) {
        filas = await sql`
          SELECT codigo, nombre_cerveza, formato, costo_liquido_unitario,
                 costo_envasado_unitario, costo_total_unitario
          FROM costo_sku WHERE codigo = ${codigo}
          ORDER BY nombre_cerveza, formato, codigo`;
      } else if (receta) {
        filas = await sql`
          SELECT codigo, nombre_cerveza, formato, costo_liquido_unitario,
                 costo_envasado_unitario, costo_total_unitario
          FROM costo_sku WHERE nombre_cerveza ILIKE ${receta}
          ORDER BY nombre_cerveza, formato, codigo`;
      } else {
        filas = await sql`
          SELECT codigo, nombre_cerveza, formato, costo_liquido_unitario,
                 costo_envasado_unitario, costo_total_unitario
          FROM costo_sku
          ORDER BY nombre_cerveza, formato, codigo`;
      }
      if (!filas.length) return "No hay SKUs de costos que coincidan (revisa que la receta este cargada).";
      return filas.map((r) =>
        `- ${r.nombre_cerveza} · ${r.formato} (${r.codigo}): liquido ` +
        `${formatearPesos(num(r.costo_liquido_unitario))} + envasado ` +
        `${formatearPesos(num(r.costo_envasado_unitario))} = ` +
        `${formatearPesos(num(r.costo_total_unitario))} por unidad`).join("\n");
    }
    case "margenes": {
      const receta = input.receta ? `%${String(input.receta)}%` : null;
      const filas = receta
        ? await sql`
            SELECT codigo, nombre_cerveza, formato, costo_total_unitario
            FROM costo_sku WHERE nombre_cerveza ILIKE ${receta}
            ORDER BY nombre_cerveza, formato, codigo`
        : await sql`
            SELECT codigo, nombre_cerveza, formato, costo_total_unitario
            FROM costo_sku
            ORDER BY nombre_cerveza, formato, codigo`;
      if (!filas.length) return "No hay SKUs de costos que coincidan.";
      return filas.map((r) => {
        const precio = precioVentaSku(String(r.nombre_cerveza), String(r.formato));
        const costo = r.costo_total_unitario == null ? null : num(r.costo_total_unitario);
        if (precio === null || costo === null) {
          return `- ${r.nombre_cerveza} · ${r.formato}: costo ` +
            `${costo === null ? "sin datos" : formatearPesos(costo)}, sin precio de venta confirmado`;
        }
        const margen = precio - costo;
        const pct = Math.round((1000 * margen) / precio) / 10;
        return `- ${r.nombre_cerveza} · ${r.formato}: precio ${formatearPesos(precio)} ` +
          `- costo ${formatearPesos(costo)} = margen ${formatearPesos(margen)} (${pct}%)`;
      }).join("\n");
    }
    case "consulta_sql": {
      const cruda = String(input.consulta ?? "").trim().replace(/;\s*$/, "");
      if (!cruda) return "Error: consulta vacia.";
      if (!/^(select|with)\b/i.test(cruda)) {
        return "Error: solo se aceptan consultas SELECT o WITH (solo lectura).";
      }
      if (cruda.includes(";")) {
        return "Error: una sola sentencia por consulta (sin ';').";
      }
      if (!sql.begin) return "Error: este entorno no soporta consulta_sql.";
      const MAX_FILAS = 100;
      const MAX_CHARS = 4000;
      try {
        // Transaccion READ ONLY: la BD misma rechaza cualquier escritura
        // (incluidas CTEs con INSERT/UPDATE/DELETE), pase lo que pase con
        // la validacion de texto. Timeout local a la transaccion.
        const filas = (await sql.begin("read only", async (t) => {
          await t.unsafe("SET LOCAL statement_timeout = 8000");
          return await t.unsafe(cruda);
        })) as Record<string, unknown>[];
        if (!filas.length) return "La consulta no devolvio filas.";
        const visibles = filas.slice(0, MAX_FILAS);
        const cols = Object.keys(visibles[0]);
        let out = `${filas.length} fila(s). Columnas: ${cols.join(", ")}\n` +
          visibles.map((f) =>
            cols.map((c) => `${c}=${f[c] ?? "NULL"}`).join(" | ")).join("\n");
        if (filas.length > MAX_FILAS) out += `\n(mostrando ${MAX_FILAS} de ${filas.length})`;
        if (out.length > MAX_CHARS) out = out.slice(0, MAX_CHARS) + "\n(truncado)";
        return out;
      } catch (e) {
        return `Error de SQL: ${(e as Error).message}. Corrige la consulta y reintenta.`;
      }
    }
    default:
      return `Herramienta desconocida: ${nombre}.`;
  }
}
