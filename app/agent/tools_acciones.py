"""Servidor MCP in-process 'acciones': herramientas que PROPONEN una escritura
sin ejecutarla. El agente nunca escribe en la BD; solo publica una tarjeta de
confirmación (Artifact tipo 'accion'). La escritura real la hace el endpoint
determinista del dashboard al apretar Confirmar.

Mismo patrón que app/agent/publish_tools.py (build_lienzo_server).
"""
import psycopg2
from psycopg2.extras import RealDictCursor

from app.canvas.artifacts import Artifact, Collector
from app.config import DB_URL
from app.negocio import cobranza
from app.negocio import gastos
from app.negocio.gastos import validar_gasto
from app.negocio import seguimiento


def _pesos(n):
    try:
        return "$" + f"{int(round(float(n))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "$0"


def _fecha_dmy(fecha):
    """Formatea 'YYYY-MM-DD' como 'DD/MM/YYYY'; si no parsea, devuelve el original."""
    from datetime import datetime
    try:
        return datetime.strptime(str(fecha), "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return str(fecha or "")


def _resumen_gasto(params: dict) -> str:
    desc = params.get("descripcion", "")
    extra = f" · {params['proveedor']}" if params.get("proveedor") else ""
    return f"Gasto: {desc} · {_pesos(params.get('monto'))} · vence {_fecha_dmy(params.get('fecha', ''))}{extra}"


def accion_gasto_artifact(params: dict) -> Artifact:
    """Construye el artefacto de acción 'registrar_gasto' a partir de params ya validados."""
    return Artifact(
        tipo="accion",
        titulo="Confirmar gasto",
        payload={
            "tipo_accion": "registrar_gasto",
            "params": params,
            "resumen": _resumen_gasto(params),
        },
    )


def marcar_incobrable_artifact(c: dict) -> Artifact:
    """Tarjeta para castigar la deuda de un cliente. El resumen muestra RUT y
    monto: es lo único que separa castigar a BIER BAR de castigar a otro
    cliente de nombre parecido."""
    deuda = _pesos(c.get("deuda"))
    n = int(c.get("n_facturas") or 0)
    return Artifact(
        tipo="accion",
        titulo="Confirmar cliente incobrable",
        payload={
            "tipo_accion": "marcar_cliente_incobrable",
            "params": {"rut_cliente": c["rut_cliente"]},
            "resumen": (f"Marcar INCOBRABLE a {c['razon_social']} ({c['rut_cliente']}). "
                        f"Salen del por cobrar {n} factura(s) por {deuda}. "
                        "Las facturas siguen registradas como NO pagadas."),
        },
    )


def reactivar_cliente_artifact(c: dict) -> Artifact:
    """Tarjeta para deshacer el castigo: el cliente vuelve a activo."""
    deuda = _pesos(c.get("deuda"))
    n = int(c.get("n_facturas") or 0)
    return Artifact(
        tipo="accion",
        titulo="Confirmar reactivar cliente",
        payload={
            "tipo_accion": "reactivar_cliente",
            "params": {"rut_cliente": c["rut_cliente"]},
            "resumen": (f"Reactivar a {c['razon_social']} ({c['rut_cliente']}): vuelve a "
                        f"estado activo y sus {n} factura(s) por {deuda} regresan "
                        "al por cobrar."),
        },
    )


def _buscar_clientes(texto):
    """Busca clientes por nombre o RUT con su propia conexión de solo lectura."""
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            return cobranza.buscar_clientes(cur, texto)
    finally:
        conn.close()


def _obtener_gasto(id):
    """Lee un gasto por id con su propia conexión de solo lectura."""
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            return gastos.obtener_gasto(cur, id)
    finally:
        conn.close()


def borrar_gasto_artifact(g) -> Artifact:
    resumen = (f"Borrar: {g['descripcion']} · {_pesos(g['monto'])} · "
               f"vence {_fecha_dmy(g['fecha_vencimiento'])}")
    return Artifact(tipo="accion", titulo="Confirmar borrado",
        payload={"tipo_accion": "borrar_gasto", "params": {"id": g["id"]}, "resumen": resumen})


def marcar_pagado_artifact(g, fecha_pago) -> Artifact:
    resumen = (f"Marcar pagado: {g['descripcion']} · {_pesos(g['monto'])} · "
               f"el {_fecha_dmy(fecha_pago)}")
    return Artifact(tipo="accion", titulo="Confirmar pago de gasto",
        payload={"tipo_accion": "marcar_gasto_pagado",
                 "params": {"id": g["id"], "fecha_pago": fecha_pago}, "resumen": resumen})


def editar_gasto_artifact(g, params, cambios) -> Artifact:
    partes = []
    for col, nuevo in cambios.items():
        viejo = g.get(col)
        if col == "monto":
            partes.append(f"monto {_pesos(viejo)} → {_pesos(nuevo)}")
        elif col == "fecha_vencimiento":
            partes.append(f"vence {_fecha_dmy(viejo)} → {_fecha_dmy(nuevo)}")
        else:
            partes.append(f"{col}: {viejo} → {nuevo}")
    resumen = f"Editar {g['descripcion']}: " + ", ".join(partes)
    return Artifact(tipo="accion", titulo="Confirmar edición",
        payload={"tipo_accion": "editar_gasto", "params": params, "resumen": resumen})


def agregar_seguimiento_artifact(params: dict) -> Artifact:
    """Tarjeta para agregar un cliente a la lista de seguimiento. `cliente`
    (razón social) es solo para mostrar; no viaja en los params de la acción."""
    quien = params.get("cliente") or params.get("rut_cliente")
    resumen = f"Seguimiento: {quien} · {params.get('motivo', '')} · prioridad {params.get('prioridad', 'media')}"
    accion_params = {
        "rut_cliente": params.get("rut_cliente"),
        "motivo": params.get("motivo"),
        "prioridad": params.get("prioridad", "media"),
        "senales": params.get("senales"),
    }
    return Artifact(tipo="accion", titulo="Confirmar seguimiento",
        payload={"tipo_accion": "agregar_seguimiento", "params": accion_params,
                 "resumen": resumen})


def marcar_seguimiento_artifact(s, estado) -> Artifact:
    quien = s.get("razon_social") or s["rut_cliente"]
    resumen = f"Marcar {quien} como {estado}: {s.get('motivo', '')}"
    return Artifact(tipo="accion", titulo="Confirmar seguimiento",
        payload={"tipo_accion": "marcar_seguimiento",
                 "params": {"id": s["id"], "estado": estado}, "resumen": resumen})


def marcar_factura_pagada_artifact(f, fecha_pago) -> Artifact:
    resumen = (f"Marcar pagada F.{f['folio']} · {f['razon_social']} · "
               f"{_pesos(f['total'])} · pago el {_fecha_dmy(fecha_pago)}")
    return Artifact(tipo="accion", titulo="Confirmar pago de factura",
        payload={"tipo_accion": "marcar_factura_pagada",
                 "params": {"folio": f["folio"], "fecha_pago": fecha_pago},
                 "resumen": resumen})


def corregir_fecha_pago_artifact(f, fecha_pago) -> Artifact:
    resumen = (f"Corregir pago F.{f['folio']} · {f['razon_social']} · "
               f"{_fecha_dmy(f['fecha_pago'])} → {_fecha_dmy(fecha_pago)}")
    return Artifact(tipo="accion", titulo="Confirmar corrección de fecha de pago",
        payload={"tipo_accion": "corregir_fecha_pago",
                 "params": {"folio": f["folio"], "fecha_pago": fecha_pago},
                 "resumen": resumen})


def _obtener_factura(folio):
    """Lee una factura por folio con su propia conexión de solo lectura."""
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            return cobranza.obtener_factura(cur, folio)
    finally:
        conn.close()


def _obtener_seguimiento(id):
    """Lee un seguimiento por id con su propia conexión de solo lectura."""
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            return seguimiento.obtener(cur, id)
    finally:
        conn.close()


def build_acciones_server(collector: Collector):
    """Construye el servidor MCP 'acciones' ligado a un collector.

    Devuelve (server, lista_de_nombres_de_tools).
    """
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool(
        "proponer_gasto",
        "Propone registrar un gasto (cuenta por pagar) para que el usuario lo "
        "confirme con un botón. NO escribe en la base de datos: solo publica una "
        "tarjeta de confirmación. Úsala cuando el usuario pida anotar/registrar un gasto.",
        {"descripcion": str, "monto": str, "fecha": str, "proveedor": str, "categoria": str},
    )
    async def proponer_gasto(args):
        try:
            limpio = validar_gasto(
                args.get("descripcion"), args.get("monto"), args.get("fecha"),
                args.get("proveedor"), args.get("categoria"))
        except ValueError as e:
            return {"content": [{"type": "text",
                    "text": f"No puedo proponer el gasto: {e} Pídele al usuario el dato que falta."}]}
        collector.add(accion_gasto_artifact(limpio))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — {_resumen_gasto(limpio)}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar para que se registre. "
                        "NO afirmes que el gasto ya quedó registrado."}]}

    @tool("proponer_borrar_gasto",
          "Propone BORRAR un gasto (cuenta por pagar) por su id, para que el usuario "
          "confirme. NO borra: publica una tarjeta. Usa listar_gastos primero para el id.",
          {"id": int})
    async def proponer_borrar_gasto(args):
        g = _obtener_gasto(args.get("id"))
        if not g:
            return {"content": [{"type": "text",
                    "text": f"No encontré un gasto con id {args.get('id')}. Usa listar_gastos para ver los ids."}]}
        collector.add(borrar_gasto_artifact(g))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — Borrar {g['descripcion']}. Quedó como tarjeta; "
                        "el usuario debe apretar Confirmar. NO afirmes que ya se borró."}]}

    @tool("proponer_marcar_gasto_pagado",
          "Propone marcar un gasto como PAGADO por su id (fecha opcional, por defecto hoy). "
          "NO escribe: publica una tarjeta. Usa listar_gastos primero.",
          {"id": int, "fecha": str})
    async def proponer_marcar_gasto_pagado(args):
        g = _obtener_gasto(args.get("id"))
        if not g:
            return {"content": [{"type": "text",
                    "text": f"No encontré un gasto con id {args.get('id')}. Usa listar_gastos para ver los ids."}]}
        from datetime import date
        fecha = (args.get("fecha") or "").strip() or date.today().isoformat()
        collector.add(marcar_pagado_artifact(g, fecha))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — Marcar pagado {g['descripcion']}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar. NO afirmes que ya se pagó."}]}

    @tool("proponer_editar_gasto",
          "Propone EDITAR campos de un gasto por su id (descripcion/monto/fecha/proveedor/categoria). "
          "NO escribe: publica una tarjeta. Usa listar_gastos primero. Pasa solo los campos a cambiar.",
          {"id": int, "descripcion": str, "monto": str, "fecha": str, "proveedor": str, "categoria": str})
    async def proponer_editar_gasto(args):
        g = _obtener_gasto(args.get("id"))
        if not g:
            return {"content": [{"type": "text",
                    "text": f"No encontré un gasto con id {args.get('id')}. Usa listar_gastos para ver los ids."}]}
        params = {"id": g["id"]}
        for campo in ("descripcion", "monto", "fecha", "proveedor", "categoria"):
            v = args.get(campo)
            if v is not None and str(v).strip() != "":
                params[campo] = v
        try:
            clean = gastos.validar_editar(params)
        except ValueError as e:
            return {"content": [{"type": "text", "text": f"No puedo proponer la edición: {e}"}]}
        collector.add(editar_gasto_artifact(g, params, clean["cambios"]))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — Editar {g['descripcion']}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar. NO afirmes que ya se editó."}]}

    @tool("proponer_agregar_seguimiento",
          "Propone agregar un cliente a la lista de seguimiento comercial, para que "
          "el usuario confirme. NO escribe: publica una tarjeta. Pasa rut_cliente, "
          "cliente (razón social, para mostrar), motivo, prioridad (alta/media) y "
          "senales (texto opcional). Úsala tras diagnosticar con clientes_en_riesgo.",
          {"rut_cliente": str, "cliente": str, "motivo": str,
           "prioridad": str, "senales": str})
    async def proponer_agregar_seguimiento(args):
        params = {"rut_cliente": args.get("rut_cliente"), "motivo": args.get("motivo"),
                  "prioridad": (args.get("prioridad") or "media"),
                  "senales": args.get("senales")}
        try:
            seguimiento.validar_agregar(params)
        except ValueError as e:
            return {"content": [{"type": "text",
                    "text": f"No puedo proponer el seguimiento: {e}"}]}
        collector.add(agregar_seguimiento_artifact({**params, "cliente": args.get("cliente")}))
        quien = args.get("cliente") or args.get("rut_cliente")
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — seguimiento de {quien}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar. "
                        "NO afirmes que ya quedó en la lista."}]}

    @tool("proponer_marcar_seguimiento",
          "Propone marcar un seguimiento como 'contactado' o 'descartado' por su id, "
          "para que el usuario confirme. NO escribe: publica una tarjeta. Usa "
          "listar_seguimiento primero para ubicar el id.",
          {"id": int, "estado": str})
    async def proponer_marcar_seguimiento(args):
        s = _obtener_seguimiento(args.get("id"))
        if not s:
            return {"content": [{"type": "text",
                    "text": f"No encontré un seguimiento con id {args.get('id')}. "
                            "Usa listar_seguimiento para ver los ids."}]}
        estado = (args.get("estado") or "").strip().lower()
        if estado not in ("contactado", "descartado"):
            return {"content": [{"type": "text",
                    "text": "El estado debe ser 'contactado' o 'descartado'."}]}
        collector.add(marcar_seguimiento_artifact(s, estado))
        quien = s.get("razon_social") or s["rut_cliente"]
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — marcar {quien} como {estado}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar. "
                        "NO afirmes que ya se marcó."}]}

    @tool("proponer_marcar_factura_pagada",
          "Propone marcar una FACTURA DE VENTA como pagada por su folio, con la "
          "fecha de pago (opcional, por defecto hoy, formato YYYY-MM-DD). NO "
          "escribe: publica una tarjeta de confirmación. Ubica primero el folio "
          "con deuda_cliente o facturas_vencidas.",
          {"folio": int, "fecha": str})
    async def proponer_marcar_factura_pagada(args):
        try:
            limpio = cobranza.validar_marcar_pagada(
                {"folio": args.get("folio"), "fecha": args.get("fecha")})
        except ValueError as e:
            return {"content": [{"type": "text",
                    "text": f"No puedo proponer el pago: {e}"}]}
        f = _obtener_factura(limpio["folio"])
        if not f:
            return {"content": [{"type": "text",
                    "text": f"No encontré una factura con folio {limpio['folio']}. "
                            "Usa deuda_cliente para ver los folios pendientes."}]}
        if f["fecha_pago"] is not None:
            return {"content": [{"type": "text",
                    "text": f"La factura {f['folio']} de {f['razon_social']} ya está "
                            f"pagada desde el {f['fecha_pago']}. No propongo nada."}]}
        collector.add(marcar_factura_pagada_artifact(f, limpio["fecha_pago"]))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — Marcar pagada la factura "
                        f"{f['folio']} de {f['razon_social']} ({_pesos(f['total'])}) "
                        f"con fecha {_fecha_dmy(limpio['fecha_pago'])}. Quedó como "
                        "tarjeta; el usuario debe apretar Confirmar. NO afirmes que "
                        "ya quedó pagada."}]}

    @tool("proponer_corregir_fecha_pago",
          "Propone CORREGIR la fecha de pago de una factura de venta YA marcada "
          "como pagada (fecha mal registrada). Requiere folio y la fecha correcta "
          "(YYYY-MM-DD, obligatoria). NO escribe: publica una tarjeta de "
          "confirmación que muestra la fecha anterior y la nueva.",
          {"folio": int, "fecha": str})
    async def proponer_corregir_fecha_pago(args):
        try:
            limpio = cobranza.validar_corregir_fecha_pago(
                {"folio": args.get("folio"), "fecha": args.get("fecha")})
        except ValueError as e:
            return {"content": [{"type": "text",
                    "text": f"No puedo proponer la corrección: {e}"}]}
        f = _obtener_factura(limpio["folio"])
        if not f:
            return {"content": [{"type": "text",
                    "text": f"No encontré una factura con folio {limpio['folio']}."}]}
        if f["fecha_pago"] is None:
            return {"content": [{"type": "text",
                    "text": f"La factura {f['folio']} no está marcada como pagada; no hay "
                            "fecha que corregir. Usa proponer_marcar_factura_pagada."}]}
        if str(f["fecha_pago"]) == limpio["fecha_pago"]:
            return {"content": [{"type": "text",
                    "text": f"La factura {f['folio']} ya tiene fecha de pago "
                            f"{f['fecha_pago']}. No hay nada que corregir."}]}
        collector.add(corregir_fecha_pago_artifact(f, limpio["fecha_pago"]))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — Corregir la fecha de pago de la "
                        f"factura {f['folio']} de {f['razon_social']}: "
                        f"{_fecha_dmy(f['fecha_pago'])} → {_fecha_dmy(limpio['fecha_pago'])}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar. NO afirmes "
                        "que ya se corrigió."}]}

    def _resolver_cliente(texto, estado_requerido, ya_esta):
        """Resuelve un nombre o RUT a UN cliente. Devuelve (cliente, None) o
        (None, mensaje_de_error). Un nombre ambiguo NO propone nada: castigar al
        cliente equivocado se descubre tarde y a mano."""
        texto = (texto or "").strip()
        if not texto:
            return None, "Necesito el nombre o el RUT del cliente."
        encontrados = _buscar_clientes(texto)
        if not encontrados:
            return None, f"No encontré ningún cliente que calce con {texto!r}."
        if len(encontrados) > 1:
            nombres = ", ".join(f"{c['razon_social']} ({c['rut_cliente']})"
                                for c in encontrados[:6])
            return None, (f"{texto!r} calza con {len(encontrados)} clientes: {nombres}. "
                          "Pregúntale al usuario cuál es y vuelve a intentar con el RUT.")
        c = encontrados[0]
        if c["estado"] != estado_requerido:
            return None, ya_esta.format(cliente=c["razon_social"])
        return c, None

    @tool("proponer_marcar_cliente_incobrable",
          "Propone castigar la deuda de un cliente que quebró o cerró: lo marca "
          "como INCOBRABLE por nombre o RUT. Su deuda sale del por cobrar pero "
          "las facturas siguen impagas. USA ESTO, NUNCA proponer_marcar_factura_pagada, "
          "cuando una deuda no se va a cobrar. NO escribe: publica una tarjeta.",
          {"cliente": str})
    async def proponer_marcar_cliente_incobrable(args):
        c, error = _resolver_cliente(
            args.get("cliente"), "activo",
            "El cliente {cliente} ya está marcado como incobrable. No propongo nada.")
        if error:
            return {"content": [{"type": "text", "text": error}]}
        collector.add(marcar_incobrable_artifact(c))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — Marcar incobrable a "
                        f"{c['razon_social']} ({_pesos(c['deuda'])} en "
                        f"{c['n_facturas']} factura(s)). Quedó como tarjeta; el "
                        "usuario debe apretar Confirmar. NO afirmes que ya quedó "
                        "castigado. Recuérdale que el efecto tributario (nota de "
                        "crédito o castigo) lo debe ver con su contador."}]}

    @tool("proponer_reactivar_cliente",
          "Propone DESHACER el castigo de un cliente incobrable: vuelve a activo "
          "y su deuda regresa al por cobrar. NO escribe: publica una tarjeta.",
          {"cliente": str})
    async def proponer_reactivar_cliente(args):
        c, error = _resolver_cliente(
            args.get("cliente"), "incobrable",
            "El cliente {cliente} está activo, no incobrable. No hay nada que deshacer.")
        if error:
            return {"content": [{"type": "text", "text": error}]}
        collector.add(reactivar_cliente_artifact(c))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — Reactivar a {c['razon_social']}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar."}]}

    server = create_sdk_mcp_server(name="acciones", version="1.0.0", tools=[
        proponer_gasto, proponer_borrar_gasto, proponer_marcar_gasto_pagado, proponer_editar_gasto,
        proponer_agregar_seguimiento, proponer_marcar_seguimiento,
        proponer_marcar_factura_pagada, proponer_corregir_fecha_pago,
        proponer_marcar_cliente_incobrable, proponer_reactivar_cliente,
    ])
    tool_names = [
        "mcp__acciones__proponer_gasto",
        "mcp__acciones__proponer_borrar_gasto",
        "mcp__acciones__proponer_marcar_gasto_pagado",
        "mcp__acciones__proponer_editar_gasto",
        "mcp__acciones__proponer_agregar_seguimiento",
        "mcp__acciones__proponer_marcar_seguimiento",
        "mcp__acciones__proponer_marcar_factura_pagada",
        "mcp__acciones__proponer_corregir_fecha_pago",
        "mcp__acciones__proponer_marcar_cliente_incobrable",
        "mcp__acciones__proponer_reactivar_cliente",
    ]
    return server, tool_names
