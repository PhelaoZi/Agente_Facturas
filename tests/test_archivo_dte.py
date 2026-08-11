# tests/test_archivo_dte.py
"""Archivado del XML de origen.

Christian venía borrando los XML después de procesarlos, y por eso el histórico
ya no se puede auditar: quedan 2 archivos de 876 documentos. El archivado tiene
que ser automático, porque el hábito ya demostró cuál es su resultado.

El XML se guarda tal cual llegó, byte por byte: el SII lo emite en ISO-8859-1 y
reescribirlo en UTF-8 cambiaría el hash y rompería la firma electrónica.
"""
import hashlib

import pytest

from scripts import archivo_dte


XML = "<?xml version='1.0' encoding='ISO-8859-1'?><EnvioDTE>ñandú</EnvioDTE>"


def test_guarda_el_xml_y_devuelve_su_hash(tmp_path):
    info = archivo_dte.archivar_contenido(XML, "DTE_2026-08-02.xml", tmp_path)

    guardado = tmp_path / "DTE_2026-08-02.xml"
    assert guardado.exists()
    assert info["hash_sha256"] == hashlib.sha256(XML.encode("latin-1")).hexdigest()
    assert info["nombre"] == "DTE_2026-08-02.xml"


def test_guarda_los_bytes_exactos_del_sii(tmp_path):
    """En ISO-8859-1, no en UTF-8: reescribir el encoding cambia el hash y
    rompe la firma del documento."""
    archivo_dte.archivar_contenido(XML, "DTE.xml", tmp_path)

    assert (tmp_path / "DTE.xml").read_bytes() == XML.encode("latin-1")


def test_reprocesar_el_mismo_archivo_no_lo_duplica(tmp_path):
    """Mismo nombre y mismo contenido: es el mismo archivo, no una versión
    nueva."""
    primero = archivo_dte.archivar_contenido(XML, "DTE.xml", tmp_path)
    segundo = archivo_dte.archivar_contenido(XML, "DTE.xml", tmp_path)

    assert primero["ruta"] == segundo["ruta"]
    assert len(list(tmp_path.iterdir())) == 1


def test_dos_xml_distintos_con_el_mismo_nombre_conviven(tmp_path):
    """El SII repite nombres de descarga. Sobrescribir seria perder evidencia,
    que es justo lo que este paso viene a evitar."""
    archivo_dte.archivar_contenido(XML, "DTE.xml", tmp_path)
    otro = archivo_dte.archivar_contenido("<EnvioDTE>otro</EnvioDTE>", "DTE.xml", tmp_path)

    assert len(list(tmp_path.iterdir())) == 2
    assert otro["hash_sha256"][:8] in otro["ruta"]


def test_acepta_bytes_ademas_de_texto(tmp_path):
    """Quien lee del disco entrega bytes; el dashboard entrega texto."""
    info = archivo_dte.archivar_contenido(XML.encode("latin-1"), "DTE.xml", tmp_path)

    assert info["hash_sha256"] == hashlib.sha256(XML.encode("latin-1")).hexdigest()


def test_archivar_desde_una_ruta(tmp_path):
    origen = tmp_path / "origen.xml"
    origen.write_bytes(XML.encode("latin-1"))
    destino = tmp_path / "archivo"

    info = archivo_dte.archivar(origen, destino)

    assert (destino / "origen.xml").read_bytes() == XML.encode("latin-1")
    assert info["nombre"] == "origen.xml"


def test_un_xml_ilegible_no_revienta_el_pipeline(tmp_path):
    """El archivado es un respaldo, no la operación principal: si falla, la
    factura igual tiene que entrar a la base."""
    assert archivo_dte.archivar(tmp_path / "no-existe.xml", tmp_path) is None
