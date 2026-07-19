// functions/_shared/auth.ts
// Verifica el JWT: RS256 (InsForge Auth, clave publica) o HS256 (secret propio,
// tests de paridad). Devuelve null si es valido, o una Response 401 lista.
// Endurecido: cada rama fija su algoritmo en jwtVerify (`algorithms`) y un
// secret HS256 ausente es error de configuracion, nunca clave vacia.
import { jwtVerify, importSPKI } from "npm:jose@5";
import { corsHeaders } from "./cors.ts";

export async function requireUser(req: Request): Promise<Response | null> {
  const header = req.headers.get("Authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : null;
  if (!token) return sin401("falta token");
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return sin401("token malformado");
    const headerObj = JSON.parse(atob(parts[0]));
    const alg = headerObj.alg;

    if (alg === "RS256") {
      const publicKeyPEM = Deno.env.get("JWT_PUBLIC_KEY");
      if (!publicKeyPEM) {
        console.error("requireUser: falta JWT_PUBLIC_KEY en el servidor");
        return sin401("error de configuracion en servidor");
      }
      const publicKey = await importSPKI(publicKeyPEM, "RS256");
      await jwtVerify(token, publicKey, { algorithms: ["RS256"] });
    } else if (alg === "HS256") {
      const secretStr = Deno.env.get("INSFORGE_JWT_SECRET") ?? Deno.env.get("JWT_SECRET") ?? "";
      if (!secretStr) {
        console.error("requireUser: falta INSFORGE_JWT_SECRET en el servidor");
        return sin401("error de configuracion en servidor");
      }
      await jwtVerify(token, new TextEncoder().encode(secretStr), { algorithms: ["HS256"] });
    } else {
      return sin401("algoritmo no soportado");
    }
    return null;
  } catch (err) {
    console.error("requireUser: token invalido:", (err as Error)?.message ?? err);
    return sin401("token invalido");
  }
}

function sin401(detalle: string): Response {
  return new Response(JSON.stringify({ error: "no autorizado", detalle }), {
    status: 401,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

