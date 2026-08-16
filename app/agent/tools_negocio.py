"""Servidor MCP in-process 'negocio': herramientas de datos de SOLO LECTURA que
el agente usa para responder con números exactos (deuda, ventas, flujo, costos).

Cada herramienta abre su propia conexión de solo lectura y reutiliza las
funciones ya probadas de app/briefing/data.py y app/negocio/. Mismo patrón que
app/agent/publish_tools.py (build_lienzo_server).
"""
import functools

import psycopg2
from psycopg2.extras import RealDictCursor

from app.canvas.artifacts import Artifact
from app.config import DB_URL
from app.briefing import data as deuda_data
from app.negocio import ventas as ventas_data
from app.negocio import ingreso_producto as ingreso_data
from app.negocio import unidades_producto as unidades_data
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


def _litros(n):
    """Litros en formato chileno: miles con punto, decimal con coma.

    No sirve un `.replace(",", ".")` sobre la línea entera como hace `_pesos`:
    acá el texto trae comas propias ("36 unidades, 16 facturas") y se las come.
    """
    entero, decimal = f"{float(n or 0):,.1f}".split(".")
    return f"{entero.replace(',', '.')},{decimal}"


def _texto(s):
    return {"content": [{"type": "text", "text": s}]}


# Desde cuántas filas conviene publicar la tabla en vez de mandarle el detalle
# al modelo. Bajo esto el detalle se lee bien en el chat, el modelo lo tiene a
# mano para razonar y una tabla sería ruido en pantalla.
UMBRAL_TABLA = 8


def tabla_o_resumen(collector, titulo, columnas, filas, cabecera, lineas):
    """Los datos van al lienzo; al modelo le llega solo el resumen. Devuelve texto.

    Una lista larga NO tiene por qué pasar por el modelo: la tool ya la tiene en
    memoria y el modelo se limitaría a re-escribirla para dibujarla. Ese viaje
    de ida y vuelta era lo que reventaba el presupuesto de tokens del turno
    (medido: completion_tokens=1500 exacto, el techo) y lo que llenaba el chat
    de tablas ilegibles.

    Además es más exacto: cada fila que cruza un LLM se puede transcribir mal.
    Una tabla publicada por la tool no la vuelve a escribir nadie.

    El modelo igual recibe la cabecera y una MUESTRA de hasta `UMBRAL_TABLA`
    líneas: sin eso no puede nombrar ningún caso concreto al redactar.

    Sin `collector` (o con pocas filas) devuelve el detalle entero, como antes.
    """
    if collector is None or len(filas) <= UMBRAL_TABLA:
        cuerpo = "\n".join(lineas)
        return f"{cabecera}\n{cuerpo}" if cuerpo else cabecera

    collector.add(Artifact(tipo="tabla", titulo=titulo,
                           payload={"columnas": columnas, "filas": filas}))
    return (_cabecera_con_muestra(cabecera, lineas) + "\n"
            f"[La tabla '{titulo}' con las {len(filas)} filas de detalle YA está "
            f"publicada en el lienzo y el usuario la está viendo. NO la publiques "
            f"de nuevo ni repitas las filas en el chat: resume en prosa.]")


def publicar_tabla_si_es_larga(collector, titulo, columnas, filas, resumen, lineas):
    """`tabla_o_resumen` envuelto como resultado de tool MCP.

    Las tools MCP devuelven {"content": [...]}; el orquestador usa la versión de
    texto directamente para el SQL ad-hoc.
    """
    return _texto(tabla_o_resumen(collector, titulo, columnas, filas, resumen, lineas))


def _alcance_ranking(devueltas, limite):
    """Avisa si el ranking viene cortado.

    El LIMIT lo aplica el SQL, así que la tool no sabe cuántos hay en total —
    pero sí sabe si llenó el cupo. Sin este aviso, el usuario ve 5 deudores y
    cree que son todos.
    """
    if devueltas >= limite:
        return f"se muestran los {devueltas} mayores, puede haber más"
    return f"son todos los que hay"


