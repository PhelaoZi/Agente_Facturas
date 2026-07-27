"""Importación de XMLs DTE del SII desde el dashboard.

Reemplaza el trabajo manual de copiar el XML a su carpeta y correr la skill de
consola: el usuario suelta los archivos en el dashboard y este módulo los
clasifica, procesa y archiva.

**No duplica el pipeline.** Reutiliza las funciones puras de `scripts/`:
`parse_dte` (parseo), `validate_changes` (validación) y `sync_db` /
`sync_compras` (escritura). Así las reglas de negocio —notas de crédito,
montos ajustados, omisión de duplicados, mapeo de insumos— viven en un solo
lugar y no pueden divergir entre la consola y la web.

**Invariante:** validar e insertar ocurren dentro de `importar_venta`, en ese
orden y sin camino alternativo. Si la validación arroja errores, la función
retorna sin haber tocado la BD. Es el equivalente en proceso del flag
`.changes_validated` que protege a la CLI.

Siguiendo el contrato de `app/negocio/`, las funciones que escriben reciben un
cursor y no manejan conexión ni transacción: eso es del endpoint.
"""
import re
from pathlib import Path

from app.config import PROJECT_ROOT
from scripts import parse_dte, sync_compras, sync_db, validate_changes

# RUT del emisor propio: si el XML lo trae como emisor, es una venta nuestra.
RUT_EMISOR_PROPIO = "76308012-9"   # Elaboradora y Comercializadora Vintage SPA

TIPO_NOTA_CREDITO = "61"

# Carpeta destino por clase de documento (convención del proyecto).
CARPETAS = {
    "venta":        PROJECT_ROOT / "facturas-ventas",
    "nota_credito": PROJECT_ROOT / "Notas de Credito",
    "compra":       PROJECT_ROOT / "facturas-compras",
}

# Caracteres aceptados en el nombre de archivo que se escribe en disco.
CARACTERES_NOMBRE = re.compile(r"[^A-Za-z0-9 ._()\-]")
LARGO_MAX_NOMBRE = 150
MAX_SUFIJOS_NOMBRE = 50   # tope de reintentos "archivo (N).xml"


# ─── Nombre de archivo seguro ────────────────────────────────────────────────

def nombre_seguro(nombre):
    """Valida el nombre que llega del navegador antes de escribirlo en disco.

    El nombre viene del cliente: es la única barrera contra escribir fuera de
    las carpetas del pipeline. Los separadores de ruta y '..' se rechazan de
    plano (un <input type="file"> nunca los manda); el resto de caracteres
    raros se reemplaza por '_' para no molestar por cosmética.
    """
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("Nombre de archivo vacío")
    if any(sep in nombre for sep in ("/", "\\", ":")) or ".." in nombre:
        raise ValueError(f"Nombre de archivo no permitido: {nombre!r}")
    if not nombre.lower().endswith(".xml"):
        raise ValueError(f"Solo se aceptan archivos .xml (llegó {nombre!r})")

    limpio = CARACTERES_NOMBRE.sub("_", nombre)
    if len(limpio) > LARGO_MAX_NOMBRE:
        limpio = limpio[:LARGO_MAX_NOMBRE - 4] + ".xml"
    if limpio.startswith("."):
        raise ValueError(f"Nombre de archivo no permitido: {nombre!r}")
    return limpio


# ─── Clasificación por RUT emisor ────────────────────────────────────────────

