import { createClient } from '@insforge/sdk';

// Base URL y API key obtenidas de .insforge/project.json
const baseUrl = import.meta.env.VITE_INSFORGE_URL || 'https://z86cmn8g.us-west.insforge.app';
const anonKey = import.meta.env.VITE_INSFORGE_ANON_KEY || 'ik_c331a36a51904055d3caecb30709a438';

export const insforge = createClient({
  baseUrl,
  anonKey,
});

export interface KPIResult {
  ventas_mes: number;
  por_cobrar: number;
  n_pendientes: number;
  n_vencidas: number;
  monto_vencido: number;
  saldo_banco: {
    saldo: number;
    fecha: string;
  } | null;
  ultimo_sync: {
    momento: string;
    filas: Record<string, number>;
  } | null;
}

export interface FacturaPendiente {
  folio: number;
  fecha: string;
  rut_cliente: string;
  razon_social: string;
  total: number;
  dias_desde_emision: number;
}

export interface PendientesResult {
  pendientes: FacturaPendiente[];
  total: number;
}

export interface VentasResult {
  serie_mensual: {
    mes: string;
    total: number;
    n_facturas: number;
  }[];
  ranking_clientes: {
    rut_cliente: string;
    razon_social: string;
    total: number;
  }[];
  ranking_productos: {
    nombre_producto: string;
    unidades: number;
  }[];
}

export interface FlujoSemana {
  semana: number;
  label: string;
  ingresos: number;
  egresos: number;
  saldo_acumulado: number;
  riesgo: boolean;
}

export interface FlujoResult {
  saldo_inicial: number;
  semanas: FlujoSemana[];
  total_ingresos: number;
  total_egresos: number;
  fuera_horizonte: number;
}

// Helper interno para hacer fetch directo al subdominio de funciones de InsForge
async function invocarEdgeFunction(slug: string): Promise<any> {
  const httpClient = insforge.getHttpClient();
  const token = await httpClient.getValidAccessToken();
  
  // URL directa de funciones
  const functionsUrl = 'https://z86cmn8g.function2.insforge.app';
  const url = `${functionsUrl}/${slug}`;
  
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  
  const res = await fetch(url, {
    method: 'GET',
    headers,
  });

  
  if (!res.ok) {
    let errorDetail = '';
    try {
      errorDetail = await res.text();
    } catch {
      errorDetail = res.statusText;
    }
    throw new Error(`Error en API (${res.status}): ${errorDetail || 'Petición fallida'}`);
  }
  
  return await res.json();
}

// Helpers para invocar las Edge Functions
export async function getKPIs(): Promise<KPIResult> {
  return await invocarEdgeFunction('kpis');
}

export async function getPendientes(): Promise<PendientesResult> {
  return await invocarEdgeFunction('pendientes');
}

export async function getVentas(meses: number = 6): Promise<VentasResult> {
  return await invocarEdgeFunction(`ventas?meses=${meses}`);
}

export async function getFlujo(): Promise<FlujoResult> {
  return await invocarEdgeFunction('flujo');
}

