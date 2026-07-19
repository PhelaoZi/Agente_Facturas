// nube/functions/_shared/cors.ts
// Configuración de cabeceras CORS y helpers para responder a peticiones preflight (OPTIONS)
// desde la PWA en el celular.

export const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With",
};

/**
 * Maneja peticiones OPTIONS de preflight del navegador.
 * Si es OPTIONS, retorna una Response con estado 204 y cabeceras CORS.
 * Si no es OPTIONS, retorna null para continuar el flujo normal.
 */
export function handleCors(req: Request): Response | null {
  if (req.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: corsHeaders,
    });
  }
  return null;
}

/**
 * Retorna una respuesta JSON que incluye las cabeceras CORS necesarias.
 */
export function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      ...corsHeaders,
      "Content-Type": "application/json",
    },
  });
}