def clasificar(contenido):
    """Decide si el XML es venta, nota de crédito o compra.

    Mira el emisor de cada <Documento> (no la carátula del envío). Lo que
    discrimina es **si Zigurat es el emisor**, no cuántos emisores hay: la
    descarga masiva de documentos *recibidos* del SII empaqueta en un mismo
    archivo las facturas de todos los proveedores del período, así que "varios
    emisores distintos" es la forma normal —y única— de una compra.

    - ningún documento con emisor propio          → compra (1 o N proveedores)
    - todos con emisor propio, todos tipo 61      → nota_credito
    - todos con emisor propio, cualquier otro tipo → venta
    - mezcla de emisor propio con terceros        → desconocido (ventas y
      compras van a pipelines distintos; hay que separar el archivo)
    - sin documentos o sin emisor                 → desconocido

    Retorna {"clase", "rut_emisor", "ruts_emisores", "tipos", "motivo"}, donde
    `rut_emisor` es el emisor solo cuando hay uno; con varios proveedores es
    None y la lista completa va en `ruts_emisores`.
    """
    def resultado(clase, ruts, tipos, motivo=None):
        return {"clase": clase, "tipos": tipos, "motivo": motivo,
                "ruts_emisores": ruts,
                "rut_emisor": ruts[0] if len(ruts) == 1 else None}

    bloques = re.findall(r"<Documento ID=.*?</Documento>", contenido, re.DOTALL)
    if not bloques:
        return resultado("desconocido", [], [],
                         "El archivo no contiene documentos DTE (<Documento>).")

    ruts, tipos = [], []
    for bloque in bloques:
        rut = re.search(r"<RUTEmisor>(.*?)</RUTEmisor>", bloque)
        if rut and rut.group(1).strip() not in ruts:
            ruts.append(rut.group(1).strip())
        tipo = re.search(r"<TipoDTE>(.*?)</TipoDTE>", bloque)
        if tipo:
            tipos.append(tipo.group(1).strip())

    if not ruts:
        return resultado("desconocido", ruts, tipos,
                         "Los documentos no traen RUT del emisor.")

    ajenos = [r for r in ruts if r != RUT_EMISOR_PROPIO]
    if ajenos and len(ajenos) < len(ruts):
        # Único caso realmente ambiguo: ventas propias y compras en un mismo
        # archivo, que van a pipelines distintos.
        return resultado("desconocido", ruts, tipos,
                         f"El archivo mezcla documentos emitidos por Zigurat con documentos "
                         f"de terceros ({', '.join(ajenos)}); las ventas y las compras se "
                         f"procesan aparte, sepáralos en archivos distintos.")
    if ajenos:
        return resultado("compra", ruts, tipos)

    clase = "nota_credito" if all(t == TIPO_NOTA_CREDITO for t in tipos) else "venta"
    return resultado(clase, ruts, tipos)


# ─── Resultado por archivo ───────────────────────────────────────────────────

def _resultado_base(nombre, clase):
    """Forma uniforme que consume el frontend (un dict por archivo)."""
    return {
        "archivo": nombre, "clase": clase, "estado": "ok",
        "facturas": 0, "notas_credito": 0, "productos": 0,
        "duplicados": [], "precios_actualizados": [], "gastos_insertados": 0,
        "sin_mapeo": [], "precios_mas_nuevos": [], "ruts": [],
        "errores": [], "advertencias": [],
        "guardado_en": None, "procesado_completo": True, "ya_procesada": False,
    }


# ─── Ventas y notas de crédito ───────────────────────────────────────────────

def importar_venta(cur, contenido, nombre, clase="venta"):
    """Parsea, valida e inserta un XML de ventas o notas de crédito.

    Si la validación falla, retorna estado "error" SIN haber escrito nada.
    Los folios que ya existen en la BD se omiten y se reportan en "duplicados".
    """
    res = _resultado_base(nombre, clase)
    try:
        documentos = parse_dte.parsear_contenido(contenido)
    except ValueError as e:
        res["estado"] = "error"
        res["errores"].append(str(e))
        return res

    changes = parse_dte.armar_changes(documentos, nombre)

    errores, advertencias = validate_changes.validar_changes(changes)
    res["advertencias"] = advertencias
    if errores:
        # Puerta del pipeline: sin validación limpia no se escribe nada.
        res["estado"] = "error"
        res["errores"] = errores
        return res

    sync = sync_db.sincronizar_en_cursor(cur, changes["documentos"])
    res["duplicados"] = sync["duplicados"]
    res["productos"] = sync["productos"]
    res["ruts"] = sync["ruts"]

    # Separar facturas de notas de crédito entre lo efectivamente insertado
    omitidos = {(d["folio"], d["tipo"]) for d in sync["duplicados"]}
    for doc in documentos:
        venta = doc["venta"]
        if (venta["folio"], str(venta["tipo_documento"])) in omitidos:
            continue
        if str(venta["tipo_documento"]) == TIPO_NOTA_CREDITO:
            res["notas_credito"] += 1
        else:
            res["facturas"] += 1

    if not sync["insertados"]:
        res["estado"] = "omitido"
    return res


# ─── Compras a proveedores ───────────────────────────────────────────────────

def compra_ya_procesada(nombre):
    """True si el XML ya figura en facturas-compras/.procesados.json."""
    return nombre in sync_compras._load_procesados()


