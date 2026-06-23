# tests/test_charts_builder.py
import plotly.graph_objects as go
import pytest
from app.charts.builder import build_figure


def test_build_bar_con_titulo():
    fig = build_figure({"chart_type": "bar", "titulo": "Ventas", "x": ["a", "b"], "y": [1, 2]})
    assert isinstance(fig, go.Figure)
    assert fig.layout.title.text == "Ventas"


def test_build_line():
    fig = build_figure({"chart_type": "line", "x": [1, 2], "y": [3, 4]})
    assert isinstance(fig, go.Figure)


def test_build_pie():
    fig = build_figure({"chart_type": "pie", "x": ["a", "b"], "y": [10, 5]})
    assert isinstance(fig, go.Figure)


def test_chart_type_invalido():
    with pytest.raises(ValueError):
        build_figure({"chart_type": "donut", "x": [], "y": []})
