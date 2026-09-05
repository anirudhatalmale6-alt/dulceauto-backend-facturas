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

from . import doctypes, documents
from .config import settings
from .models import Invoice, InvoiceSnapshot, utcnow

# Medidas de la hoja. Coinciden con @page en el CSS aprobado.
DESIGN_WIDTH = 900          # px, ancho para el que esta hecho el diseno
MARGIN_MM = 8
SAFETY = 0.99               # 1% de holgura para no rozar el borde del papel
PX_PER_MM = 96 / 25.4
# Grosor del marco exterior que .page-shell dibuja al imprimir (seccion 11 del
# CSS). Va en content-box, o sea que se suma por fuera del ancho y del alto
# calibrados. Se descuenta de la hoja util para que el calculo siga siendo
# exacto y no dependa de que sobre sitio por el SAFETY.
MARCO_PX = 1

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


def _escribir_codigos(invoice: Invoice, destino: Path, qr_manual: Path | None = None) -> None:
    """Escribe el QR y el codigo de barras de ESTA factura sobre las copias que
    se acaban de traer de la plantilla.

    Se conservan los nombres de archivo del diseno aprobado a proposito: asi el
    documento del snapshot no necesita que se le cambie ninguna ruta y la
    carpeta se sigue abriendo sola en cualquier navegador.

    Con un QR subido a mano cambia solo la extension -reservation-qr.png en vez
    de .svg- y el documento ya viene apuntando ahi desde render(). El archivo se
    copia dentro del snapshot: cambiar despues el QR en Configuracion no toca
    ninguna factura ya emitida.
    """
    import shutil as _shutil

    from . import codes

    carpeta = destino / "img"
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "reservation-barcode.svg").write_text(
        codes.barcode_svg(invoice.folio or ""), encoding="utf-8"
    )
    if qr_manual is not None:
        _shutil.copy2(qr_manual, carpeta / f"reservation-qr{qr_manual.suffix.lower()}")
    else:
        (carpeta / "reservation-qr.svg").write_text(
            codes.qr_svg(_url_verificacion(invoice)), encoding="utf-8"
        )


def _congelar_archivo(relativa: str | None, destino: Path, nombre: str) -> str | None:
    """Copia un archivo de marca dentro de la carpeta del snapshot.

    Es lo que hace que cambiar manana un logotipo o un icono no toque ningun
    documento ya emitido: el snapshot deja de mirar al archivo de trabajo y pasa
    a tener el suyo propio.
    """
    import shutil as _shutil

    from . import uploads

    origen = uploads.ruta_absoluta(relativa)
    if origen is None:
        return None
    (destino / "img").mkdir(parents=True, exist_ok=True)
    final = destino / "img" / f"{nombre}{origen.suffix.lower()}"
    _shutil.copy2(origen, final)
    return f"assets/img/{final.name}"


def _marca_de(db: Session, invoice: Invoice) -> tuple[str | None, str | None, str, str | None]:
    """Logotipo, icono, nombre y titulo con los que se emite esta factura.

    El perfil de marca manda. Si la factura no tiene perfil asignado -- porque
    es anterior a esta version -- se cae al logotipo global de Configuracion,
    que es exactamente lo que se venia usando. Asi ninguna factura antigua
    cambia de aspecto al regenerar su PDF.
    """
    from .models import BrandProfile, Setting

    perfil = db.get(BrandProfile, invoice.brand_profile_id) if invoice.brand_profile_id else None
    if perfil is not None:
        # El nombre y el titulo se leen de la factura, no del perfil: ahi estan
        # congelados desde que se creo. El perfil solo aporta los archivos.
        return (
            perfil.logo_path,
            perfil.safe_icon_path,
            invoice.brand_name or perfil.name,
            invoice.brand_doc_title or perfil.doc_title,
        )

    fila = db.execute(
        select(Setting).where(Setting.key == "brand.logo_path", Setting.market.is_(None))
    ).scalar_one_or_none()
    return (fila.value if fila else None), None, (invoice.brand_name or "DulceAuto"), None