def importar_compra(cur, raw, nombre):
    """Procesa un XML de proveedor: actualiza precios de insumos o registra gastos.

    Se omite si el archivo ya está en `.procesados.json`. Esa lista es la única
    protección contra reprocesar una compra: los gastos son idempotentes por
    (folio, rut_emisor), pero los precios de insumos se sobrescriben con un
    UPDATE simple, así que volver a cargar una factura antigua dejaría el
    precio del insumo en un valor viejo.

    Un proveedor sin clasificar no es un error del archivo: se reporta como
    "omitido" pidiendo agregar el RUT, y el archivo NO se marca como procesado
    para poder reprocesarlo después (mismo criterio que sync_compras.py).
    """
    res = _resultado_base(nombre, "compra")
    if compra_ya_procesada(nombre):
        res["estado"] = "omitido"
        res["procesado_completo"] = False   # ya está en la lista: no re-anotar
        res["ya_procesada"] = True
        res["advertencias"].append(
            "Esta compra ya la habías procesado antes — no la volví a cargar.")
        return res

    try:
        dtes = sync_compras.parse_contenido(raw, nombre)
    except Exception as e:
        res["estado"] = "error"
        res["errores"].append(f"No se pudo leer el XML: {e}")
        return res

    sin_clasificar = []
    # De más antiguo a más nuevo: el precio que queda es el de la factura
    # más reciente, no el del documento que venía último en el archivo.
    for dte in sorted(dtes, key=lambda d: d["fecha"] or ""):
        rut = dte["rut_emisor"]
        if rut in sync_compras.PROVEEDORES_GASTOS:
            _, categoria = sync_compras.PROVEEDORES_GASTOS[rut]
            if sync_compras.procesar_gasto(dte, categoria, cur):
                res["gastos_insertados"] += 1
            else:
                res["advertencias"].append(
                    f"Folio {dte['folio']} de {dte['razon_social']}: ya estaba registrado.")
        elif rut in sync_compras.PROVEEDORES_INSUMOS:
            actualizados, no_mapeados, omitidos = sync_compras.procesar_insumos(dte, cur)
            res["precios_actualizados"] += actualizados
            res["sin_mapeo"] += no_mapeados
            res["precios_mas_nuevos"] += omitidos
            # Lo que no es insumo de receta no se bota: queda como gasto.
            if sync_compras.procesar_lineas_no_insumo(dte, no_mapeados, cur):
                res["gastos_insertados"] += 1
        else:
            sin_clasificar.append(f"{dte['razon_social']} ({rut})")

    if sin_clasificar:
        res["estado"] = "omitido"
        for proveedor in sin_clasificar:
            res["errores"].append(
                f"Proveedor sin clasificar: {proveedor}. Agrégalo a PROVEEDORES_INSUMOS "
                f"o PROVEEDORES_GASTOS en scripts/sync_compras.py y vuelve a importarlo.")
    elif not res["gastos_insertados"] and not res["precios_actualizados"]:
        res["estado"] = "omitido"

    res["procesado_completo"] = not sin_clasificar
    return res


def marcar_compra_procesada(nombre):
    """Anota el XML en facturas-compras/.procesados.json (idempotencia del script)."""
    procesados = sync_compras._load_procesados()
    if nombre not in procesados:
        procesados.add(nombre)
        sync_compras._save_procesados(procesados)


# ─── Archivado del XML ───────────────────────────────────────────────────────

def guardar_xml(raw, nombre, clase):
    """Copia el XML a su carpeta del pipeline y devuelve la ruta final.

    Si ya hay un archivo con ese nombre y el mismo contenido, no reescribe.
    Si el contenido difiere, guarda como "nombre (2).xml" para no pisar nada.
    """
    carpeta = CARPETAS.get(clase)
    if carpeta is None:
        raise ValueError(f"Clase de documento desconocida: {clase!r}")
    carpeta.mkdir(parents=True, exist_ok=True)

    destino = carpeta / nombre
    if not destino.exists():
        destino.write_bytes(raw)
        return destino

    if destino.read_bytes() == raw:
        return destino   # ya archivado, nada que hacer

    base, ext = destino.stem, destino.suffix
    for i in range(2, MAX_SUFIJOS_NOMBRE):
        candidato = carpeta / f"{base} ({i}){ext}"
        if not candidato.exists():
            candidato.write_bytes(raw)
            return candidato
        if candidato.read_bytes() == raw:
            return candidato
    raise ValueError(f"Demasiados archivos con el nombre {nombre!r} en {carpeta.name}/")


# ─── Decodificación del XML del SII ──────────────────────────────────────────

def decodificar(raw):
    """Los DTE del SII vienen en ISO-8859-1 (latin-1), que nunca falla al decodificar."""
    return raw.decode("latin-1")


def fecha_mas_reciente(contenido):
    """Última fecha de emisión del archivo, para ordenar la carga cronológicamente.

    `procesar_insumos` sobrescribe el precio con un UPDATE sin condición: manda
    el último documento procesado. Como la descarga masiva del SII numera los
    archivos al revés (el "(7)" es el más antiguo), procesarlos en el orden que
    llegan dejaría los insumos con precios viejos. Retorna "" si no hay fechas
    (esos archivos van primero y de todos modos fallan la clasificación).
    """
    fechas = re.findall(r"<FchEmis>(\d{4}-\d{2}-\d{2})</FchEmis>", contenido)
    return max(fechas) if fechas else ""


def ruta_relativa(path):
    """Ruta legible para mostrar en la UI (relativa a la raíz del proyecto)."""
    try:
        return str(Path(path).relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)
