// nube/functions/_shared/auth.ts
// Verifica el JWT de InsForge Auth. Devuelve null si el usuario es valido,
// o una Response 401 lista para retornar. Ningun endpoint responde sin esto.
import { jwtVerify, importSPKI } from "npm:jose@5";
import { corsHeaders } from "./cors.ts";

export async function requireUser(req: Request): Promise<Response | null> {
  const header = req.headers.get("Authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : null;
  if (!token) return sin401("falta token");
  try {
    const parts = token.split('.');
    if (parts.length !== 3) {
      return sin401("token malformado");
    }
    const headerObj = JSON.parse(atob(parts[0]));
    const alg = headerObj.alg;
    
    if (alg === "RS256") {
      const publicKeyPEM = Deno.env.get("JWT_PUBLIC_KEY");
      if (!publicKeyPEM) {
        console.error("requireUser: Falta el secreto JWT_PUBLIC_KEY en el servidor.");
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
    console.error("requireUser: Error al verificar token:", err?.message || err);
    return sin401("token invalido");
  }
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

