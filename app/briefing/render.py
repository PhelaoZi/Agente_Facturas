"""Renderiza el dict del brief a Markdown. Función pura, sin BD."""
from datetime import date


def _pesos(n):
    """Formato peso chileno: $1.234.567."""
    if n is None:
        return "$0"
    signo = "-" if n < 0 else ""
    return f"{signo}${abs(int(round(n))):,}".replace(",", ".")


def render_markdown(brief, hoy=None):
    """Convierte el dict del brief en un documento Markdown legible."""
    hoy = hoy or date.today()
    L = [f"# Brief diario Zigurat — {hoy.strftime('%d/%m/%Y')}", ""]

    cob = brief["cobranza"]
    b = cob["buckets"]
    L += [
        "## Cobranza",
        f"- **Deuda total pendiente:** {_pesos(cob['total'])} en {cob['n_facturas']} facturas",
        (f"- Al día: {_pesos(b['al_dia'])} · 1–30 d: {_pesos(b['d1_30'])} · "
         f"31–60 d: {_pesos(b['d31_60'])} · +60 d: {_pesos(b['d60_mas'])}"),
        "",
    ]

    L.append("## Top deudores")
    if brief["top_deudores"]:
        L += ["| Cliente | Deuda | Facturas |", "|---|---:|---:|"]
        for d in brief["top_deudores"]:
            L.append(f"| {d['cliente']} | {_pesos(d['deuda'])} | {d['n']} |")
    else:
        L.append("Sin deuda pendiente. 🎉")
    L.append("")

    L.append(f"## Facturas vencidas (+{brief['umbral_vencidas']} días)")
    if brief["vencidas"]:
        L += ["| Folio | Cliente | Total | Días |", "|---|---|---:|---:|"]
        for f in brief["vencidas"]:
            L.append(f"| {f['folio']} | {f['cliente']} | {_pesos(f['total'])} | {f['dias']} |")
    else:
        L.append("Ninguna factura vencida sobre el umbral. 👍")
    L.append("")

    cr = brief["cobrado_reciente"]
    vp = brief["ventas_periodo"]
    L += [
        f"## Cobrado últimos {brief['umbral_reciente']} días",
        f"- {cr['n']} facturas · {_pesos(cr['total'])}",
        "",
        f"## Ventas últimos {brief['umbral_reciente']} días",
        f"- {vp['n']} facturas · {_pesos(vp['total'])}",
        "",
    ]

    L.append(f"## Clientes inactivos (+{brief['umbral_inactivos']} días)")
    if brief["inactivos"]:
        L += ["| Cliente | Última venta | Días |", "|---|---|---:|"]
        for c in brief["inactivos"]:
            L.append(f"| {c['cliente']} | {c['ultima_venta']} | {c['dias']} |")
    else:
        L.append("Ningún cliente inactivo sobre el umbral. 👍")
    L.append("")

    return "\n".join(L)
