// nube/functions/_shared/db.ts
// Cliente Postgres compartido por todas las functions (solo lectura de views).
import postgres from "npm:postgres@3.4.5";

let sql: ReturnType<typeof postgres> | null = null;

export function db() {
  if (!sql) {
    const url = Deno.env.get("INSFORGE_DB_URL");
    if (!url) throw new Error("Falta el secret INSFORGE_DB_URL");
    sql = postgres(url, { max: 1, prepare: false });
  }
  return sql;
}
