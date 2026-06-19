"""Convierte una especificación de datos en una figura Plotly."""
import plotly.graph_objects as go

CHART_TYPES = {"bar", "line", "pie"}

# Paleta Zigurat Brewery
ZIGURAT_COLORS = [
    "#f97316",  # naranja principal
    "#fb923c",  # naranja medio
    "#fdba74",  # naranja claro
    "#c2410c",  # naranja oscuro
    "#ea580c",  # naranja intenso
    "#3b82f6",  # azul acento
    "#22c55e",  # verde acento
    "#a855f7",  # violeta acento
]

ZIGURAT_LAYOUT = dict(
    colorway=ZIGURAT_COLORS,
    font=dict(family="system-ui, -apple-system, sans-serif", size=12, color="#18181b"),
    paper_bgcolor="white",
    plot_bgcolor="#f9fafb",
    title_font=dict(size=13, color="#18181b"),
    margin=dict(l=20, r=20, t=44, b=20),
    legend=dict(font=dict(size=11)),
    xaxis=dict(gridcolor="#e4e4e7", linecolor="#e4e4e7"),
    yaxis=dict(gridcolor="#e4e4e7", linecolor="#e4e4e7"),
)


def build_figure(spec: dict) -> go.Figure:
    chart_type = spec.get("chart_type")
    if chart_type not in CHART_TYPES:
        raise ValueError(f"chart_type inválido: {chart_type}")

    titulo = spec.get("titulo", "")
    x = spec.get("x", [])
    y = spec.get("y", [])

    if chart_type == "bar":
        fig = go.Figure(go.Bar(
            x=x, y=y,
            marker_color=ZIGURAT_COLORS[0],
            marker_line_width=0,
        ))
    elif chart_type == "line":
        fig = go.Figure(go.Scatter(
            x=x, y=y,
            mode="lines+markers",
            line=dict(color=ZIGURAT_COLORS[0], width=2),
            marker=dict(color=ZIGURAT_COLORS[0], size=6),
        ))
    else:  # pie
        fig = go.Figure(go.Pie(
            labels=x, values=y,
            marker=dict(colors=ZIGURAT_COLORS),
            textfont=dict(size=11),
            hole=0.3,  # donut ligero, más moderno que pie sólido
        ))

    fig.update_layout(title=titulo, **ZIGURAT_LAYOUT)
    return fig