# --- transparencia -----------------------------------------------------------
#
# Un logotipo suele llegar en PNG con el fondo transparente, y ahi hay dos
# trampas que no dan ningun error y estropean el documento impreso:
#
#   1. convert("RGB") sobre una imagen con transparencia NO pone fondo blanco.
#      Descarta el canal alfa y deja a la vista los colores que habia debajo,
#      que en la mayoria de los PNG son negros: el logotipo aparece dentro de
#      un rectangulo negro.
#   2. Guardar en JPEG un archivo que se llama .png "funciona" -el navegador
#      mira el contenido, no la extension- pero JPEG no admite transparencia,
#      asi que un logotipo recortado se imprime igualmente sobre un recuadro.
#
# Paso de verdad, y solo en el PDF: el documento HTML y la vista previa sirven
# el archivo tal cual lo subio el operador, y unicamente el PDF pasa por la
# reduccion de resolucion de mas abajo. De ahi que se viera bien en pantalla y
# quemado en el papel.

# Formato de salida segun la extension, para que el archivo sea de verdad lo
# que su nombre dice.
FORMATO_POR_EXTENSION = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
}


def tiene_transparencia(imagen) -> bool:
    return imagen.mode in ("RGBA", "LA", "PA") or (
        imagen.mode == "P" and "transparency" in imagen.info
    )


def sobre_blanco(imagen):
    """La imagen aplanada sobre blanco, que es el color del papel."""
    from PIL import Image

    if not tiene_transparencia(imagen):
        return imagen.convert("RGB")
    con_alfa = imagen.convert("RGBA")
    fondo = Image.new("RGB", con_alfa.size, (255, 255, 255))
    fondo.paste(con_alfa, mask=con_alfa.getchannel("A"))
    return fondo


def _guardar_imagen(imagen, ruta: Path) -> None:
    """Escribe la imagen en el formato que anuncia su extension."""
    formato = FORMATO_POR_EXTENSION[ruta.suffix.lower()]
    if formato == "JPEG":
        sobre_blanco(imagen).save(ruta, "JPEG", quality=88, optimize=True, progressive=True)
    elif formato == "PNG":
        # Se conserva el canal alfa: es justo lo que hace que un logotipo
        # recortado se apoye sobre el fondo del documento en vez de traer su
        # propio recuadro.
        imagen.save(ruta, "PNG", optimize=True)
    else:
        imagen.save(ruta, "WEBP", quality=90, method=6)


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
    fotos = sorted(getattr(invoice, "photos", []), key=lambda f: f.position)
    for orden, foto in enumerate(fotos, start=1):
        origen = uploads.ruta_absoluta(foto.file_path)
        if origen is None:
            continue

        # Cada fotografia se copia con DOS nombres, porque el documento la pide
        # de dos sitios distintos:
        #
        #   · los cuatro nombres del diseno (vehicle-front.jpg y compania), que
        #     son los que usa la pagina 1;
        #   · album/foto-NN.jpg, que es el nombre que usa el album de la
        #     pagina 2 y que decide documents.archivo_album().
        #
        # Sin la segunda copia el PDF sale con la pagina 2 llena de imagenes
        # rotas, y ademas no se nota hasta abrirlo: el HTML es correcto, lo que
        # falta es el archivo.
        nombres = []
        de_diseno = documents.ARCHIVO_FOTO.get(f"foto_{foto.position}")
        if de_diseno:
            nombres.append(de_diseno)
        nombres.append(documents.archivo_album(orden))

        # Se abre y se aplana una sola vez aunque se guarde dos veces.
        with Image.open(origen) as imagen:
            plana = sobre_blanco(imagen)
            for nombre in nombres:
                final = destino / "img" / nombre
                final.parent.mkdir(parents=True, exist_ok=True)
                # Se guarda siempre como JPEG con el nombre que espera la
                # plantilla: el operador puede subir un PNG y el archivo se
                # llama .jpg. Como JPEG no tiene transparencia, se aplana sobre
                # blanco -nunca con un convert("RGB") a secas, que la dejaria
                # negra. Ver sobre_blanco().
                plana.save(final, "JPEG", quality=92, optimize=True)
        puestas.append(foto.position)
    return puestas


