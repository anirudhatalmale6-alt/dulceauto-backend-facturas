"""
Codigo QR y codigo de barras de cada factura.

Los dos se dibujan como SVG, no como imagen: en un PDF el SVG se imprime nitido
a cualquier tamano, mientras que un PNG de 200 px puesto en un A4 sale con los
bordes emborronados y un lector de codigos se atraganta.

Se genera con la misma forma que los archivos aprobados en el Milestone 1
(mismo viewBox, mismos colores, mismo <title>), de manera que ocupan
exactamente el hueco que ya tenian en el diseno.

  - QR      : el enlace de verificacion de esa factura.
  - Barras  : Code 128-B del folio, que es lo que se acordo en el Milestone 1.

Para el Code 128 se usa python-barcode y no una tabla escrita a mano. El
alfabeto, el digito de control y los patrones de arranque y parada son faciles
de escribir mal, y un codigo mal formado se ve perfecto y no lo lee ningun
lector: el fallo aparece en el mostrador, no aqui.
"""
from __future__ import annotations

import html
from pathlib import Path

# Colores de los archivos aprobados.
COLOR_QR = "#0b1f3a"
COLOR_BARRAS = "#111"

# Los dos modos de QR que se pactaron: el normal, que lo dibuja el servidor a
# partir del folio, y el manual, para cuando hay que poner un QR que viene de
# fuera (una pasarela de pago, una campana, otro sistema de verificacion).
MODO_DINAMICO = "dynamic"
MODO_FIJO = "fixed"

# El codigo de barras aprobado mide 134 x 78 en su viewBox.
ALTO_BARRAS = 78


def qr_svg(url: str) -> str:
    """QR del enlace de verificacion, con el aspecto del archivo aprobado."""
    import segno

    if not url:
        return _svg_vacio(33, 33, COLOR_QR)

    # Correccion de errores media: aguanta que el papel se ensucie o se doble
    # sin que el codigo deje de leerse, y no crece tanto como para que los
    # modulos queden diminutos en el hueco del diseno.
    codigo = segno.make(url, error="m")
    matriz = [list(fila) for fila in codigo.matrix]
    lado = len(matriz) + 4  # 2 modulos de margen a cada lado, como el aprobado

    piezas = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {lado} {lado}" '
        'shape-rendering="crispEdges" role="img">',
        f"<title>{html.escape(url)}</title>",
        f'<rect width="{lado}" height="{lado}" fill="#fff"/>',
        f'<g fill="{COLOR_QR}">',
    ]
    # Se juntan los modulos contiguos de cada fila en un solo rectangulo. El
    # archivo queda bastante mas pequeno y se dibuja igual.
    for y, fila in enumerate(matriz):
        x = 0
        while x < len(fila):
            if not fila[x]:
                x += 1
                continue
            ancho = 1
            while x + ancho < len(fila) and fila[x + ancho]:
                ancho += 1
            piezas.append(f'<rect x="{x + 2}" y="{y + 2}" width="{ancho}" height="1"/>')
            x += ancho
    piezas.append("</g></svg>")
    return "".join(piezas)


def barcode_svg(texto: str) -> str:
    """Code 128-B del folio, con el aspecto del archivo aprobado."""
    from barcode import Code128

    if not texto:
        return _svg_vacio(134, ALTO_BARRAS, COLOR_BARRAS)

    # build() devuelve la secuencia de modulos como cadena de unos y ceros.
    modulos = Code128(texto).build()[0]
    ancho = len(modulos)

    piezas = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {ancho} {ALTO_BARRAS}" '
        'preserveAspectRatio="none" shape-rendering="crispEdges" role="img">',
        f"<title>{html.escape(texto)}</title>",
        f'<rect width="{ancho}" height="{ALTO_BARRAS}" fill="#fff"/>',
        f'<g fill="{COLOR_BARRAS}">',
    ]
    x = 0
    while x < ancho:
        if modulos[x] == "0":
            x += 1
            continue
        barra = 1
        while x + barra < ancho and modulos[x + barra] == "1":
            barra += 1
        piezas.append(f'<rect x="{x}" y="0" width="{barra}" height="{ALTO_BARRAS}"/>')
        x += barra
    piezas.append("</g></svg>")
    return "".join(piezas)


def _svg_vacio(ancho: int, alto: int, color: str) -> str:
    """Un recuadro vacio. Se usa cuando no hay nada que codificar: es preferible
    un hueco en blanco a un codigo que lleve a ninguna parte."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {ancho} {alto}" role="img">'
        f'<rect width="{ancho}" height="{alto}" fill="#fff"/></svg>'
    )


# --- QR fijo, subido a mano ---------------------------------------------------
#
# El modo normal es el dinamico: el servidor dibuja el QR con el enlace de
# verificacion de cada factura, asi que nunca hay que acordarse de nada. El modo
# fijo existe para los casos en que el QR viene de otro sitio -una pasarela de
# pago, una campana, un sistema de verificacion propio- y tiene que salir tal
# cual en el documento.
#
# La eleccion vive en Configuracion, detras de la Master Password, y el archivo
# que se este usando se copia dentro de cada snapshot: cambiarlo despues no
# altera ninguna factura ya emitida.


def ajuste(db, clave: str) -> str:
    from sqlalchemy import select

    from .models import Setting

    fila = db.execute(
        select(Setting).where(Setting.key == clave, Setting.market.is_(None))
    ).scalar_one_or_none()
    return ((fila.value if fila else "") or "").strip()


def qr_fijo(db) -> Path | None:
    """El archivo de QR que hay puesto a mano, o None si se dibuja por folio.

    Devuelve None tambien cuando el modo es fijo pero el archivo ya no esta en
    el disco. Es deliberado: preferimos volver al QR dinamico, que siempre
    funciona, antes que imprimir una factura con el hueco del QR roto. La
    pantalla de Configuracion avisa de esa situacion.
    """
    from . import uploads

    if ajuste(db, "qr.mode") != MODO_FIJO:
        return None
    return uploads.ruta_absoluta(ajuste(db, "qr.image_path"))
