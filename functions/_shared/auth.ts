// nube/functions/_shared/auth.ts
// Verifica el JWT de InsForge Auth. Devuelve null si el usuario es valido,
// o una Response 401 lista para retornar. Ningun endpoint responde sin esto.
import { jwtVerify } from "npm:jose@5";

export async function requireUser(req: Request): Promise<Response | null> {
  const header = req.headers.get("Authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : null;
  if (!token) return sin401("falta token");
  try {
    const secreto = new TextEncoder().encode(
      Deno.env.get("INSFORGE_JWT_SECRET") ?? "",
    );
    await jwtVerify(token, secreto);
    return null;
  } catch {
    return sin401("token invalido");
  }
}

function sin401(detalle: string): Response {
  return new Response(JSON.stringify({ error: "no autorizado", detalle }), {
    status: 401,
    headers: { "Content-Type": "application/json" },
  });
}