def _url_verificacion(invoice: Invoice) -> str:
    base = (invoice.verify_url_base or "").strip()
    if not base:
        return ""
    return base.rstrip("/") + "/" + (invoice.folio or "")


def _siguiente_version(db: Session, invoice_id: int, doc: str = doctypes.FACTURA) -> int:
    """Siguiente version DE ESE DOCUMENTO.

    El tipo entra en el filtro a proposito. Sin el, generar el "Pago de apartado
    confirmado" de una reserva haria que la siguiente pre-factura de la misma
    reserva fuese la v3, y el historial los mezclaria: pareceria que un
    documento sustituye al otro.
    """
    actual = db.execute(
        select(func.max(InvoiceSnapshot.version)).where(
            InvoiceSnapshot.invoice_id == invoice_id,
            InvoiceSnapshot.doc_type == doc,
        )
    ).scalar()
    return (actual or 0) + 1


# Resolucion a la que se dejan las fotografias dentro del PDF. 300 puntos por
# pulgada es lo que se considera calidad de imprenta; por encima solo se gana
# peso.
DPI_IMPRESION = 300
CSS_PPI = 96
LADO_MINIMO = 400           # nunca se baja de aqui, por si algo se mide mal

# Un QR subido a mano se reduce con mucha mas holgura que una fotografia. Un
# codigo es geometria, no una imagen: al encogerlo, los bordes de sus cuadros
# se mezclan con el blanco de al lado y el lector deja de distinguirlos. El
# archivo sigue siendo pequeno -un QR de 1000 px en PNG no llega a 30 KB-, asi
# que no hay nada que ganar apurando.
LADO_MINIMO_QR = 1000
NOMBRE_QR = "reservation-qr"


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

    # La FORMA del hueco se mide aparte, y con dos cuidados que no son manias:
    #
    #   · con la hoja en modo IMPRESION, porque hay huecos que cambian de forma
    #     al imprimir -la foto principal del vehiculo es uno- y lo que se va a
    #     imprimir es esto;
    #
    #   · leyendo el width/height CALCULADO y no el rectangulo en pantalla. El
    #     rectangulo viene ya multiplicado por el transform:scale de impresion,
    #     y el recorte de object-fit no se calcula sobre eso: se calcula sobre
    #     la caja de maquetacion, que el transform no toca. Son centesimas de
    #     diferencia, y con centesimas Chromium ya recorta.
    page.emulate_media(media="print")
    try:
        # Y se descuentan bordes y relleno. object-fit reparte la CAJA DE
        # CONTENIDO, no la caja con su borde: las fotografias de la pagina 1
        # llevan un borde de 1px y son 2px por lado que no le tocan a la imagen.
        # Sobre 216px son centesimas, y con centesimas Chromium ya recorta.
        formas = page.evaluate(
            "Array.from(document.querySelectorAll('img[src^=\"assets/\"]')).map("
            " i => { const c = getComputedStyle(i);"
            "        const n = v => parseFloat(v) || 0;"
            "        const dentro = c.boxSizing === 'border-box';"
            "        const hor = dentro ? n(c.borderLeftWidth) + n(c.borderRightWidth)"
            "                           + n(c.paddingLeft) + n(c.paddingRight) : 0;"
            "        const ver = dentro ? n(c.borderTopWidth) + n(c.borderBottomWidth)"
            "                           + n(c.paddingTop) + n(c.paddingBottom) : 0;"
            "        return { src: i.getAttribute('src'), w: n(c.width) - hor,"
            "                 h: n(c.height) - ver, ajuste: c.objectFit }; })"
        )
    finally:
        # "null" quita la emulacion y deja la pagina como estaba. Poner "screen"
        # NO es lo mismo: eso la fija en pantalla, y page.pdf() imprimiria con
        # los estilos de pantalla. Se probo, y el PDF salio con cuatro hojas.
        page.emulate_media(media="null")
    # Una misma imagen puede salir dos veces con tamanos distintos: manda la
    # mas grande, o la mas pequena saldria pixelada.
    ancho_por_archivo: dict[str, float] = {}
    for m in medidas:
        rel = m["src"][len("assets/"):]
        if m["w"] > ancho_por_archivo.get(rel, 0):
            ancho_por_archivo[rel] = m["w"]

    forma_por_archivo: dict[str, float | None] = {}
    for m in formas:
        rel = m["src"][len("assets/"):]
        forma = m["w"] / m["h"] if m["ajuste"] == "cover" and m["h"] else None
        if rel in forma_por_archivo:
            # Dos huecos con formas distintas para el mismo archivo: recortarlo
            # a una dejaria la otra deformada. Se renuncia al recorte.
            anterior = forma_por_archivo[rel]
            if anterior is None or forma is None or abs(anterior - forma) > 0.01:
                forma = None
        forma_por_archivo[rel] = forma

    tocadas = []
    for rel, ancho_css in ancho_por_archivo.items():
        ruta = carpeta / rel
        if not ruta.exists() or ruta.suffix.lower() not in FORMATO_POR_EXTENSION:
            continue
        suelo = LADO_MINIMO_QR if Path(rel).stem == NOMBRE_QR else LADO_MINIMO
        objetivo = max(suelo, int(ancho_css * escala * DPI_IMPRESION / CSS_PPI))
        forma = forma_por_archivo.get(rel)
        with Image.open(ruta) as img:
            recorte = _recorte_a(img, forma)
            if img.width <= objetivo and recorte is None:
                continue
            # El modo de trabajo se elige segun la imagen, no segun el destino:
            # una con transparencia se reduce en RGBA para no perder el alfa por
            # el camino, y es _guardar_imagen quien decide si hay que aplanarla.
            modo = "RGBA" if tiene_transparencia(img) else "RGB"
            trabajada = img.convert(modo)
            if recorte is not None:
                trabajada = trabajada.crop(recorte)
            ancho, alto = _medida_exacta(min(objetivo, trabajada.width), forma, trabajada)
            _guardar_imagen(trabajada.resize((ancho, alto), Image.LANCZOS), ruta)
        tocadas.append(rel)
    return tocadas


