"""Servidor MCP in-process 'negocio': herramientas de datos de SOLO LECTURA que
el agente usa para responder con números exactos (deuda, ventas, flujo, costos).

Cada herramienta abre su propia conexión de solo lectura y reutiliza las
funciones ya probadas de app/briefing/data.py y app/negocio/. Mismo patrón que
app/agent/publish_tools.py (build_lienzo_server).
"""
import psycopg2
from psycopg2.extras import RealDictCursor

from app.config import DB_URL
from app.briefing import data as deuda_data
from app.negocio import ventas as ventas_data
from app.negocio import costos as costos_data
from app.negocio import flujo as flujo_data
from app.negocio import gastos as gastos_data
from app.negocio import clientes as clientes_data
from app.negocio import seguimiento as seguimiento_data


def _con_cursor(fn, *args, **kwargs):
    """Abre conexión RealDictCursor, ejecuta fn(cur, ...), cierra y devuelve."""
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            return fn(cur, *args, **kwargs)
    finally:
        conn.close()


def _pesos(n):
    if n is None:
        return "$0"
    return f"${int(round(float(n))):,}".replace(",", ".")


def _texto(s):
    return {"content": [{"type": "text", "text": s}]}


def build_negocio_server():
    """Construye el servidor MCP 'negocio'. Devuelve (server, lista_de_tool_names)."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool("deuda_total", "Deuda total pendiente de cobro con desglose por antigüedad.", {})
    async def deuda_total(args):
        r = _con_cursor(deuda_data.resumen_cobranza)
        b = r["buckets"]
        return _texto(
            f"Deuda total pendiente: {_pesos(r['total'])} en {r['n_facturas']} facturas. "
            f"Al día {_pesos(b['al_dia'])}, 1-30d {_pesos(b['d1_30'])}, "
            f"31-60d {_pesos(b['d31_60'])}, +60d {_pesos(b['d60_mas'])}."
        )

    @tool("deuda_cliente", "Deuda pendiente de un cliente, por nombre o RUT.", {"nombre": str})
    async def deuda_cliente(args):
        r = _con_cursor(deuda_data.deuda_cliente, args["nombre"])
        if r["n_facturas"] == 0:
            return _texto(f"{args['nombre']}: sin deuda pendiente.")
        lineas = [f"- Folio {f['folio']} ({f['fecha']}): {_pesos(f['total'])}, {f['dias']}d"
                  for f in r["facturas"]]
        return _texto(f"{args['nombre']}: {_pesos(r['total'])} en {r['n_facturas']} facturas.\n"
                      + "\n".join(lineas))

    @tool("ranking_deudores", "Top N clientes por deuda pendiente.", {"limite": int})
    async def ranking_deudores(args):
        r = _con_cursor(deuda_data.top_deudores, args.get("limite", 5))
        if not r:
            return _texto("Sin deuda pendiente.")
        return _texto("\n".join(
            f"{i+1}. {d['cliente']}: {_pesos(d['deuda'])} ({d['n']} facturas)"
            for i, d in enumerate(r)))

    @tool("facturas_vencidas", "Facturas pendientes con más de N días (morosos).", {"dias": int})
    async def facturas_vencidas(args):
        r = _con_cursor(deuda_data.facturas_vencidas, args.get("dias", 30))
        if not r:
            return _texto("Ninguna factura vencida sobre el umbral.")
        return _texto("\n".join(
            f"- Folio {f['folio']} {f['cliente']}: {_pesos(f['total'])}, {f['dias']}d"
            for f in r))

    @tool("ventas_total", "Total vendido. Opcional: rango desde/hasta (YYYY-MM-DD).",
          {"desde": str, "hasta": str})
    async def ventas_total(args):
        r = _con_cursor(ventas_data.total, args.get("desde"), args.get("hasta"))
        periodo = f" entre {r['desde']} y {r['hasta']}" if r["desde"] and r["hasta"] else ""
        return _texto(f"Ventas{periodo}: {_pesos(r['total'])} en {r['n']} facturas.")

    @tool("ranking_clientes", "Top N clientes por ventas.", {"limite": int})
    async def ranking_clientes(args):
        r = _con_cursor(ventas_data.ranking, args.get("limite", 10))
        if not r:
            return _texto("Sin ventas.")
        return _texto("\n".join(f"{i+1}. {c['cliente']}: {_pesos(c['total'])}"
                                for i, c in enumerate(r)))

    @tool("ventas_cliente", "Ventas de un cliente, por nombre.", {"nombre": str})
    async def ventas_cliente(args):
        r = _con_cursor(ventas_data.por_cliente, args["nombre"])
        return _texto(f"{args['nombre']}: {_pesos(r['total_real'])} en {r['n_facturas']} "
                      f"facturas ({r['n_notas_credito']} notas de crédito).")

    @tool("ventas_producto", "Buscar ventas por nombre de producto.", {"nombre": str})
    async def ventas_producto(args):
        r = _con_cursor(ventas_data.por_producto, args["nombre"])
        if not r:
            return _texto(f"Sin ventas que coincidan con '{args['nombre']}'.")
        unidades = sum((x["cantidad"] or 0) for x in r)
        return _texto(f"'{args['nombre']}': {len(r)} líneas de venta, {unidades} unidades en total.")

    @tool("flujo_caja", "Proyección de caja a 4 semanas (ingresos esperados − gastos). "
                        "Opcional: saldo_inicial.", {"saldo_inicial": float})
    async def flujo_caja(args):
        r = _con_cursor(flujo_data.proyectar_flujo, args.get("saldo_inicial"))
        lineas = [
            f"- {s['label']}: ingresos {_pesos(s['ingresos'])}, egresos {_pesos(s['egresos'])}, "
            f"saldo {_pesos(s['saldo_acumulado'])}" + (" [RIESGO]" if s["riesgo"] else "")
            for s in r["semanas"]
        ]
        return _texto(
            f"Flujo de caja 4 semanas (saldo inicial {_pesos(r['saldo_inicial'])}):\n"
            + "\n".join(lineas)
            + f"\nTotales: ingresos {_pesos(r['total_ingresos'])}, "
              f"egresos {_pesos(r['total_egresos'])}.")

    @tool("costos_sku", "Costo unitario por SKU. Opcional: filtrar por receta.", {"receta": str})
    async def costos_sku(args):
        r = _con_cursor(costos_data.costos_sku, args.get("receta"))
        if not r:
            return _texto("Sin SKUs cargados.")
        return _texto("\n".join(
            f"- {s['codigo']} {s['cerveza']} {s['formato']}: costo {_pesos(s['costo_total'])}"
            for s in r))

    @tool("margenes", "Margen por cerveza/formato (precio venta − costo; solo barriles). "
                      "Opcional: filtrar por receta.", {"receta": str})
    async def margenes(args):
        r = _con_cursor(costos_data.margenes, args.get("receta"))
        if not r:
            return _texto("Sin SKUs cargados.")
        lineas = []
        for m in r:
            if m["margen"] is None:
                lineas.append(f"- {m['cerveza']} {m['formato']}: costo {_pesos(m['costo_total'])} "
                              f"(sin precio de venta confirmado)")
            else:
                lineas.append(f"- {m['cerveza']} {m['formato']}: precio {_pesos(m['precio_venta'])} "
                              f"− costo {_pesos(m['costo_total'])} = margen {_pesos(m['margen'])} "
                              f"({m['margen_pct']}%)")
        return _texto("\n".join(lineas))

    @tool("listar_gastos", "Lista los gastos pendientes (cuentas por pagar) con su id, "
                           "para ubicar uno antes de borrarlo, editarlo o marcarlo pagado. "
                           "Opcional: filtro de texto sobre la descripción.",
          {"filtro": str})
    async def listar_gastos(args):
        r = _con_cursor(gastos_data.listar, args.get("filtro"))
        if not r:
            suf = f" que coincidan con '{args['filtro']}'." if args.get("filtro") else "."
            return _texto("No hay gastos pendientes" + suf)
        return _texto("\n".join(
            f"- id {g['id']}: {g['descripcion']} · {_pesos(g['monto'])} · vence {g['fecha_vencimiento']}"
            + (f" · {g['proveedor']}" if g.get("proveedor") else "")
            for g in r))

    @tool("clientes_en_riesgo",
          "Clientes con señales de alerta comercial (dormido, caída de consumo, "
          "baja frecuencia, nuevo sin recompra), priorizados (los grandes primero). "
          "Úsala para diagnosticar la salud de la cartera y a quién contactar.", {})
    async def clientes_en_riesgo(args):
        r = _con_cursor(clientes_data.salud_clientes)
        if not r:
            return _texto("Ningún cliente con señales de alerta ahora mismo.")
        lineas = [f"- [{c['prioridad']}] {c['cliente']} (RUT {c['rut']}): {c['motivo']}"
                  for c in r]
        return _texto("Clientes en riesgo (priorizados):\n" + "\n".join(lineas))

    @tool("listar_seguimiento",
          "Lista la lista de seguimiento comercial con su id y estado, para ubicar "
          "uno antes de marcarlo. Opcional: estado (pendiente/contactado/descartado; "
          "por defecto pendiente).", {"estado": str})
    async def listar_seguimiento(args):
        estado = (args.get("estado") or "pendiente").strip() or "pendiente"
        r = _con_cursor(seguimiento_data.listar, estado)
        if not r:
            return _texto(f"No hay seguimientos en estado '{estado}'.")
        return _texto("\n".join(
            f"- id {s['id']} [{s['prioridad']}] "
            f"{s.get('razon_social') or s['rut_cliente']}: {s['motivo']}"
            for s in r))

    server = create_sdk_mcp_server(name="negocio", version="1.0.0", tools=[
        deuda_total, deuda_cliente, ranking_deudores, facturas_vencidas,
        ventas_total, ranking_clientes, ventas_cliente, ventas_producto,
        flujo_caja, costos_sku, margenes, listar_gastos,
        clientes_en_riesgo, listar_seguimiento,
    ])
    tool_names = [
        "mcp__negocio__deuda_total", "mcp__negocio__deuda_cliente",
        "mcp__negocio__ranking_deudores", "mcp__negocio__facturas_vencidas",
        "mcp__negocio__ventas_total", "mcp__negocio__ranking_clientes",
        "mcp__negocio__ventas_cliente", "mcp__negocio__ventas_producto",
        "mcp__negocio__flujo_caja", "mcp__negocio__costos_sku", "mcp__negocio__margenes",
        "mcp__negocio__listar_gastos",
        "mcp__negocio__clientes_en_riesgo", "mcp__negocio__listar_seguimiento",
    ]
    return server, tool_names