def _alcance_filtro(filtro, sin_filtro, con_filtro):
    """Cabecera que dice con qué filtro se respondió. La arma el código con el
    argumento que DE VERDAD llegó, no el modelo."""
    filtro = (filtro or "").strip()
    return con_filtro.format(filtro=filtro) if filtro else sin_filtro


def _cabecera_con_muestra(cabecera, lineas):
    """Resumen para el modelo: la cabecera más la punta de la lista, para que
    pueda nombrar los casos grandes en prosa sin recibir el detalle entero."""
    muestra = lineas[:UMBRAL_TABLA]
    resto = len(lineas) - len(muestra)
    if resto > 0:
        muestra.append(f"(+ {resto} más, en la tabla del lienzo)")
    return cabecera + "\n" + "\n".join(muestra)


def _resumen_por_cliente(facturas, dias):
    """Agrupa las facturas pendientes por cliente.

    Es la forma en que se pregunta la cobranza ("cuántos clientes me deben y
    cuántas facturas cada uno"), así que el modelo la recibe ya hecha en vez de
    tener que agregar 55 filas de cabeza — que además es donde se equivoca.
    """
    por_cliente = {}
    for f in facturas:
        acc = por_cliente.setdefault(f["cliente"], {"n": 0, "deuda": 0.0, "dias": 0})
        acc["n"] += 1
        acc["deuda"] += f["total"]
        acc["dias"] = max(acc["dias"], f["dias"])
    ordenados = sorted(por_cliente.items(), key=lambda kv: kv[1]["deuda"], reverse=True)
    total = sum(f["total"] for f in facturas)
    return _cabecera_con_muestra(
        f"Facturas pendientes con más de {dias} días: {len(facturas)} por "
        f"{_pesos(total)}, repartidas en {len(ordenados)} clientes. Por cliente:",
        [f"- {nombre}: {v['n']} facturas, {_pesos(v['deuda'])} "
         f"(la más vieja {v['dias']}d)" for nombre, v in ordenados])


def _tool_seguro(fn):
    """Convierte un error de BD en un resultado de tool legible (is_error) en vez
    de abortar el turno completo del agente (ej: Postgres caído)."""
    @functools.wraps(fn)
    async def wrapper(args):
        try:
            return await fn(args)
        except psycopg2.Error as e:
            return {
                "content": [{"type": "text", "text":
                    f"Error consultando la base de datos: {e}. "
                    "Avisa al usuario que PostgreSQL puede estar caído."}],
                "is_error": True,
            }
    return wrapper