def _medida_exacta(ancho: int, forma: float | None, img) -> tuple[int, int]:
    """Los pixeles con los que la imagen queda mas pegada a la forma del hueco.

    Con un ancho fijo no se puede: el alto tiene que ser un numero entero de
    pixeles, y al redondearlo la imagen se queda unas milesimas mas ancha o mas
    estrecha que el hueco. Parece nada. No lo es: a Chromium esas milesimas le
    bastan para decidir que la imagen no encaja, recortarla y, con eso, dejar de
    copiar el JPEG y escribir el mapa de bits entero.

    Asi que no se fija el ancho: se prueban los altos de alrededor y se elige el
    par (ancho, alto) cuyo cociente cae mas cerca de la forma del hueco. Es una
    aproximacion racional a mano, y con margen de sobra: entre cien candidatos
    siempre hay alguno que se queda a menos de una centesima de pixel, mientras
    que redondear a secas se queda a media.

    Se busca solo HACIA ARRIBA. El ancho que llega aqui ya trae aplicado el
    suelo de resolucion del propio motor, asi que quedarse por debajo seria
    entregar la fotografia mas pobre de lo que pide el papel -y hay una
    comprobacion que lo vigila, que es la que lo caso-. Como mucho se sube un
    12%, y nunca por encima de los pixeles que la imagen tiene.
    """
    if not forma:
        alto = max(1, round(img.height * ancho / img.width))
        return ancho, alto

    mejor, mejor_error = (ancho, max(1, round(ancho / forma))), None
    alto_ideal = ancho / forma
    for alto in range(max(1, round(alto_ideal)), int(alto_ideal * 1.12) + 2):
        candidato = max(1, round(alto * forma))
        # Ni por debajo del ancho pedido ni por encima de lo que hay: agrandar
        # una foto no le anade informacion, solo le anade peso.
        if candidato < ancho or candidato > img.width or alto > img.height:
            continue
        error = abs(candidato - alto * forma)
        if mejor_error is None or error < mejor_error:
            mejor, mejor_error = (candidato, alto), error
    return mejor


