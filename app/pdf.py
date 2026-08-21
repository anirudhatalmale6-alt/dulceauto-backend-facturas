"""
Generacion del PDF y snapshot historico.

Un PDF no se genera desde el HTML que hay ahora mismo en disco: se genera desde
una **copia congelada** de ese momento. La copia guarda el documento y los
archivos que usa (hoja de estilo, tipografias e imagenes) en su propia carpeta,
de modo que dentro de dos anos la factura RES-87241 siga imprimiendose
exactamente igual aunque se haya cambiado el logo, la cuenta bancaria o la
plantilla.

Esa fue una condicion del cliente y es la razon de que aqui se copien archivos
en lugar de apuntar a los de la aplicacion.

Una sola pagina, siempre
------------------------
El CSS aprobado imprime la factura escalada, con dos variables:
--print-scale y --print-height. En el Milestone 1 esos dos numeros se
calibraron a mano para el texto de la maqueta.

Aqui se recalculan **para cada factura**, midiendo la altura real del documento
ya cargado en Chromium. Es lo unico que garantiza una sola pagina cuando los
datos cambian: un titulo de vehiculo mas largo, un texto de entrega escrito a
mano o un nombre de cliente que ocupa dos lineas cambian la altura, y una
escala fija dejaria media factura en una segunda hoja.

Uno cada vez
------------
La generacion pasa por un cerrojo. Cada Chromium ocupa varios cientos de megas
mientras imprime; diez peticiones a la vez levantarian diez Chromium y se
llevarian por delante la memoria del servidor. Con el cerrojo, diez peticiones
se convierten en diez PDF seguidos, que es mas lento pero no se cae.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import documents
from .config import settings
from .models import Invoice, InvoiceSnapshot, utcnow

# Medidas de la hoja. Coinciden con @page en el CSS aprobado.
DESIGN_WIDTH = 900          # px, ancho para el que esta hecho el diseno
MARGIN_MM = 8
SAFETY = 0.99               # 1% de holgura para no rozar el borde del papel
PX_PER_MM = 96 / 25.4

# Un Chromium cada vez. Ver la nota de arriba.
_CERROJO = threading.Lock()


class PdfError(RuntimeError):
    """La generacion no salio adelante. Se ensena tal cual al operador."""


@dataclass
class Resultado:
    snapshot: InvoiceSnapshot
    pdf: Path
    paginas: int
    escala: float
    altura_px: float


def _archivos_referenciados(html: str) -> set[str]:
    """Rutas de assets que el documento usa de verdad.

    Se copian solo esas y no la carpeta entera: los iconos sueltos, por
    ejemplo, no se usan porque el sprite va incrustado en el propio HTML.
    """
    return {m.group(1) for m in re.finditer(r'(?:src|href)="assets/([^"]+)"', html)}


def _copiar_assets(html: str, destino: Path) -> int:
    """Copia a la carpeta del snapshot los archivos que el documento usa.

    Devuelve cuantos se copiaron. La hoja de estilo se lee ademas por dentro,
    porque referencia las tipografias con rutas relativas a si misma y esas no
    aparecen en el HTML.
    """
    copiados = 0
    pendientes = list(_archivos_referenciados(html))
    vistos: set[str] = set()

    while pendientes:
        rel = pendientes.pop()
        if rel in vistos:
            continue
        vistos.add(rel)
        origen = documents.TEMPLATES_DIR / "assets" / rel
        if not origen.exists():
            continue
        final = destino / rel
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origen, final)
        copiados += 1

        if origen.suffix == ".css":
            # url(../fonts/x.woff2) dentro del CSS: relativo al propio archivo.
            base = Path(rel).parent
            for m in re.finditer(r"url\(['\"]?([^'\")]+)['\"]?\)", origen.read_text(encoding="utf-8")):
                ruta = m.group(1)
                if ruta.startswith(("http", "data:")):
                    continue
                # normpath y no un replace de "..": la hoja esta en assets/css
                # y pide url("../fonts/x.woff2"), que es assets/fonts/x.woff2.
                # Quitando el ".." a mano saldria assets/css/fonts/x.woff2, la
                # tipografia no se copiaria y el PDF se imprimiria con otra
                # letra sin avisar de nada.
                pendientes.append(os.path.normpath((base / ruta).as_posix()))

    return copiados


def _escribir_codigos(invoice: Invoice, destino: Path) -> None:
    """Escribe el QR y el codigo de barras de ESTA factura sobre las copias que
    se acaban de traer de la plantilla.

    Se conservan los nombres de archivo del diseno aprobado a proposito: asi el
    documento del snapshot no necesita que se le cambie ninguna ruta y la
    carpeta se sigue abriendo sola en cualquier navegador.
    """
    from . import codes

    qr = destino / "img/reservation-qr.svg"
    barras = destino / "img/reservation-barcode.svg"
    if qr.parent.exists():
        qr.write_text(codes.qr_svg(_url_verificacion(invoice)), encoding="utf-8")
        barras.write_text(codes.barcode_svg(invoice.folio or ""), encoding="utf-8")


def _copiar_logo(db: Session, destino: Path) -> str | None:
    """Trae el logotipo de Configuracion a la carpeta del snapshot.

    Devuelve la ruta relativa que tiene que llevar el documento, o None si no
    hay logotipo propio: en ese caso se conserva la marca del diseno aprobado.
    """
    import shutil as _shutil

    from . import uploads
    from .models import Setting

    fila = db.execute(
        select(Setting).where(Setting.key == "brand.logo_path", Setting.market.is_(None))
    ).scalar_one_or_none()
    origen = uploads.ruta_absoluta(fila.value if fila else None)
    if origen is None:
        return None
    (destino / "img").mkdir(parents=True, exist_ok=True)
    final = destino / "img" / f"logo{origen.suffix.lower()}"
    _shutil.copy2(origen, final)
    return f"assets/img/{final.name}"


def _congelar_fotos(invoice: Invoice, destino: Path) -> list[int]:
    """Mete las fotografias subidas dentro del snapshot.

    Se escriben ENCIMA de las copias que acaban de traerse de la plantilla, con
    su mismo nombre de archivo. De ese modo el documento congelado no necesita
    que se le cambie ninguna ruta y la carpeta se abre sola en cualquier
    navegador, que es la razon de ser del snapshot.

    Las que el operador no haya subido se quedan con la del diseno aprobado.
    """
    from PIL import Image

    from . import documents, uploads

    puestas = []
    for foto in getattr(invoice, "photos", []):
        campo = f"foto_{foto.position}"
        nombre = documents.ARCHIVO_FOTO.get(campo)
        origen = uploads.ruta_absoluta(foto.file_path)
        if not nombre or origen is None:
            continue
        final = destino / "img" / nombre
        if not final.parent.exists():
            continue
        # Se guarda siempre como JPEG con el nombre que espera la plantilla:
        # el operador puede subir un PNG y el archivo se llama .jpg.
        with Image.open(origen) as imagen:
            imagen.convert("RGB").save(final, "JPEG", quality=92, optimize=True)
        puestas.append(foto.position)
    return puestas


def _url_verificacion(invoice: Invoice) -> str:
    base = (invoice.verify_url_base or "").strip()
    if not base:
        return ""
    return base.rstrip("/") + "/" + (invoice.folio or "")


def _siguiente_version(db: Session, invoice_id: int) -> int:
    actual = db.execute(
        select(func.max(InvoiceSnapshot.version)).where(InvoiceSnapshot.invoice_id == invoice_id)
    ).scalar()
    return (actual or 0) + 1


# Resolucion a la que se dejan las fotografias dentro del PDF. 300 puntos por
# pulgada es lo que se considera calidad de imprenta; por encima solo se gana
# peso.
DPI_IMPRESION = 300
CSS_PPI = 96
LADO_MINIMO = 400           # nunca se baja de aqui, por si algo se mide mal


def _ajustar_imagenes(page, carpeta: Path, escala: float) -> list[str]:
    """Deja cada fotografia a la resolucion que de verdad necesita el papel.

    Chromium no recomprime las imagenes al imprimir: las incrusta tal cual. Una
    foto de 1280 px que en la hoja ocupa 45 mm entra en el PDF con casi 2 MB de
    mapa de bits. Con las cuatro del vehiculo, la factura pasaba de 6 MB, que es
    demasiado para un documento que se le manda por correo a un cliente.

    Se mide cuanto ocupa cada imagen en pantalla, se traduce a milimetros de la
    hoja (aplicando la escala de impresion) y se reduce la copia del snapshot a
    los pixeles que hacen falta para 300 ppp. La copia del snapshot es la que se
    imprime, asi que el original de la plantilla no se toca.
    """
    from PIL import Image

    medidas = page.evaluate(
        "Array.from(document.querySelectorAll('img[src^=\"assets/\"]')).map("
        " i => ({ src: i.getAttribute('src'), w: i.getBoundingClientRect().width }))"
    )
    # Una misma imagen puede salir dos veces con tamanos distintos: manda la
    # mas grande, o la mas pequena saldria pixelada.
    ancho_por_archivo: dict[str, float] = {}
    for m in medidas:
        rel = m["src"][len("assets/"):]
        ancho_por_archivo[rel] = max(ancho_por_archivo.get(rel, 0), m["w"])

    tocadas = []
    for rel, ancho_css in ancho_por_archivo.items():
        ruta = carpeta / rel
        if not ruta.exists() or ruta.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        objetivo = max(LADO_MINIMO, int(ancho_css * escala * DPI_IMPRESION / CSS_PPI))
        with Image.open(ruta) as img:
            if img.width <= objetivo:
                continue
            alto = round(img.height * objetivo / img.width)
            reducida = img.convert("RGB").resize((objetivo, alto), Image.LANCZOS)
            reducida.save(ruta, "JPEG", quality=88, optimize=True, progressive=True)
        tocadas.append(rel)
    return tocadas


def _calibrar(page) -> tuple[float, int, float]:
    """Escala y altura de impresion para el documento que hay cargado.

    Se mide la caja real de .invoice y se busca la escala mas grande que quepa
    en la hoja util, tanto de ancho como de alto.
    """
    altura = page.evaluate("document.querySelector('.invoice').getBoundingClientRect().height")
    ancho_util = (210 - 2 * MARGIN_MM) * PX_PER_MM
    alto_util = (297 - 2 * MARGIN_MM) * PX_PER_MM
    escala = min(ancho_util / DESIGN_WIDTH, alto_util / altura) * SAFETY
    return escala, math.ceil(altura * escala), altura


def generar(db: Session, invoice: Invoice) -> Resultado:
    """Congela la factura, imprime su PDF y deja anotado el snapshot.

    Todo ocurre dentro del cerrojo, incluido el reparto del numero de version.
    Antes solo se protegia la parte de Chromium, y dos operadores que pulsaran
    a la vez calculaban los dos la misma version, escribian en la misma carpeta
    y uno borraba los archivos del otro a media copia. Se descubrio midiendo:
    de tres peticiones simultaneas, dos devolvian error 500.
    """
    from playwright.sync_api import sync_playwright

    with _CERROJO:
        return _generar_bajo_cerrojo(db, invoice, sync_playwright)


def _generar_bajo_cerrojo(db: Session, invoice: Invoice, sync_playwright) -> Resultado:
    version = _siguiente_version(db, invoice.id)
    carpeta = settings.snapshots_dir / str(invoice.id) / f"v{version}"
    if carpeta.exists():
        shutil.rmtree(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)

    # El documento del snapshot apunta a sus propios archivos, con rutas
    # relativas: la carpeta se puede mover, copiar o descargar entera y sigue
    # abriendose bien.
    # El logotipo se copia dentro del snapshot con un nombre propio: cambiarlo
    # despues en Configuracion no toca ningun PDF ya emitido.
    logo = _copiar_logo(db, carpeta / "assets")
    documento = documents.render(invoice, assets="assets/", logo=logo)
    html_path = carpeta / "documento.html"
    html_path.write_text(documento.html, encoding="utf-8")
    copiados = _copiar_assets(documento.html, carpeta / "assets")
    _escribir_codigos(invoice, carpeta / "assets")
    _congelar_fotos(invoice, carpeta / "assets")

    pdf_path = carpeta / f"{invoice.folio}.pdf"

    try:
        with sync_playwright() as p:
            navegador = p.chromium.launch(args=["--no-sandbox"])
            pagina = navegador.new_page(viewport={"width": DESIGN_WIDTH + 40, "height": 1400})
            pagina.goto(html_path.resolve().as_uri())
            # Las tipografias se cargan aparte del HTML. Medir antes de que
            # esten listas da una altura equivocada y el PDF sale con una
            # escala que no es la que toca.
            pagina.wait_for_load_state("networkidle")
            pagina.evaluate("document.fonts ? document.fonts.ready : null")
            escala, altura_impresion, altura = _calibrar(pagina)

            # Las fotografias se reducen a la resolucion que pide el papel
            # y se recarga la pagina para que Chromium imprima las copias
            # ya reducidas. Sin recargar seguiria usando las que tiene en
            # memoria y el PDF pesaria igual.
            if _ajustar_imagenes(pagina, carpeta / "assets", escala):
                pagina.reload()
                pagina.wait_for_load_state("networkidle")
                escala, altura_impresion, altura = _calibrar(pagina)

            pagina.evaluate(
                "([e, h]) => {"
                " document.documentElement.style.setProperty('--print-scale', e);"
                " document.documentElement.style.setProperty('--print-height', h + 'px'); }",
                [f"{escala:.4f}", altura_impresion],
            )
            pagina.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
                margin={
                    "top": f"{MARGIN_MM}mm",
                    "bottom": f"{MARGIN_MM}mm",
                    "left": f"{MARGIN_MM}mm",
                    "right": f"{MARGIN_MM}mm",
                },
            )
            navegador.close()
    except Exception as exc:  # noqa: BLE001 - se le ensena al operador
        shutil.rmtree(carpeta, ignore_errors=True)
        raise PdfError(f"No se ha podido generar el PDF: {exc}") from exc

    paginas = contar_paginas(pdf_path)
    if paginas != 1:
        # El documento aprobado es de una sola pagina. Si salen dos, algo ha
        # cambiado de alto y hay que saberlo ahora, no cuando el cliente lo
        # reciba.
        shutil.rmtree(carpeta, ignore_errors=True)
        raise PdfError(
            f"El PDF ha salido con {paginas} páginas y la factura es de una sola. "
            "Revise si algún texto se ha alargado mucho."
        )

    snapshot = InvoiceSnapshot(
        invoice_id=invoice.id,
        version=version,
        folio=invoice.folio,
        locale=invoice.locale,
        pdf_path=str(pdf_path.relative_to(settings.data_dir)),
        html_path=str(html_path.relative_to(settings.data_dir)),
        assets_dir=str((carpeta / "assets").relative_to(settings.data_dir)),
    )
    db.add(snapshot)
    invoice.pdf_generated_at = utcnow()
    # Se confirma aqui dentro, todavia con el cerrojo puesto: si se dejara para
    # la ruta, la siguiente peticion calcularia su version antes de que esta se
    # hubiera guardado y las dos pedirian el mismo numero.
    db.commit()

    return Resultado(
        snapshot=snapshot,
        pdf=pdf_path,
        paginas=paginas,
        escala=escala,
        altura_px=altura,
    )


def contar_paginas(pdf: Path) -> int:
    """Paginas de un PDF, sin librerias.

    Se cuentan los objetos /Type /Page del archivo. Es suficiente para lo unico
    que hay que comprobar aqui, que es que la factura no se haya partido en dos.
    """
    datos = pdf.read_bytes()
    return len(re.findall(rb"/Type\s*/Page[^s]", datos))


def snapshots_de(db: Session, invoice_id: int) -> list[InvoiceSnapshot]:
    return list(
        db.execute(
            select(InvoiceSnapshot)
            .where(InvoiceSnapshot.invoice_id == invoice_id)
            .order_by(InvoiceSnapshot.version.desc())
        ).scalars()
    )


def ruta_absoluta(relativa: str | None) -> Path | None:
    if not relativa:
        return None
    ruta = settings.data_dir / relativa
    return ruta if ruta.exists() else None
