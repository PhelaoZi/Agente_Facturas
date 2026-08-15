# tests/test_prompt_dinero_por_producto.py
"""El detalle de facturas no sirve para responder dinero por producto.

Medido el 2026-08-10: ante "los 5 mejores clientes de Cream Ale 30L", el agente
sumo la tabla `productos` y respondio $3.575.105 cuando el ingreso real de esa
cerveza es $10.867.242 (33% de lo real). La causa es de datos: `parse_dte.py`
descarta las lineas llamadas "Logistica" a secas, y la logistica es cerca de la
mitad del precio del barril.

Mientras la reparacion de datos no este hecha, la unica proteccion es que
NINGUNO de los dos agentes -el del PC y el del telefono- entregue un monto por
producto sacado del detalle. Unidades si; dinero no.

Ninguno de los dos lee CLAUDE.md: si la regla no esta en su prompt, no existe
para ellos.
"""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PROMPT_NUBE = RAIZ / "functions" / "_shared" / "chat_prompt.ts"


def _una_linea(texto: str) -> str:
    """Junta el texto en una sola linea.

    Los prompts van envueltos a ~78 columnas y el punto de corte se mueve cada
    vez que se reescribe un parrafo. Sin esto, el test fijaria el ancho de linea
    en vez de la regla, y fallaria por un reflujo cosmetico.

    El prompt de la nube es un template literal de TypeScript, donde el salto se
    escribe `\\` + fin de linea. Ese backslash tambien es ancho de linea, no
    regla: hay que sacarlo ANTES de colapsar los espacios, o el test vuelve a
    fijar donde cae el corte.
    """
    return re.sub(r"\s+", " ", texto.replace("\\\n", ""))


# Sin el sujeto ("productos sirve" / "v_ventas_producto y productos sirven"):
# el test fija la REGLA, no la conjugacion del verbo. Mismo criterio que
# _una_linea con el ancho de linea.
REGLA_UNIDADES = "para UNIDADES, nunca para dinero"


def test_el_prompt_local_prohibe_dinero_por_producto_desde_el_detalle():
    from app.agent.system_prompt import SYSTEM_PROMPT

    prompt = _una_linea(SYSTEM_PROMPT)
    assert REGLA_UNIDADES in prompt
    assert "NO entregues montos por producto" in prompt


def test_el_prompt_de_la_nube_prohibe_dinero_por_producto_desde_el_detalle():
    """El chat del telefono tiene el mismo defecto y su propio prompt: arreglar
    solo el del PC deja el agujero abierto en el canal que mas se usa."""
    texto = _una_linea(PROMPT_NUBE.read_text(encoding="utf-8"))

    assert REGLA_UNIDADES in texto
    assert "NO entregues montos por producto" in texto


def test_el_prompt_de_la_nube_ya_no_manda_v_ventas_producto_sin_reservas():
    """La instruccion anterior era 'Por producto: usa la view v_ventas_producto
    (ya excluye Logistica y PET)'. Es correcta para contar unidades y es
    exactamente la que produjo la cifra deflactada al pedir pesos."""
    texto = _una_linea(PROMPT_NUBE.read_text(encoding="utf-8"))

    assert "usa la view v_ventas_producto (ya excluye Logistica y PET)" not in texto


def test_el_prompt_de_la_nube_manda_a_la_tool_de_ingreso():
    """Prohibir sin dar el reemplazo deja al telefono sin poder responder algo
    que el PC ya contesta bien. Desde que la atribucion se replica (2026-08-12)
    hay una fuente correcta y el prompt tiene que nombrarla."""
    texto = _una_linea(PROMPT_NUBE.read_text(encoding="utf-8"))

    assert "ingreso_producto" in texto
    assert "v_ingreso_producto" in texto
    # Ya no corresponde declararse incapaz: eso era el parche de mientras.
    assert "no tienes la cifra confiable" not in texto


def test_el_prompt_de_la_nube_obliga_a_declarar_la_cobertura():
    """La logistica repartida a prorrata no se puede verificar contra el
    documento. El PC ya obliga a decirlo; el telefono es el canal mas usado."""
    texto = _una_linea(PROMPT_NUBE.read_text(encoding="utf-8"))

    assert "cobertura" in texto.lower()
    assert "estimad" in texto.lower()