def _recorte_a(img, forma: float | None) -> tuple[int, int, int, int] | None:
    """El recorte centrado que deja la imagen con la forma del hueco.

    Esto es lo que mas adelgaza el PDF, y el motivo no es obvio.

    Chromium incrusta el JPEG tal cual -sin tocarlo, sin recomprimirlo- SIEMPRE
    QUE no tenga que recortarlo. En cuanto un object-fit:cover le pide un
    recorte, tiene que decodificar, recortar y volver a codificar, y lo que
    escribe entonces es un mapa de bits sin comprimir. Una sola fotografia del
    album pasaba asi de 42 kB a 1,3 MB. No es la resolucion: es el recorte.

    Asi que el recorte se hace aqui, con Pillow, antes de que lo vea el
    navegador. Lo que se ve en el documento no cambia ni un pixel -es
    exactamente el mismo recorte centrado que hacia object-fit:cover-, pero la
    imagen le llega a Chromium ya con la forma del hueco y ya no tiene nada que
    recortar, asi que la copia entera.

    Devuelve None cuando no hay nada que recortar.
    """
    if not forma:
        return None
    if img.width / img.height > forma:
        ancho = min(img.width, max(1, round(img.height * forma)))
        margen = (img.width - ancho) // 2
        caja = (margen, 0, margen + ancho, img.height)
    else:
        alto = min(img.height, max(1, round(img.width / forma)))
        margen = (img.height - alto) // 2
        caja = (0, margen, img.width, margen + alto)
    # Sin margen de tolerancia: si sobra aunque sea una fila de pixeles, se
    # quita. Con una tolerancia del medio por ciento, una foto de 542x310 en un
    # hueco de 216x123,4 se daba por buena, Chromium le recortaba las dos filas
    # que sobraban y el PDF se llevaba 350 kB de mapa de bits por esas dos filas.
    return None if caja == (0, 0, img.width, img.height) else caja


# --- suelo de legibilidad ----------------------------------------------------
#
# El cliente pidio por escrito que si un documento no cabe en A4, el sistema
# avise en lugar de encogerlo hasta que no se lea.
#
# El suelo no es un numero inventado: sale de MEDIR lo que el cliente ya aprobo
# y tiene en produccion. Las tres pre-facturas vivas imprimen su letra mas
# pequena (7 px de diseno) a:
#
#     en     escala 0,8044 -> 5,630 px = 4,223 pt
#     es-MX  escala 0,8044 -> 5,630 px = 4,223 pt
#     es-AR  escala 0,7966 -> 5,576 px = 4,182 pt   <- la peor de las tres
#
# El suelo se pone JUSTO POR DEBAJO de la peor: 5,5 px = 4,125 pt.
#
# Que quede por debajo y no clavado en 5,576 es a proposito. La escala se
# recalcula para cada factura, asi que la letra impresa de la pre-factura se
# mueve unas centesimas segun los datos. Un suelo clavado en el valor medido
# haria fallar a la propia pre-factura la mitad de las veces -- que es
# exactamente lo que paso al escribirlo como 7 x 0,8044 y lo cazaron las
# baterias de las fases anteriores.
#
# Se compara en pixeles CSS y no en puntos para no arrastrar el redondeo de la
# conversion en cada comprobacion.
SUELO_LETRA_PX = 5.5


class PdfIlegible(PdfError):
    """El documento no cabe en A4 sin bajar del suelo de legibilidad."""


