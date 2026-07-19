// functions/_shared/auth_test.ts
// La auth debe fijar el algoritmo por rama y tratar un secret HS256 ausente
// como error de configuracion (nunca verificar con clave vacia).
import { assertEquals } from "jsr:@std/assert@1";
import { generateKeyPair, exportSPKI, SignJWT } from "npm:jose@5";
import { requireUser } from "./auth.ts";

function reqCon(token: string | null): Request {
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return new Request("http://local/api", { headers });
}

async function status(token: string | null): Promise<number | null> {
  const r = await requireUser(reqCon(token));
  return r === null ? null : r.status;
}

Deno.test("RS256 valido firmado con la clave privada pasa", async () => {
  const { publicKey, privateKey } = await generateKeyPair("RS256", { extractable: true });
  Deno.env.set("JWT_PUBLIC_KEY", await exportSPKI(publicKey));
  const token = await new SignJWT({ sub: "u1" })
    .setProtectedHeader({ alg: "RS256" }).setExpirationTime("5m").sign(privateKey);
  assertEquals(await status(token), null);
});

Deno.test("HS256 valido con el secret del servidor pasa", async () => {
  Deno.env.set("INSFORGE_JWT_SECRET", "secreto-de-prueba");
  const token = await new SignJWT({ sub: "u1" }).setProtectedHeader({ alg: "HS256" })
    .setExpirationTime("5m").sign(new TextEncoder().encode("secreto-de-prueba"));
  assertEquals(await status(token), null);
});

Deno.test("HS256 sin secret configurado es error de configuracion", async () => {
  Deno.env.delete("INSFORGE_JWT_SECRET");
  Deno.env.delete("JWT_SECRET");
  const token = await new SignJWT({ sub: "u1" }).setProtectedHeader({ alg: "HS256" })
    .setExpirationTime("5m").sign(new TextEncoder().encode("cualquier-cosa"));
  const r = await requireUser(reqCon(token));
  assertEquals(r?.status, 401);
  const body = JSON.parse(await r!.text());
  assertEquals(body.detalle, "error de configuracion en servidor");
});

Deno.test("alg none rechazado", async () => {
  const header = btoa(JSON.stringify({ alg: "none" })).replace(/=+$/, "");
  const payload = btoa(JSON.stringify({ sub: "u1" })).replace(/=+$/, "");
  assertEquals(await status(`${header}.${payload}.x`), 401);
});

Deno.test("sin token 401", async () => {
  assertEquals(await status(null), 401);
});