def build_negocio_server(collector=None):
    """Construye el servidor MCP 'negocio'. Devuelve (server, lista_de_tool_names).

    Con `collector`, las tools de listado largo publican su tabla en el lienzo
    y le devuelven al modelo solo el resumen (ver publicar_tabla_si_es_larga).
    Sin él siguen devolviendo el detalle en texto.
    """
    from app.agent.tools_base import Registro, tool

    @tool("deuda_total", "Deuda total pendiente de cobro con desglose por antigüedad.", {})
    @_tool_seguro
    async def deuda_total(args):
        r = _con_cursor(deuda_data.resumen_cobranza)
        b = r["buckets"]
        return _texto(
            f"Deuda total pendiente: {_pesos(r['total'])} en {r['n_facturas']} facturas. "
            f"Al día {_pesos(b['al_dia'])}, 1-30d {_pesos(b['d1_30'])}, "
            f"31-60d {_pesos(b['d31_60'])}, +60d {_pesos(b['d60_mas'])}."
        )

    @tool("deuda_cliente", "Deuda pendiente de un cliente, por nombre o RUT.", {"nombre": str})
    @_tool_seguro
    async def deuda_cliente(args):
        r = _con_cursor(deuda_data.deuda_cliente, args["nombre"])
        if r["n_facturas"] == 0:
            return _texto(f"{args['nombre']}: sin deuda pendiente.")
        lineas = [f"- Folio {f['folio']} ({f['fecha']}): {_pesos(f['total'])}, {f['dias']}d"
                  for f in r["facturas"]]
        return _texto(f"{args['nombre']}: {_pesos(r['total'])} en {r['n_facturas']} facturas.\n"
                      + "\n".join(lineas))

    @tool("ranking_deudores", "Top N clientes por deuda pendiente (por defecto 5).",
          {"limite": int}, opcionales=("limite",))
    @_tool_seguro
    async def ranking_deudores(args):
        limite = args.get("limite", 5)
        r = _con_cursor(deuda_data.top_deudores, limite)
        if not r:
            return _texto("Sin deuda pendiente.")
        lineas = [f"{i+1}. {d['cliente']}: {_pesos(d['deuda'])} ({d['n']} facturas)"
                  for i, d in enumerate(r)]
        total = sum(d["deuda"] for d in r)
        return publicar_tabla_si_es_larga(
            collector, "Deuda por cliente",
            ["#", "Cliente", "Facturas", "Deuda"],
            [[i + 1, d["cliente"], d["n"], _pesos(d["deuda"])] for i, d in enumerate(r)],
            _cabecera_con_muestra(
                f"Top deudores ({_alcance_ranking(len(r), limite)}), "
                f"{_pesos(total)} entre los {len(r)}:", lineas),
            lineas)

    @tool("facturas_vencidas",
          "Facturas pendientes con más de N días (morosos; por defecto 30).",
          {"dias": int}, opcionales=("dias",))
    @_tool_seguro
    async def facturas_vencidas(args):
        dias = args.get("dias", 30)
        r = _con_cursor(deuda_data.facturas_vencidas, dias)
        if not r:
            return _texto(f"Ninguna factura pendiente con más de {dias} días.")
        return publicar_tabla_si_es_larga(
            collector, f"Facturas pendientes con más de {dias} días",
            ["Folio", "Cliente", "Monto", "Días"],
            [[f["folio"], f["cliente"], _pesos(f["total"]), f["dias"]] for f in r],
            _resumen_por_cliente(r, dias),
            [f"- Folio {f['folio']} {f['cliente']}: {_pesos(f['total'])}, {f['dias']}d"
             for f in r])

    @tool("ventas_total",
          "Total vendido. SIN fechas devuelve el total histórico completo; con "
          "desde y hasta (YYYY-MM-DD, los dos), el de ese rango.",
          {"desde": str, "hasta": str}, opcionales=("desde", "hasta"))
    @_tool_seguro
    async def ventas_total(args):
        r = _con_cursor(ventas_data.total, args.get("desde"), args.get("hasta"))
        periodo = (f" entre {r['desde']} y {r['hasta']}" if r["desde"] and r["hasta"]
                   else " (todo el histórico, sin filtro de fecha)")
        return _texto(f"Ventas{periodo}: {_pesos(r['total'])} en {r['n']} facturas.")

    @tool("ranking_clientes", "Top N clientes por ventas (por defecto 10).",
          {"limite": int}, opcionales=("limite",))
    @_tool_seguro
    async def ranking_clientes(args):
        limite = args.get("limite", 10)
        r = _con_cursor(ventas_data.ranking, limite)
        if not r:
            return _texto("Sin ventas.")
        lineas = [f"{i+1}. {c['cliente']}: {_pesos(c['total'])}" for i, c in enumerate(r)]
        return publicar_tabla_si_es_larga(
            collector, "Top clientes por ventas",
            ["#", "Cliente", "Ventas"],
            [[i + 1, c["cliente"], _pesos(c["total"])] for i, c in enumerate(r)],
            _cabecera_con_muestra(
                f"Top clientes por ventas ({_alcance_ranking(len(r), limite)}), "
                f"{_pesos(sum(c['total'] for c in r))} entre los {len(r)}:", lineas),
            lineas)

    @tool("ventas_cliente", "Ventas de un cliente, por nombre.", {"nombre": str})
    @_tool_seguro
    async def ventas_cliente(args):
        r = _con_cursor(ventas_data.por_cliente, args["nombre"])
        return _texto(f"{args['nombre']}: {_pesos(r['total_real'])} en {r['n_facturas']} "
                      f"facturas ({r['n_notas_credito']} notas de crédito).")

    @tool("ventas_producto", "Buscar LÍNEAS de venta por nombre de producto "
                             "(unidades y folios, NO dinero).", {"nombre": str})
    @_tool_seguro
    async def ventas_producto(args):
        r = _con_cursor(ventas_data.por_producto, args["nombre"])
        if not r:
            return _texto(f"Sin ventas que coincidan con '{args['nombre']}'.")
        unidades = sum((x["cantidad"] or 0) for x in r)
        return _texto(f"'{args['nombre']}': {len(r)} líneas de venta, {unidades} unidades en total.")

    @tool("ingreso_producto",
          "Ingreso neto en pesos por cerveza (producto + logística). Es la "
          "ÚNICA fuente de dinero por producto. Opcionales: desde, hasta "
          "(YYYY-MM-DD), cerveza (para el detalle de una), limite.",
          {"desde": str, "hasta": str, "cerveza": str, "limite": int},
          opcionales=("desde", "hasta", "cerveza", "limite"))
    @_tool_seguro
    async def ingreso_producto(args):
        """El ingreso de una cerveza es su línea MÁS la logística que le toca.

        Sumar `productos` daba un tercio de lo real y además ordenaba mal el
        ranking de clientes. La cabecera de alcance y cobertura la arma Python
        con los filtros que de verdad llegaron: el modelo puede olvidarlos.
        """
        desde, hasta = args.get("desde"), args.get("hasta")

        if args.get("cerveza"):
            r = _con_cursor(ingreso_data.por_cerveza, args["cerveza"], desde, hasta)
            if not r["ingreso"]:
                return _texto(f"Sin ventas de '{args['cerveza']}' ({r['alcance']}).")
            lineas = [f"- {c['cliente']}: {_pesos(c['ingreso'])} "
                      f"({c['unidades']:.0f} unidades)" for c in r["clientes"]]
            return _texto(
                f"{r['alcance']}\nCobertura: {r['cobertura']}\n"
                f"Ingreso neto: {_pesos(r['ingreso'])} en {r['n_documentos']} "
                f"documentos, {r['unidades']:.0f} unidades.\n"
                f"Principales clientes:\n" + "\n".join(lineas))

        r = _con_cursor(ingreso_data.ranking, desde, hasta, args.get("limite") or 10)
        if not r["cervezas"]:
            return _texto(f"Sin ventas de cerveza ({r['alcance']}).")
        lineas = [
            f"- {c['cerveza']}: {_pesos(c['ingreso'])} ({c['unidades']:.0f} unidades"
            + (f", {c['pct_estimado']:.0f}% estimado)" if c["pct_estimado"] else ")")
            for c in r["cervezas"]
        ]
        return _texto(f"{r['alcance']}\nCobertura: {r['cobertura']}\n"
                      + "\n".join(lineas))

    @tool("unidades_producto",
          "Volumen vendido por cerveza y formato, en LITROS y en unidades. "
          "Úsala para cuánta cerveza se vendió, cuántos barriles/botellas y "
          "comparaciones entre períodos. Ya agrupa las erratas del nombre y "
          "excluye logística, envases PET y CO2. Para PESOS usa "
          "ingreso_producto. Con por_mes=true abre una fila por mes, para "
          "informes mes a mes. Opcionales: desde, hasta (YYYY-MM-DD), cerveza, "
          "por_mes.",
          {"desde": str, "hasta": str, "cerveza": str, "por_mes": bool},
          opcionales=("desde", "hasta", "cerveza", "por_mes"))
    @_tool_seguro
    async def unidades_producto(args):
        """Existe para que el modelo NO tenga que escribir SQL para esto.

        Sin esta tool, ante "unidades por producto en julio vs junio" el modelo
        improvisaba SQL sobre `productos` y agrupaba por `nombre_producto`:
        devolvía "Botella 330cc Cream Ale" (96) y "Botella 330c Cream Ale" (24)
        como dos productos, y "Barril 30L APA" dos veces. Prohibirlo en el
        prompt no alcanza — mientras la pregunta no tenga herramienta, el modelo
        improvisa.

        Devuelve litros PRIMERO y ordena por litros: con la versión anterior el
        modelo sumó 120 botellas + 36 barriles = "156 unidades" y concluyó que
        Scotch Ale (94) vendió más que Stout Café (25). En litros es al revés
        —327 contra 394— porque esas botellas son 39,6 litros y los barriles
        1.080. La aritmética estaba bien; el ranking, dado vuelta.
        """
        r = _con_cursor(unidades_data.ranking, args.get("desde"),
                        args.get("hasta"), args.get("cerveza"),
                        bool(args.get("por_mes")))
        if not r["productos"]:
            return _texto(f"Sin ventas de cerveza ({r['alcance']}).")
        lineas = [(f"- {p['mes']} · " if p.get("mes") else "- ")
                  + f"{p['cerveza']} · {p['formato'] or 's/formato'}: "
                  f"{_litros(p['litros'])} L ({p['unidades']:.0f} unidades, "
                  f"{p['documentos']} facturas)"
                  for p in r["productos"]]
        return _texto(
            f"{r['alcance']}\n"
            f"Total: {_litros(r['total_litros'])} litros.\n"
            + "\n".join(lineas)
            + "\n[Compara por LITROS. Las unidades de formatos distintos no se "
              "suman entre sí: una botella es 0,33 L y un barril 30 L.]")

    @tool("flujo_caja", "Proyección de caja a 4 semanas (ingresos esperados − gastos). "
                        "Opcional: saldo_inicial.", {"saldo_inicial": float},
          opcionales=("saldo_inicial",))
    @_tool_seguro
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

    @tool("costos_sku",
          "Costo unitario por SKU. SIN receta devuelve todo el catálogo; con "
          "receta, solo esa cerveza.", {"receta": str}, opcionales=("receta",))
    @_tool_seguro
    async def costos_sku(args):
        r = _con_cursor(costos_data.costos_sku, args.get("receta"))
        if not r:
            return _texto("Sin SKUs cargados.")
        cabecera = _alcance_filtro(
            args.get("receta"),
            f"Costos de todo el catálogo ({len(r)} SKU):",
            f"Costos filtrados por receta \"{{filtro}}\" ({len(r)} SKU):")
        return _texto(cabecera + "\n" + "\n".join(
            f"- {s['codigo']} {s['cerveza']} {s['formato']}: costo {_pesos(s['costo_total'])}"
            for s in r))

    @tool("margenes", "Margen por cerveza y formato: precio de venta real menos "
                      "costo unitario. Cubre barriles Y botellas. El precio se "
                      "deduce de las facturas emitidas. SIN receta devuelve todo "
                      "el catálogo; con receta, solo esa cerveza.",
          {"receta": str}, opcionales=("receta",))
    @_tool_seguro
    async def margenes(args):
        r = _con_cursor(costos_data.margenes, args.get("receta"))
        if not r:
            return _texto("Sin SKUs cargados.")
        lineas = []
        for m in r:
            if m["margen"] is None:
                lineas.append(f"- {m['cerveza']} {m['formato']}: costo "
                              f"{_pesos(m['costo_total'])} (aún sin ventas, "
                              f"así que no hay precio de venta conocido)")
                continue
            if m["origen"] == "facturas":
                respaldo = (f" [{m['n_facturas']} facturas; promedio "
                            f"{_pesos(m['precio_promedio'])}]")
            else:
                respaldo = " [precio de lista, este SKU aún no se ha vendido]"
            if m["envase_pass_through"]:
                respaldo += " (el envase PET se factura aparte a costo)"
            lineas.append(f"- {m['cerveza']} {m['formato']}: precio "
                          f"{_pesos(m['precio_venta'])} − costo "
                          f"{_pesos(m['costo_comparable'])} = margen "
                          f"{_pesos(m['margen'])} ({m['margen_pct']}%)" + respaldo)
        cabecera = _alcance_filtro(
            args.get("receta"),
            f"Márgenes de todo el catálogo ({len(r)} SKU):",
            f"Márgenes filtrados por receta \"{{filtro}}\" ({len(r)} SKU):")
        return _texto(cabecera + "\n" + "\n".join(lineas))

    @tool("margen_periodo",
          "Margen REALIZADO de un período: cuánto se ganó de verdad entre dos "
          "fechas (ingreso menos costo de lo efectivamente vendido). Úsala para "
          "'cuánto gané en junio', 'margen del mes', 'utilidad del trimestre'. "
          "Fechas YYYY-MM-DD. NO uses margenes para esto: esa da el margen "
          "unitario de catálogo, no el total del período.",
          {"desde": str, "hasta": str})
    @_tool_seguro
    async def margen_periodo(args):
        r = _con_cursor(costos_data.margen_periodo, args["desde"], args["hasta"])
        if not r["por_producto"] and not r["sin_costo"]:
            return _texto(f"Sin ventas entre {args['desde']} y {args['hasta']}.")

        out = [
            f"Margen realizado {args['desde']} a {args['hasta']} "
            f"({r['n_facturas']} facturas):",
            f"- Ventas netas del período: {_pesos(r['ventas_netas'])}",
            f"- Ingreso con costo conocido: {_pesos(r['ingreso_costeado'])} "
            f"(cubre el {r['cobertura_pct']}% de la venta)",
            f"- Costo: {_pesos(r['costo'])}",
            f"- MARGEN: {_pesos(r['margen'])} ({r['margen_pct']}%)",
            "",
            "Por producto:",
        ]
        out += [f"- {f['cerveza']} {f['formato']}: {f['unidades']:.0f} u · "
                f"ingreso {_pesos(f['ingreso'])} · margen {_pesos(f['margen'])} "
                f"({f['margen_pct']}%)" for f in r["por_producto"]]
        if r["sin_costo"]:
            total = sum(s["ingreso"] for s in r["sin_costo"])
            out += ["", f"SIN COSTO CARGADO ({_pesos(total)} de venta que no entra "
                        f"en el margen — falta cargar su receta):"]
            out += [f"- {s['producto']}: {s['unidades']:.0f} u · {_pesos(s['ingreso'])}"
                    for s in r["sin_costo"]]
        return _texto("\n".join(out))

    @tool("margen_cliente",
          "Margen de cada cerveza AL PRECIO QUE PAGA UN CLIENTE, comparado con "
          "el precio general. Úsala cuando pregunten cuánto deja un cliente, a "
          "qué precio se le vende o si tiene descuento. Nombre o RUT del "
          "cliente; opcional filtrar por receta.",
          {"cliente": str, "receta": str}, opcionales=("receta",))
    @_tool_seguro
    async def margen_cliente(args):
        r = _con_cursor(costos_data.margen_cliente, args["cliente"], args.get("receta"))
        if not r:
            return _texto(f"No hay ventas con costo cargado para "
                          f"'{args['cliente']}' (revisa el nombre o el RUT).")
        lineas = []
        for m in r:
            dif = ""
            if m["precio_general"] and m["precio_cliente"] != m["precio_general"]:
                delta = m["precio_cliente"] - m["precio_general"]
                dif = (f" · general {_pesos(m['precio_general'])} "
                       f"({'+' if delta > 0 else ''}{_pesos(delta)}, "
                       f"margen general {m['margen_pct_general']}%)")
            lineas.append(
                f"- {m['cerveza']} {m['formato']}: paga "
                f"{_pesos(m['precio_cliente'])} − costo {_pesos(m['costo'])} "
                f"= margen {_pesos(m['margen'])} ({m['margen_pct']}%)"
                f"{dif} [{m['n_facturas']} facturas]")
        # El cliente va SIEMPRE en la cabecera: un margen sin decir de quién es
        # no se puede interpretar, y los descuentos por cliente son grandes.
        cabecera = _alcance_filtro(
            args.get("receta"),
            f"Márgenes al precio de {args['cliente']}, todo el catálogo "
            f"({len(r)} SKU):",
            f"Márgenes al precio de {args['cliente']}, filtrados por receta "
            f"\"{{filtro}}\" ({len(r)} SKU):")
        return _texto(cabecera + "\n" + "\n".join(lineas))

    @tool("listar_gastos", "Lista los gastos pendientes (cuentas por pagar) con su id, "
                           "para ubicar uno antes de borrarlo, editarlo o marcarlo pagado. "
                           "Opcional: filtro de texto sobre la descripción.",
          {"filtro": str}, opcionales=("filtro",))
    @_tool_seguro
    async def listar_gastos(args):
        r = _con_cursor(gastos_data.listar, args.get("filtro"))
        if not r:
            suf = f" que coincidan con '{args['filtro']}'." if args.get("filtro") else "."
            return _texto("No hay gastos pendientes" + suf)
        cabecera = _alcance_filtro(
            args.get("filtro"),
            f"Todos los gastos pendientes ({len(r)}):",
            f"Gastos pendientes que calzan con \"{{filtro}}\" ({len(r)}):")
        return _texto(cabecera + "\n" + "\n".join(
            f"- id {g['id']}: {g['descripcion']} · {_pesos(g['monto'])} · vence {g['fecha_vencimiento']}"
            + (f" · {g['proveedor']}" if g.get("proveedor") else "")
            for g in r))

    @tool("clientes_en_riesgo",
          "Clientes con señales de alerta comercial (dormido, caída de consumo, "
          "baja frecuencia, nuevo sin recompra), priorizados (los grandes primero). "
          "Úsala para diagnosticar la salud de la cartera y a quién contactar.", {})
    @_tool_seguro
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
          "por defecto pendiente).", {"estado": str}, opcionales=("estado",))
    @_tool_seguro
    async def listar_seguimiento(args):
        estado = (args.get("estado") or "pendiente").strip() or "pendiente"
        r = _con_cursor(seguimiento_data.listar, estado)
        if not r:
            return _texto(f"No hay seguimientos en estado '{estado}'.")
        return _texto(f"Seguimientos en estado \"{estado}\" ({len(r)}):\n" + "\n".join(
            f"- id {s['id']} [{s['prioridad']}] "
            f"{s.get('razon_social') or s['rut_cliente']}: {s['motivo']}"
            for s in r))

    # Los nombres salen del propio registro: mantener una lista a mano al lado
    # de las tools es una copia que se desincroniza en silencio.
    registro = Registro("negocio", [
        deuda_total, deuda_cliente, ranking_deudores, facturas_vencidas,
        ventas_total, ranking_clientes, ventas_cliente, ventas_producto,
        ingreso_producto, unidades_producto,
        flujo_caja, costos_sku, margenes, margen_periodo, margen_cliente,
        listar_gastos,
        clientes_en_riesgo, listar_seguimiento,
    ])
    return registro, registro.nombres()