class PdfEstadoNoCorresponde(PdfError):
    """Ese documento no se emite con la factura en el estado que tiene.

    Se levanta antes de crear nada: sin version, sin fila en el historico y sin
    carpeta en el disco.
    """


def _fuente_minima(page, raiz: str) -> float:
    """Tamano en px de la letra mas pequena que se pinta dentro del documento.

    Solo cuenta los elementos que tienen texto propio: un contenedor vacio con
    font-size heredado pequeno no se ve, y contarlo dispararia el aviso sin que
    hubiera nada ilegible.
    """
    return page.evaluate(
        """(raiz) => {
            const nodos = [...document.querySelectorAll(raiz + ' *')].filter(e => {
                if (!e.offsetParent && getComputedStyle(e).position !== 'fixed') return false;
                return [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
            });
            const medidas = nodos
                .map(e => parseFloat(getComputedStyle(e).fontSize))
                .filter(v => v > 0 && isFinite(v));
            return medidas.length ? Math.min(...medidas) : 0;
        }""",
        raiz,
    )


def _calibrar(page, raiz: str = ".invoice", ancho_diseno: int = DESIGN_WIDTH) -> tuple[float, int, float]:
    """Escala y altura de impresion para el documento que hay cargado.

    Se mide la caja real del documento y se busca la escala mas grande que quepa
    en la hoja util, tanto de ancho como de alto.

    raiz y ancho_diseno vienen del tipo de documento: la pre-factura envuelve el
    suyo en .invoice y esta calibrada a 900 px, y los dos complementarios usan
    .page a 1038 px. Con los valores de la factura, un complementario saldria
    con las columnas estrechadas.
    """
    altura = page.evaluate("(r) => document.querySelector(r).getBoundingClientRect().height", raiz)
    # El marco de .page-shell se dibuja por fuera del alto y del ancho
    # calibrados, asi que la hoja util para el documento es la de la caja menos
    # ese marco por los dos lados.
    ancho_util = (210 - 2 * MARGIN_MM) * PX_PER_MM - 2 * MARCO_PX
    alto_util = (297 - 2 * MARGIN_MM) * PX_PER_MM - 2 * MARCO_PX
    escala = min(ancho_util / ancho_diseno, alto_util / altura) * SAFETY
    return escala, math.ceil(altura * escala), altura


def _comprobar_legibilidad(page, raiz: str, escala: float) -> None:
    """Levanta PdfIlegible si a esa escala la letra mas pequena baja del suelo.

    Se mide la letra REAL del documento cargado, no la del CSS a ojo: un texto
    puede heredar su tamano de tres sitios distintos y el unico numero que
    importa es el que Chromium va a pintar.
    """
    minima = _fuente_minima(page, raiz)
    if not minima:
        return
    impresa = minima * escala
    if impresa >= SUELO_LETRA_PX:
        return
    falta = SUELO_LETRA_PX / impresa
    raise PdfIlegible(
        f"El documento no cabe en una hoja A4 con un tamaño de letra legible. "
        f"Su texto más pequeño saldría a {impresa * 72 / 96:.2f} pt y el mínimo "
        f"aceptado es {SUELO_LETRA_PX * 72 / 96:.2f} pt, el de la pre-factura. "
        f"Hay que acortar el contenido en torno a un {(falta - 1) * 100:.0f} %. "
        "No se ha generado ningún PDF."
    )


def generar(db: Session, invoice: Invoice, doc: str = doctypes.FACTURA) -> Resultado:
    """Congela el documento, imprime su PDF y deja anotado el snapshot.

    Todo ocurre dentro del cerrojo, incluido el reparto del numero de version.
    Antes solo se protegia la parte de Chromium, y dos operadores que pulsaran
    a la vez calculaban los dos la misma version, escribian en la misma carpeta
    y uno borraba los archivos del otro a media copia. Se descubrio midiendo:
    de tres peticiones simultaneas, dos devolvian error 500.

    doc dice de que documento es la copia. Cada tipo lleva su propia numeracion
    de versiones y su propia carpeta, asi que generar uno no toca a los demas.
    """
    from playwright.sync_api import sync_playwright

    with _CERROJO:
        return _generar_bajo_cerrojo(db, invoice, sync_playwright, doc)


