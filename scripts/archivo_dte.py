#!/usr/bin/env python3
"""
archivo_dte.py — Zigurat ERP
Archiva el XML del SII del que salió cada documento, con su hash SHA-256.

Por qué existe
--------------
Los XML se venían borrando después de procesarlos. El resultado es que hoy
sobreviven 2 archivos de 876 documentos (1,8%): el histórico no se puede volver
a parsear ni auditar, y cualquier reconstrucción es una estimación.

El archivado tiene que ser automático. El hábito ya demostró cuál es su
resultado cuando depende de acordarse.

El archivo se guarda tal cual llegó, byte por byte. El SII emite en ISO-8859-1;
reescribirlo en UTF-8 cambiaría el hash y rompería la firma electrónica que
lleva adentro.
"""
import hashlib
from pathlib import Path

# Carpeta de archivo. Los tests la reemplazan por una temporal (ver conftest).
DIRECTORIO = Path(__file__).parent.parent / "dte-archivo"

# El SII emite siempre en ISO-8859-1.
ENCODING_SII = "latin-1"


def _a_bytes(contenido):
    """Normaliza a bytes sin reescribir el encoding del SII."""
    if isinstance(contenido, bytes):
        return contenido
    return contenido.encode(ENCODING_SII, errors="replace")


def archivar_contenido(contenido, nombre, directorio=None):
    """Guarda el XML y devuelve {"nombre", "hash_sha256", "ruta"}.

    Si ya existe un archivo con ese nombre:
    - mismo hash  → es el mismo archivo, se reutiliza (reprocesar no duplica);
    - hash distinto → se guarda aparte con el hash en el nombre, porque el SII
      repite nombres de descarga y sobrescribir sería perder evidencia.
    """
    carpeta = Path(directorio) if directorio is not None else DIRECTORIO
    carpeta.mkdir(parents=True, exist_ok=True)

    datos = _a_bytes(contenido)
    hash_sha256 = hashlib.sha256(datos).hexdigest()

    destino = carpeta / Path(nombre).name
    if destino.exists():
        if hashlib.sha256(destino.read_bytes()).hexdigest() != hash_sha256:
            destino = destino.with_name(
                f"{destino.stem}__{hash_sha256[:8]}{destino.suffix}"
            )

    if not destino.exists():
        destino.write_bytes(datos)

    return {
        "nombre":      Path(nombre).name,
        "hash_sha256": hash_sha256,
        "ruta":        str(destino),
    }


def archivar(ruta_xml, directorio=None):
    """Archiva un XML desde el disco. Devuelve None si no se pudo leer.

    Nunca levanta: el archivado es un respaldo, no la operación principal. Si
    falla, la factura igual tiene que poder entrar a la base — perder el
    respaldo es malo, perder la venta es peor.
    """
    ruta = Path(ruta_xml)
    try:
        datos = ruta.read_bytes()
    except OSError:
        return None

    try:
        return archivar_contenido(datos, ruta.name, directorio)
    except OSError:
        return None