def carpeta_snapshot(invoice_id: int, doc: str, version: int) -> Path:
    """Donde vive la copia congelada.

    La pre-factura conserva EXACTAMENTE la ruta de siempre, sin el tipo por
    medio: hay PDF ya emitidos apuntando ahi y esa ruta esta guardada en la base
    de datos. Los documentos nuevos cuelgan de una subcarpeta con su tipo.
    """
    base = settings.snapshots_dir / str(invoice_id)
    if doc == doctypes.FACTURA:
        return base / f"v{version}"
    return base / doc / f"v{version}"


def _generar_bajo_cerrojo(
    db: Session, invoice: Invoice, sync_playwright, doc: str = doctypes.FACTURA
) -> Resultado:
    tipo = doctypes.tipo(doc)
    if not doctypes.existe_para(tipo.clave, invoice.locale):
        raise PdfError(
            f'El documento "{tipo.nombre}" no existe para el mercado '
            f"{invoice.locale}. En esta fase sólo está preparado para es-MX."
        )

    # La regla de estado se comprueba AQUI, en el motor, y no en la ruta del
    # panel. Es el punto por el que pasa cualquier via de generacion, asi que
    # tocar la URL o el formulario no la esquiva.
    #
    # Y va ANTES de repartir version y de crear la carpeta, a proposito: si se
    # comprobara despues habria que borrar lo ya creado, y un borrado que falle
    # deja una carpeta a medias en el historico.
    permitido, motivo = doctypes.puede_generarse(db, tipo.clave, invoice.status)
    if not permitido:
        raise PdfEstadoNoCorresponde(motivo)

    version = _siguiente_version(db, invoice.id, tipo.clave)
    carpeta = carpeta_snapshot(invoice.id, tipo.clave, version)
    if carpeta.exists():
        shutil.rmtree(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)

    # El documento del snapshot apunta a sus propios archivos, con rutas
    # relativas: la carpeta se puede mover, copiar o descargar entera y sigue
    # abriendose bien.
    # El logotipo se copia dentro del snapshot con un nombre propio: cambiarlo
    # despues en Configuracion no toca ningun PDF ya emitido.
    logo_rel, icono_rel, marca, doc_title = _marca_de(db, invoice)
    logo = _congelar_archivo(logo_rel, carpeta / "assets", "logo")
    safe_icon = _congelar_archivo(icono_rel, carpeta / "assets", "safe-icon")
    # El QR se decide ANTES de componer el documento: si es una imagen subida a
    # mano, el documento tiene que apuntar a ella con su extension de verdad.
    from . import codes

    qr_manual = codes.qr_fijo(db)
    qr_src = f"assets/img/reservation-qr{qr_manual.suffix.lower()}" if qr_manual else None

    documento = documents.render(
        invoice,
        assets="assets/",
        logo=logo,
        qr_src=qr_src,
        marca=marca,
        safe_icon=safe_icon,
        doc_title=doc_title,
        doc=tipo.clave,
    )
    html_path = carpeta / "documento.html"
    html_path.write_text(documento.html, encoding="utf-8")
    copiados = _copiar_assets(documento.html, carpeta / "assets")
    _escribir_codigos(invoice, carpeta / "assets", qr_manual)
    _congelar_fotos(invoice, carpeta / "assets")

    pdf_path = carpeta / f"{invoice.folio}{tipo.sufijo_pdf}.pdf"

    try:
        with sync_playwright() as p:
            navegador = p.chromium.launch(args=["--no-sandbox"])
            pagina = navegador.new_page(viewport={"width": tipo.ancho + 40, "height": 1400})
            pagina.goto(html_path.resolve().as_uri())
            # Las tipografias se cargan aparte del HTML. Medir antes de que
            # esten listas da una altura equivocada y el PDF sale con una
            # escala que no es la que toca.
            pagina.wait_for_load_state("networkidle")
            pagina.evaluate("document.fonts ? document.fonts.ready : null")
            escala, altura_impresion, altura = _calibrar(pagina, tipo.raiz, tipo.ancho)

            # Las fotografias se reducen a la resolucion que pide el papel
            # y se recarga la pagina para que Chromium imprima las copias
            # ya reducidas. Sin recargar seguiria usando las que tiene en
            # memoria y el PDF pesaria igual.
            if _ajustar_imagenes(pagina, carpeta / "assets", escala):
                pagina.reload()
                pagina.wait_for_load_state("networkidle")
                escala, altura_impresion, altura = _calibrar(pagina, tipo.raiz, tipo.ancho)

            # El aviso va ANTES de imprimir. Encoger hasta que no se lea y
            # entregar el PDF igualmente seria lo contrario de lo que el cliente
            # pidio: prefiere un aviso a un documento ilegible.
            #
            # Solo para los documentos que lo tienen activado. Ver la nota de
            # comprueba_legibilidad en doctypes: la pre-factura conserva el
            # comportamiento que ya tenia.
            if tipo.comprueba_legibilidad:
                _comprobar_legibilidad(pagina, tipo.raiz, escala)

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
    except PdfIlegible:
        # El aviso de legibilidad ya explica que pasa y cuanto sobra. Envolverlo
        # en "No se ha podido generar el PDF: ..." lo convertiria en un error
        # tecnico y el operador perderia justo la parte util.
        shutil.rmtree(carpeta, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001 - se le ensena al operador
        shutil.rmtree(carpeta, ignore_errors=True)
        raise PdfError(f"No se ha podido generar el PDF: {exc}") from exc

    paginas = contar_paginas(pdf_path)
    esperadas = documents.paginas(invoice) if tipo.clave == doctypes.FACTURA else 1
    if paginas != esperadas:
        # Cuantas hojas debe tener este documento no es una constante desde que
        # existe el album: la pre-factura son dos hojas cuando hay fotografias y
        # una cuando no. Lo que sigue siendo un error es que salgan MAS de las
        # que toca, porque significa que algo se ha alargado y se ha partido; y
        # que salgan menos, porque significa que algo no ha entrado.
        #
        # Se compara contra el mismo documents.paginas() que escribe el pie de
        # la pagina 2. Si cada uno lo dedujera por su cuenta, podria salir un
        # PDF de una hoja con un pie que dice "Pagina 2 de 2".
        shutil.rmtree(carpeta, ignore_errors=True)
        raise PdfError(
            f"El PDF ha salido con {paginas} página{'' if paginas == 1 else 's'} "
            f"y este documento es de {esperadas}. "
            "Revise si algún texto se ha alargado mucho."
        )

    snapshot = InvoiceSnapshot(
        invoice_id=invoice.id,
        doc_type=tipo.clave,
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


def snapshots_de(
    db: Session, invoice_id: int, doc: str | None = doctypes.FACTURA
) -> list[InvoiceSnapshot]:
    """Historial de una reserva.

    doc=None devuelve los tres historiales juntos; con un tipo, solo el de ese
    documento. El valor por defecto es la pre-factura a proposito: todas las
    pantallas que ya existian piden "el historial" queriendo decir el de la
    pre-factura, y sin ese filtro empezarian a ensenar mezclados unos documentos
    que hasta hoy no existian.
    """
    consulta = select(InvoiceSnapshot).where(InvoiceSnapshot.invoice_id == invoice_id)
    if doc is not None:
        consulta = consulta.where(InvoiceSnapshot.doc_type == doc)
    return list(
        db.execute(
            consulta.order_by(InvoiceSnapshot.doc_type, InvoiceSnapshot.version.desc())
        ).scalars()
    )


def ruta_absoluta(relativa: str | None) -> Path | None:
    if not relativa:
        return None
    ruta = settings.data_dir / relativa
    return ruta if ruta.exists() else None
