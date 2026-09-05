"""
Motor de plantillas.

Coge una factura de la base de datos y las tres plantillas aprobadas, y
devuelve el documento con los datos puestos. La regla que manda sobre todas las
demas: **el diseno no se toca**. El motor no genera maquetacion, no anade
etiquetas y no reordena nada; solo sustituye el contenido de los huecos que
estan marcados con data-field y algun atributo suelto (el href del QR, el alt
del codigo de barras, la clase de los pasos de la barra de progreso).

Como esta hecho
---------------
No se construye un arbol DOM ni se vuelve a serializar el HTML. Un arbol hay
que volver a escribirlo, y al escribirlo se normalizan comillas, espacios,
saltos de linea y etiquetas vacias: el archivo sale distinto del que se aprobo
aunque el navegador lo pinte parecido.

Aqui se hace al reves. Se recorre el HTML con html.parser solo para *anotar
posiciones*, y despues se cortan y pegan trozos del archivo original. Todo lo
que no sea un hueco marcado sale byte a byte como estaba. Eso es lo que permite
afirmar que la plantilla no se ha modificado, y verificarlo automaticamente.

Un hueco puede ser de dos tipos:

  - de texto      : la etiqueta no tiene hijos, se sustituye lo de dentro.
                    <b data-field="folio">RES-87241</b>
  - de atributos  : la etiqueta tiene hijos o es vacia (<img>), y entonces solo
                    se cambian los atributos declarados en ATRIBUTOS.
                    <a data-field="url_verificacion" href="..."><img></a>

Si un hueco de texto tuviera hijos, el motor lo trata como de atributos en vez
de tragarse el HTML de dentro. Nunca se pierde marcado por accidente.
"""
from __future__ import annotations

import html as html_mod
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from . import album, doctypes, verificaciones
from .config import PROJECT_DIR
from .fields import FIELD_MAP
from .locales import (
    delivery_texts,
    doc_text,
    headline,
    format_amount,
    format_date_long,
    format_date_numeric,
    format_date_short,
    format_date_weekday,
    get_market,
    status_text,
)
from .models import (
    STATUS_CANCELLED,
    STATUS_DELIVERED,
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_SCHEDULED,
    STATUS_VALIDATED,
)

TEMPLATES_DIR = PROJECT_DIR / "templates_html"

# Etiquetas que no llevan cierre. Si un data-field cae en una de ellas, es
# forzosamente un hueco de atributos.
VACIAS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

# Prefijo por defecto para los archivos de la plantilla (css, tipografias,
# imagenes). En el HTML aprobado son rutas relativas "../assets/..." porque se
# abria como archivo suelto; servido desde el panel hay que apuntarlas a la ruta
# donde estan montadas. Es la unica sustitucion que no viene de un data-field, y
# es de rutas, no de diseno.
ASSETS_ORIGEN = "../assets/"
ASSETS_PANEL = "/plantillas/assets/"

# Nombre interno del hueco de marca. Es el unico sitio donde el motor sustituye
# marcado y no texto, y por eso va declarado aparte y con su propia regla: si no
# hay logotipo propio, no se toca nada y se queda la marca aprobada.
LOGO = "__logo__"

# Icono de "Compra segura". Va aparte del logotipo por la misma razon que el QR:
# en el diseno aprobado NO es una imagen, es un vector dibujado dentro del propio
# HTML (<svg><use href="#i-shield">), porque un sprite externo no carga bajo
# file:// y romperia la vista previa local y varios generadores de PDF. Si el
# perfil de marca trae icono propio se sustituye por un <img>; si no, se queda
# el vector aprobado sin tocar.
SAFE_ICON = "__safe_icon__"

# Titulo del documento. Es texto, pero se marca aparte porque no sale de la
# factura sino del perfil de marca, y porque es el unico hueco que vive en el
# <head>: es lo que se ve en la pestana y lo que Chromium copia a los metadatos
# Title del PDF.
DOC_TITLE = "__doc_title__"


# --- lectura de la plantilla -------------------------------------------------


@dataclass
class Hueco:
    campo: str
    etiqueta: str
    tag_ini: int
    tag_fin: int
    cont_ini: int | None = None
    cont_fin: int | None = None
    muestra: str = ""
    hijos: bool = False
    ocultar_si_vacio: str | None = None
    # Estilo en linea para el <img> del logotipo propio, cuando el hueco lo
    # trae escrito. Sin el se usa el del tipo de documento. Existe porque la
    # marca sale ahora en las dos hojas y las dos NO se dibujan en la misma
    # unidad: la pagina 1 esta maquetada en pixeles y se escala al imprimir,
    # y la pagina 2 esta maquetada en milimetros. El mismo "max-height:34px"
    # en las dos daria dos logotipos de tamano distinto en el mismo PDF.
    estilo: str | None = None

    @property
    def es_de_texto(self) -> bool:
        return self.cont_ini is not None and not self.hijos


@dataclass
class Plantilla:
    ruta: Path
    fuente: str
    huecos: list[Hueco] = field(default_factory=list)
    firma: tuple = ()


class _Anotador(HTMLParser):
    """Recorre el HTML apuntando donde empieza y acaba cada hueco."""

    def __init__(self, fuente: str):
        super().__init__(convert_charrefs=False)
        self.fuente = fuente
        # Offset absoluto donde empieza cada linea, para traducir la posicion
        # (linea, columna) que da html.parser.
        self.inicios = [0]
        for linea in fuente.splitlines(keepends=True):
            self.inicios.append(self.inicios[-1] + len(linea))
        self.pila: list[dict] = []
        self.huecos: list[Hueco] = []

    def _offset(self) -> int:
        linea, columna = self.getpos()
        return self.inicios[linea - 1] + columna

    def handle_starttag(self, tag, attrs):
        if self.pila:
            self.pila[-1]["hijos"] = True
        diccionario = dict(attrs)
        campo = diccionario.get("data-field")
        oculta = diccionario.get("data-hide-if-empty")
        estilo = diccionario.get("data-logo-estilo")
        if "data-logo" in diccionario:
            campo = campo or LOGO
        if "data-safe-icon" in diccionario:
            campo = campo or SAFE_ICON
        if "data-doc-title" in diccionario:
            campo = campo or DOC_TITLE

        crudo = self.get_starttag_text() or ""
        ini = self._offset()

        if tag in VACIAS:
            if campo or oculta:
                self.huecos.append(
                    Hueco(
                        campo=campo or "",
                        etiqueta=tag,
                        tag_ini=ini,
                        tag_fin=ini + len(crudo),
                        muestra="",
                        hijos=True,
                        ocultar_si_vacio=oculta,
                        estilo=estilo,
                    )
                )
            return

        self.pila.append(
            {
                "tag": tag,
                "campo": campo,
                "oculta": oculta,
                "estilo": estilo,
                "tag_ini": ini,
                "tag_fin": ini + len(crudo),
                "hijos": False,
            }
        )

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        fin = self._offset()
        while self.pila:
            marco = self.pila.pop()
            if marco["campo"] or marco["oculta"]:
                self.huecos.append(
                    Hueco(
                        campo=marco["campo"] or "",
                        etiqueta=marco["tag"],
                        tag_ini=marco["tag_ini"],
                        tag_fin=marco["tag_fin"],
                        cont_ini=marco["tag_fin"],
                        cont_fin=fin,
                        muestra=self.fuente[marco["tag_fin"] : fin],
                        hijos=marco["hijos"],
                        ocultar_si_vacio=marco["oculta"],
                        estilo=marco["estilo"],
                    )
                )
            if marco["tag"] == tag:
                break


_CACHE: dict[str, Plantilla] = {}


def ruta_plantilla(locale: str, doc: str = doctypes.FACTURA) -> Path:
    """Archivo de la plantilla para ese mercado y ese tipo de documento.

    La pre-factura tiene un nombre de archivo distinto en cada mercado y lo dice
    locales.get_market(). Los documentos complementarios usan el mismo nombre en
    todos los mercados en los que existen, colgando de la carpeta del mercado.
    """
    tipo = doctypes.tipo(doc)
    if tipo.archivo is None:
        return TEMPLATES_DIR / get_market(locale).template
    return TEMPLATES_DIR / (locale or "es-MX") / tipo.archivo


def cargar(locale: str, doc: str = doctypes.FACTURA) -> Plantilla:
    """Plantilla del mercado, leida y anotada. Se guarda en memoria y se vuelve
    a leer sola si el archivo cambia en disco, para no tener que reiniciar el
    servidor cada vez que se retoca una plantilla."""
    ruta = ruta_plantilla(locale, doc)
    est = ruta.stat()
    firma = (est.st_mtime_ns, est.st_size)
    cacheada = _CACHE.get(ruta.as_posix())
    if cacheada is not None and cacheada.firma == firma:
        return cacheada

    fuente = ruta.read_text(encoding="utf-8")
    anotador = _Anotador(fuente)
    anotador.feed(fuente)
    anotador.close()
    plantilla = Plantilla(ruta=ruta, fuente=fuente, huecos=anotador.huecos, firma=firma)
    _CACHE[ruta.as_posix()] = plantilla
    return plantilla


# --- valores -----------------------------------------------------------------
#
# El puente entre las claves fijas (fields.py) y los nombres que llevan los
# huecos en el HTML aprobado. Se declara aqui, en un solo sitio, para que
# cambiar el nombre de un hueco en la plantilla no obligue a buscar por todo el
# codigo. Los nombres de los huecos vienen del Milestone 1 y estan en espanol.

HUECO_A_CLAVE = {
    "folio": "transaction.folio",
    "autorizacion": "transaction.authorization",
    "cliente_nombre": "customer.name",
    "cliente_email": "customer.email",
    "cliente_telefono": "customer.phone",
    "cliente_ciudad": "customer.city",
    "vehiculo": "vehicle.title",
    "vehiculo_ubicacion": "vehicle.location",
    "vin": "vehicle.vin",
    "anio": "vehicle.year",
    "tipo": "vehicle.type",
    "kilometraje": "vehicle.mileage",
    "combustible": "vehicle.fuel",
    "transmision": "vehicle.transmission",
    "descuento": "pricing.discount",
    "banco": "banking.bank",
    "beneficiario": "banking.beneficiary",
    "clabe": "banking.account_number",
    "cuenta": "banking.bank_account",
    "agente_nombre": "representative.name",
    "agente_cargo": "representative.role",
    "agente_telefono": "representative.phone",
    "agente_email": "representative.email",
    "agente_horario": "representative.hours",
}

# Huecos que no son una lectura directa: llevan formato, dependen del mercado o
# se calculan. El texto de cada uno se explica en construir_valores().
CALCULADOS = (
    "titular",
    "titular_texto",
    "fecha_emision",
    "vigencia",
    "fecha_entrega",
    "estado",
    "precio_vehiculo",
    "importe",
    "moneda",
    "cobertura",
    "transporte",
    "entrega_modalidad",
    "entrega_texto",
    "entrega_alternativa",
    "entrega_alternativa_texto",
    "agente_iniciales",
)

# Huecos de los que solo se tocan atributos.
#
# "pagina2" y "verificaciones_panel" son de la pagina 2: del primero se cambia
# el style para esconder la hoja entera cuando no hay album, y del segundo el
# alto del panel segun cuantas verificaciones haya marcadas. De ninguno de los
# dos se toca el contenido.
SOLO_ATRIBUTOS = (
    "url_verificacion",
    "codigo_barras",
    "codigo_qr",
    "pagina2",
    "verificaciones_panel",
    "resumen_soporte",
)

# Las cuatro fotografias del vehiculo, en el orden en que salen en el diseno: la
# grande y las tres pequenas. El nombre de archivo de cada una lo fija la
# plantilla aprobada y se conserva, de modo que en el snapshot basta con
# sobrescribir la copia y no hay que reescribir ninguna ruta.
FOTOS = ("foto_1", "foto_2", "foto_3", "foto_4")
ARCHIVO_FOTO = {
    "foto_1": "vehicle-front.jpg",
    "foto_2": "vehicle-rear.jpg",
    "foto_3": "vehicle-interior.jpg",
    "foto_4": "vehicle-main.jpg",
}

# Nombre que tendra cada fotografia del album dentro de la carpeta del snapshot.
# Mismo criterio que ARCHIVO_FOTO: nombre fijo, decidido aqui, para que congelar
# un documento sea copiar archivos encima y no reescribir rutas.
def archivo_album(posicion: int) -> str:
    return f"album/foto-{posicion:02d}.jpg"


def _texto(valor) -> str:
    return "" if valor is None else str(valor)


def iniciales(nombre: str | None) -> str:
    """'Yoselina de la Cruz' -> 'YC'. Las particulas no cuentan: si contaran,
    saldria 'YD' y el circulo del representante quedaria con unas iniciales que
    no son las suyas."""
    particulas = {"de", "del", "la", "las", "los", "y", "da", "van", "von", "of"}
    partes = [p for p in (nombre or "").split() if p.lower() not in particulas]
    if not partes:
        return ""
    if len(partes) == 1:
        return partes[0][:2].upper()
    return (partes[0][0] + partes[-1][0]).upper()


def construir_valores(invoice, doc: str = doctypes.FACTURA) -> dict[str, str]:
    """Texto que va en cada hueco, ya formateado para el mercado de la factura."""
    locale = invoice.locale or "es-MX"
    market = get_market(locale)
    moneda = invoice.pricing_currency or market.currency
    modo = (invoice.delivery_mode or "home").strip().lower()
    if modo not in ("home", "branch"):
        modo = "home"
    otro = "branch" if modo == "home" else "home"
    entrega = delivery_texts(locale)

    valores: dict[str, str] = {}
    for hueco, clave in HUECO_A_CLAVE.items():
        atributo = FIELD_MAP.get(clave)
        valores[hueco] = _texto(getattr(invoice, atributo, None) if atributo else None)

    # Fechas. Cada hueco lleva su formato y son tres distintos dentro del mismo
    # documento; son los de la version aprobada, no una eleccion mia.
    valores["fecha_emision"] = format_date_short(invoice.issue_date, locale)
    valores["vigencia"] = format_date_numeric(invoice.valid_until)
    valores["fecha_entrega"] = format_date_long(invoice.delivery_date, locale)

    # Estado: el texto que ve el cliente, en el idioma del documento.
    valores["estado"] = status_text(invoice.status, locale)

    # Titular y linea de debajo. Dependen del estado: en una factura ya
    # entregada, "Confirma el pago" contradice al resto del documento. En "Pago
    # pendiente" son los textos aprobados, sin tocar.
    valores["titular"], valores["titular_texto"] = headline(invoice.status, locale)

    # Importes. El precio lleva la moneda al lado; el de la pre-reserva no,
    # porque la plantilla la saca aparte en <small>.
    valores["precio_vehiculo"] = format_amount(
        invoice.pricing_vehicle_price, locale, currency=moneda
    )
    valores["importe"] = format_amount(
        invoice.pricing_reservation_amount, locale, with_currency=False
    )
    valores["moneda"] = moneda

    # Seguro y transporte: si no se ha escrito nada, la palabra que usa el
    # documento aprobado para ese idioma.
    valores["cobertura"] = _texto(invoice.pricing_coverage) or doc_text(locale, "incluido")
    valores["transporte"] = _texto(invoice.pricing_transport) or doc_text(locale, "incluido")

    # Entrega. El primer bloque describe la modalidad elegida y el segundo la
    # otra. Cada modalidad tiene dos redacciones, una para cuando va arriba y
    # otra para cuando queda como alternativa: la frase que empieza por "También
    # puedes solicitar" solo tiene sentido debajo. El operador puede sustituir
    # las dos escribiendo en los campos de entrega.
    valores["entrega_modalidad"] = entrega[modo]["titulo"]
    valores["entrega_texto"] = _texto(invoice.delivery_text) or entrega[modo]["principal"]
    valores["entrega_alternativa"] = entrega[otro]["titulo"]
    valores["entrega_alternativa_texto"] = (
        _texto(invoice.delivery_alt) or entrega[otro]["alternativa"]
    )

    valores["agente_iniciales"] = iniciales(invoice.representative_name)

    # --- huecos de los documentos complementarios ---------------------------
    #
    # Los dos HTML del Milestone 4 vienen marcados con las CLAVES FIJAS de
    # fields.py ("customer.name"), no con los nombres en espanol de los huecos
    # de la pre-factura ("cliente_nombre"). Se admiten las dos formas: es el
    # cliente quien marco sus archivos y renombrarle los huecos seria tocarle el
    # diseno para nada.
    valores.update(_valores_por_clave(invoice, locale, moneda, entrega, modo))
    valores.update(doctypes.textos_de_estado(doc, invoice.status))

    # --- pagina 2 -----------------------------------------------------------
    #
    # Los tres contadores del diseno eran texto fijo: "14 fotografias",
    # "6 verificaciones clave" y "Pagina 2 de 2". Los tres pasan a calcularse.
    # Un contador escrito a mano es una afirmacion que deja de ser cierta en
    # cuanto cambia lo que cuenta, y ademas no avisa: sigue ahi, diciendo 14.
    cuantas = len(fotos_album(invoice))
    valores["album_cuenta"] = (
        f"{cuantas} fotografía{'' if cuantas == 1 else 's'}" if cuantas else ""
    )
    n_verificadas = len(verificaciones.marcadas(invoice))
    valores["verificaciones_cuenta"] = (
        f"{n_verificadas} verificaci{'ón' if n_verificadas == 1 else 'ones'} clave"
        if n_verificadas
        else ""
    )
    valores["pagina_pie"] = doc_text(locale, "pagina_de").format(
        pagina=2, total=paginas(invoice, doc)
    )
    return valores


def tiene_pagina2(locale: str | None, doc: str = doctypes.FACTURA) -> bool:
    """Si la plantilla de ese mercado trae la hoja del album.

    Hoy solo la trae Mexico. Argentina e Inglaterra siguen con la pre-factura de
    una sola hoja, que es la que se aprobo para esos dos mercados.

    Se pregunta a la PLANTILLA y no a una lista de mercados escrita aqui: el dia
    que la pagina 2 se lleve a otro mercado, basta con anadirsela al archivo y
    esto se entera solo. Una lista habria que acordarse de tocarla.
    """
    try:
        plantilla = cargar(locale or "es-MX", doc)
    except (OSError, KeyError):
        return False
    return any(h.campo == "pagina2" for h in plantilla.huecos)


def paginas(invoice, doc: str = doctypes.FACTURA) -> int:
    """Cuantas hojas tiene el documento. Dos cuando hay album, una si no.

    Se calcula aqui, en un solo sitio, porque lo necesitan tanto el pie de la
    pagina 2 como el generador del PDF. Si cada uno lo dedujera por su cuenta,
    podria salir un documento de una hoja con un pie que dice "Pagina 2 de 2".

    Tener fotografias no basta: la plantilla del mercado tiene que traer la hoja.
    Sin esta condicion, subirle un album a una factura de Argentina o de
    Inglaterra dejaba el documento en una hoja y el generador de PDF esperando
    dos, y lo que veia el operador era "El PDF ha salido con 1 pagina y este
    documento es de 2. Revise si algun texto se ha alargado mucho": un aviso que
    apunta al sitio equivocado, sobre un PDF que ya no se llegaba a generar.
    """
    if not fotos_album(invoice):
        return 1
    return 2 if tiene_pagina2(invoice.locale, doc) else 1


# Separador de linea permitido dentro de un hueco de texto. El motor escapa
# SIEMPRE el valor y solo despues convierte este caracter en <br>, de modo que
# nada que venga de la base de datos puede colar marcado. La lista de huecos que
# lo admiten es explicita, nunca "todos".
SALTO = "\n"
HUECOS_CON_SALTO = frozenset({"delivery.estimated"})


def importe_restante(invoice):
    """Precio acordado menos apartado validado.

    Cerrado por escrito con el cliente el 29-ago-2026. No entran aqui el
    descuento, la cobertura ni el transporte, y no por olvido: en el modelo son
    columnas de TEXTO (String(120)), etiquetas como "9% DE DESCUENTO APLICADO",
    no importes. El precio del vehiculo ES el total final acordado, y el propio
    documento lo dice: "El precio acordado ya incluye todos estos conceptos".
    Asi que no hay nada que se pueda aplicar dos veces.

    Devuelve None si falta cualquiera de los dos numeros. Un hueco vacio con
    aviso en la vista previa es preferible a una cifra inventada en un documento
    que recibe el comprador.
    """
    precio = invoice.pricing_vehicle_price
    apartado = invoice.pricing_reservation_amount
    if precio is None or apartado is None:
        return None
    return precio - apartado


def _entrega_estimada(invoice, locale: str) -> str:
    """'LUNES 25/08' o 'LUNES 25/08 / A MAS TARDAR / MIERCOLES 26/08'.

    La segunda fecha es opcional, tal como lo pidio el cliente: con las dos se
    ensena el rango, y con la segunda vacia solo la fecha de entrega que ya
    existia, en una linea.
    """
    primera = format_date_weekday(invoice.delivery_date, locale)
    if not primera:
        return ""
    segunda = format_date_weekday(getattr(invoice, "delivery_date_latest", None), locale)
    if not segunda:
        return primera
    return f"{primera}{SALTO}{doc_text(locale, 'a_mas_tardar')}{SALTO}{segunda}"


def _valores_por_clave(invoice, locale, moneda, entrega, modo) -> dict[str, str]:
    """Los huecos marcados con la clave fija, tal como vienen en los dos HTML
    aprobados por el cliente."""
    restante = importe_restante(invoice)
    return {
        "customer.name": _texto(invoice.customer_name),
        "transaction.folio": _texto(invoice.folio),
        "transaction.authorization": _texto(invoice.authorization),
        "vehicle.model": _texto(invoice.vehicle_title),
        "vehicle.price": format_amount(invoice.pricing_vehicle_price, locale, currency=moneda),
        "payment.deposit_amount": format_amount(
            invoice.pricing_reservation_amount, locale, currency=moneda
        ),
        "payment.remaining_amount": (
            "" if restante is None else format_amount(restante, locale, currency=moneda)
        ),
        "delivery.method": _texto(invoice.delivery_text) or entrega[modo]["principal"],
        "delivery.estimated": _entrega_estimada(invoice, locale),
        "protection.until": format_date_numeric(invoice.valid_until),
        "representative.phone": _texto(invoice.representative_phone),
        "representative.email": _texto(invoice.representative_email),
    }


def _url_verificacion(invoice) -> str:
    base = (invoice.verify_url_base or "").strip()
    if not base:
        return ""
    return base if base.endswith(invoice.folio or "") else base.rstrip("/") + "/" + (invoice.folio or "")


def construir_atributos(
    invoice, codigos: str | None = None, qr_src: str | None = None
) -> dict[str, dict[str, str]]:
    """Atributos que dependen de la factura. Son pocos y estan todos aqui.

    codigos="panel" hace que el QR y el codigo de barras apunten a las rutas que
    los generan al vuelo. Sin ese argumento se dejan las rutas que trae la
    plantilla, que es lo que quiere el snapshot: alli los dos archivos se
    escriben en su sitio con el mismo nombre, de modo que la copia congelada
    sigue siendo una carpeta que se abre sola.

    qr_src cambia solo el archivo del QR. Hace falta cuando el QR no lo dibuja
    el servidor sino que es una imagen subida a mano: la plantilla pide un .svg
    y la imagen puede ser un PNG, y un PNG guardado con nombre .svg es
    exactamente la clase de mentira que rompe las cosas mas adelante.
    """
    locale = invoice.locale or "es-MX"
    folio = invoice.folio or ""
    fuentes = {}
    if codigos == "panel":
        fuentes = {
            "codigo_qr": {"src": f"/facturas/{invoice.id}/codigo-qr"},
            "codigo_barras": {"src": f"/facturas/{invoice.id}/codigo-barras.svg"},
        }
    if qr_src:
        fuentes["codigo_qr"] = {"src": qr_src}

    # Fotografias.
    #
    # El texto alternativo solo se cambia cuando la fotografia se ha cambiado.
    # El del diseno aprobado describe el coche de la maqueta ("Vista principal
    # del Audi A3 plateado"); mientras la imagen siga siendo esa, el texto es
    # correcto y se deja intacto. En cuanto el operador sube la suya, ese texto
    # pasa a ser falso y se sustituye por el vehiculo de la factura.
    subidas = {f.position for f in getattr(invoice, "photos", [])}
    atributos = {}
    for posicion, campo in enumerate(FOTOS, start=1):
        atributos[campo] = {}
        if posicion in subidas:
            if invoice.vehicle_title:
                atributos[campo]["alt"] = invoice.vehicle_title
            if codigos == "panel":
                atributos[campo]["src"] = f"/facturas/{invoice.id}/foto/{posicion}"

    # --- pagina 2 -----------------------------------------------------------
    #
    # Dos alturas y dos "esto no se ensena", todo en el atributo style. Se hace
    # con atributos y no escondiendo el contenido porque un panel vacio con su
    # borde verde sigue ocupando sitio y sigue diciendo "Verificacion".
    n_fotos = len(fotos_album(invoice))
    atributos["pagina2"] = {} if n_fotos else {"style": "display:none"}

    n_verificadas = len(verificaciones.marcadas(invoice))
    if n_verificadas:
        atributos["verificaciones_panel"] = {
            "style": f"height:{alto_verificaciones(n_verificadas):.1f}mm"
        }
    else:
        atributos["verificaciones_panel"] = {"style": "display:none"}

    # La franja de resumen. Solo sale cuando, DESPUES de estirar el album hasta
    # su tope, sigue sobrando sitio para ella entera. No se encoge ni se estira:
    # o cabe con su alto o no se ensena.
    atributos["resumen_soporte"] = (
        {}
        if n_fotos and hay_resumen(album.repartir(n_fotos), n_verificadas)
        else {"style": "display:none"}
    )

    atributos.update({
        "url_verificacion": {
            "href": _url_verificacion(invoice),
            "aria-label": doc_text(locale, "aria_verificar").format(folio=folio),
        },
        "codigo_barras": {
            "alt": doc_text(locale, "alt_barras").format(folio=folio),
            **fuentes.get("codigo_barras", {}),
        },
        "codigo_qr": {
            "alt": doc_text(locale, "alt_qr").format(folio=folio),
            **fuentes.get("codigo_qr", {}),
        },
    })
    return atributos


# --- pagina 2: album y verificaciones ---------------------------------------
#
# Estos tres huecos no llevan texto: llevan marcado que se genera aqui. El motor
# los trata como al logotipo, sustituyendo el CONTENIDO del elemento y dejando
# intacto el elemento en si.
#
# El CSS de la rejilla lo escribe album.py y entra por el hueco album_estilos,
# en vez de estar copiado en la hoja de estilos. Es a proposito: la hoja de
# contactos que aprobo el cliente sale de album.py, y si el documento pintara
# con una copia del CSS, las dos podrian separarse sin que nadie se enterara.
ALBUM = "album"
ALBUM_ESTILOS = "album_estilos"
VERIFICACIONES = "verificaciones"

MARCADO = (ALBUM, ALBUM_ESTILOS, VERIFICACIONES)

# Huecos que pueden salir vacios sin que eso signifique que falta un dato. Los
# dos contadores de la pagina 2 se quedan en blanco cuando no hay album o no
# hay ninguna verificacion marcada, y eso es un estado legitimo, no un olvido:
# avisar de ellos en la vista previa haria que el aviso de "faltan datos"
# saltara siempre y dejara de significar nada.
OPCIONALES = ("album_cuenta", "verificaciones_cuenta")


def fotos_album(invoice) -> list:
    """Las fotografias del album, ya ordenadas por posicion y sin huecos.

    Si faltara la posicion 7 -por ejemplo, porque se borro a mano en la base-
    el album NO debe dejar un agujero ni saltarse un numero: se renumeran de
    corrido. Lo que el documento ensena es "las fotografias de este vehiculo",
    no "las posiciones ocupadas de una tabla".
    """
    fotos = sorted(getattr(invoice, "photos", []) or [], key=lambda f: f.position)
    return fotos[: album.MAX_FOTOS]


def _fuente_album(invoice, codigos: str | None):
    """De donde sale la imagen de cada posicion del album."""
    fotos = fotos_album(invoice)
    titulo = invoice.vehicle_title or ""

    def src(i: int) -> str:
        if codigos == "panel":
            return f"/facturas/{invoice.id}/foto/{fotos[i - 1].position}"
        # Fuera del panel, el documento apunta al nombre que la fotografia
        # tendra dentro de la carpeta del snapshot. Congelar es copiar los
        # archivos con ese nombre; no hay que reescribir ninguna ruta.
        return ASSETS_ORIGEN + "img/" + archivo_album(i)

    def alt(i: int) -> str:
        # El texto alternativo NO describe el coche del diseno. Decir "Audi A3
        # plateado" en una factura de otro vehiculo seria una mentira en el
        # unico sitio donde nadie la ve: el texto que solo leen los lectores de
        # pantalla.
        return f"{titulo} — fotografía {i}" if titulo else f"Fotografía {i} del vehículo"

    return len(fotos), src, alt


def _tarjeta_verificacion(v) -> str:
    return (
        '<div class="verify-card"><div class="verify-icon">'
        '<svg class="icon" viewBox="0 0 24 24">' + v.icono + "</svg>"
        '<span class="mini-check">✓</span></div><div>'
        f"<strong>{html_mod.escape(v.titulo)}</strong>"
        f"<p>{html_mod.escape(v.texto)}</p></div></div>"
    )


def construir_marcado(invoice, codigos: str | None = None) -> dict[str, str]:
    """Los huecos de la pagina 2 que se rellenan con marcado generado."""
    cuantas, src, alt = _fuente_album(invoice, codigos)
    marcadas = verificaciones.marcadas(invoice)

    marcado_album, estilos = "", album.CSS
    if cuantas:
        reparto = album.repartir(cuantas)
        marcado_album = album.marcado(reparto, src, alt)
        estilos += _estilo_estirado(reparto, len(marcadas))

    return {
        ALBUM_ESTILOS: estilos,
        ALBUM: marcado_album,
        VERIFICACIONES: "".join(_tarjeta_verificacion(v) for v in marcadas),
    }


# Alto del panel de verificaciones, en milimetros, segun cuantas filas de
# tarjetas haya. Las constantes salen del diseno: 25.1mm por tarjeta, 2.1mm de
# separacion entre filas, y 18.7mm entre el relleno del panel y su cabecera.
# Con dos filas da los 71mm exactos que el diseno tenia escritos a mano, y esa
# igualdad la comprueba la bateria: es la forma de saber que la formula no se
# ha inventado el numero.
ALTO_TARJETA_MM = 25.1
SEPARACION_TARJETAS_MM = 2.1
CABECERA_VERIFICACIONES_MM = 18.7


def alto_verificaciones(cuantas: int) -> float:
    filas = verificaciones.filas(cuantas)
    if not filas:
        return 0.0
    return (
        CABECERA_VERIFICACIONES_MM
        + filas * ALTO_TARJETA_MM
        + (filas - 1) * SEPARACION_TARJETAS_MM
    )


# --- el album aprovecha el hueco que dejan las verificaciones que faltan ------
#
# Con seis verificaciones la hoja va llena y el album ocupa sus 136mm. Con menos
# el panel de abajo encoge, y como el pie esta pegado al borde inferior, lo que
# aparecia era una franja de blanco en mitad de la pagina.
#
# Lo que se hace NO es recalcular el album para el hueco nuevo. El reparto de
# 1 a 20 esta congelado a 136mm -es el que se aprobo- y sigue siendo el mismo:
# lo unico que cambia es a que alto se pinta. Dos reglas de CSS, ni una foto de
# sitio.
#
# Y no se estira todo lo que cabe. Estirar sin freno pone las fotografias mas
# altas que anchas y un coche recortado en vertical se ve por el centro de la
# puerta. El tope es el mismo suelo de forma que ya usaba la regla, 0.88, y lo
# marca la fotografia mas cuadrada del reparto. Cuando el tope llega antes que
# el hueco, sobra blanco: eso es lo correcto, no un defecto.

# Lo que separa el panel de verificaciones de lo que tiene encima. Sale del
# margin-top de .verify-wrap en pagina2.css. Cuando no hay ninguna marcada el
# panel entero desaparece y esta separacion se va con el.
SEPARACION_VERIFICACIONES_MM = 3.5

# La cabecera del album -el titulo y la pastilla con el numero de fotografias-
# va encima del album y dentro del mismo bloque, asi que el bloque mide siempre
# esto mas el album.
ALTO_CABECERA_ALBUM_MM = 9.0


def _alto_bloque_verificaciones(cuantas: int) -> float:
    alto = alto_verificaciones(cuantas)
    return alto + SEPARACION_VERIFICACIONES_MM if alto else 0.0


def hueco_libre(cuantas_verificaciones: int) -> float:
    """Milimetros que sobran respecto a la hoja llena de seis verificaciones.

    Se mide contra el caso de seis y no contra el borde de la hoja a proposito.
    Con seis verificaciones la pagina 2 ya deja algo de aire encima del pie: ese
    aire es parte del diseno aprobado y no se toca. El album solo se queda con
    lo que liberan las verificaciones que NO estan.
    """
    completo = _alto_bloque_verificaciones(len(verificaciones.CLAVES))
    return completo - _alto_bloque_verificaciones(cuantas_verificaciones)


def alto_album(cuantas_fotos: int, cuantas_verificaciones: int) -> float:
    """A que alto se pinta el album. Nunca menos que el congelado."""
    if cuantas_fotos < 1:
        return album.ALTO_BASE_MM
    return alto_album_de(album.repartir(cuantas_fotos), cuantas_verificaciones)


def alto_album_de(reparto, cuantas_verificaciones: int) -> float:
    cabe = album.ALTO_BASE_MM + hueco_libre(cuantas_verificaciones)
    return min(cabe, album.alto_maximo(reparto))


def _estilo_estirado(reparto, cuantas_verificaciones: int) -> str:
    """Las dos medidas que cambian cuando el album se estira, y nada mas.

    Sale por el hueco album_estilos, detras del CSS de la rejilla, en vez de
    tocar pagina2.css: asi la hoja de estilos sigue diciendo lo que dice el
    diseno aprobado y el estiron se ve entero en un solo sitio.
    """
    alto = alto_album_de(reparto, cuantas_verificaciones)
    if alto - album.ALTO_BASE_MM < 0.05:
        return ""
    return (
        f".pagina2 .p2-main{{height:{ALTO_CABECERA_ALBUM_MM + alto:.1f}mm}}"
        f".album{{height:{alto:.1f}mm}}"
    )


# --- la franja de resumen cierra el blanco que el tope deja abierto -----------
#
# Cuando el tope de 0.88 llega antes que el hueco, el album para y queda una
# franja de blanco encima del pie. Es correcto -estirar mas recortaria los
# coches por el centro de la puerta- pero deja la hoja con cara de inacabada.
#
# Lo que va ahi NO es contenido nuevo: son tres datos que ya salen en el
# documento -cuantas fotografias tiene el album, el folio y el estado de la
# reserva- puestos en horizontal. Ni una verificacion mas, ni un dato que no
# estuviera ya impreso en la misma hoja.
#
# Y no se estira ni se encoge. Tiene un alto fijo y sale solo si cabe entero,
# por dos razones: una franja de alto variable seria un cuarto elemento con
# geometria propia que mantener, y una franja apretada se ve peor que un poco
# de aire. Con lo que hay hoy, o sobran mas de 41mm o sobra menos de 1,2mm: no
# hay ningun caso intermedio en el que la decision sea dudosa.
ALTO_RESUMEN_MM = 38.0
SEPARACION_RESUMEN_MM = 3.5


def hueco_sobrante(reparto, cuantas_verificaciones: int) -> float:
    """Blanco que queda encima del pie DESPUES de estirar el album."""
    estiron = alto_album_de(reparto, cuantas_verificaciones) - album.ALTO_BASE_MM
    return hueco_libre(cuantas_verificaciones) - estiron


def hay_resumen(reparto, cuantas_verificaciones: int) -> bool:
    """Si la franja de resumen cabe entera en lo que sobra."""
    return (
        hueco_sobrante(reparto, cuantas_verificaciones)
        >= ALTO_RESUMEN_MM + SEPARACION_RESUMEN_MM
    )


# Barra de progreso. La mueve el estado de la operacion y nada mas: generar el
# PDF o enviarlo no la tocan, porque no significan que el cliente haya pagado.
#
# Los cuatro pasos son los del documento aprobado y sus nombres no se cambian.
# El tercero se llama "Documentacion y tramites", que es justo lo que empieza
# cuando el pago queda validado.
PROGRESO = {
    STATUS_DRAFT: {1: "active"},
    STATUS_PENDING: {1: "done", 2: "active"},
    STATUS_VALIDATED: {1: "done", 2: "done", 3: "active"},
    STATUS_SCHEDULED: {1: "done", 2: "done", 3: "done", 4: "active"},
    STATUS_DELIVERED: {1: "done", 2: "done", 3: "done", 4: "done"},
    STATUS_CANCELLED: {1: "done"},
}


def _clases_paso(paso: int, estado: str) -> str:
    mapa = PROGRESO.get(estado, PROGRESO[STATUS_PENDING])
    extra = mapa.get(paso)
    return "step" + (f" {extra}" if extra else "")


# --- sustitucion -------------------------------------------------------------

def _poner_atributo(tag: str, nombre: str, valor: str) -> str:
    """Cambia un atributo dentro del texto crudo de una etiqueta, o lo anade si
    no estaba. Se trabaja sobre el trozo de la propia etiqueta, nunca sobre el
    documento entero, para no tocar otra cosa que se llame igual."""
    escapado = html_mod.escape(valor, quote=True)
    patron = re.compile(r"(\s%s=)(\"[^\"]*\"|'[^']*')" % re.escape(nombre))
    if patron.search(tag):
        return patron.sub(lambda m: f'{m.group(1)}"{escapado}"', tag, count=1)
    cierre = "/>" if tag.rstrip().endswith("/>") else ">"
    return tag[: tag.rfind(cierre)].rstrip() + f' {nombre}="{escapado}"' + cierre


def _respeta_mayusculas(muestra: str, valor: str) -> str:
    """Si en la plantilla aprobada ese hueco esta en mayusculas (la pastilla de
    estado, la del descuento), el valor tambien. El diseno usa la mayuscula como
    parte del estilo y no hay CSS que la fuerce."""
    limpio = muestra.strip()
    if limpio and any(c.isalpha() for c in limpio) and limpio == limpio.upper():
        return valor.upper()
    return valor


@dataclass
class Documento:
    html: str
    locale: str
    plantilla: Path
    vacios: list[str]
    doc: str = doctypes.FACTURA


def render(
    invoice,
    *,
    assets: str = ASSETS_PANEL,
    codigos: str | None = None,
    logo: str | None = None,
    qr_src: str | None = None,
    marca: str = "DulceAuto",
    safe_icon: str | None = None,
    doc_title: str | None = None,
    doc: str = doctypes.FACTURA,
) -> Documento:
    """Documento de una factura, listo para enseñar o para imprimir.

    logo es la ruta o URL del logotipo propio. Sin logotipo se conserva la marca
    del diseno aprobado, que es lo unico que el motor sustituye en forma de
    marcado y no de texto.

    safe_icon es el icono de "Compra segura" del perfil de marca, y sigue la
    misma regla: sin icono propio se queda el vector aprobado.

    doc_title es el titulo del documento. Cambiarlo cambia a la vez la pestana
    del navegador y los metadatos Title del PDF, porque Chromium copia el
    <title> del HTML al imprimir. El nombre del archivo que se descarga es otra
    cosa distinta y se decide en main.py.
    """
    locale = invoice.locale or "es-MX"
    tipo = doctypes.tipo(doc)
    plantilla = cargar(locale, doc)
    valores = construir_valores(invoice, doc)
    atributos = construir_atributos(invoice, codigos, qr_src)
    generado = construir_marcado(invoice, codigos)
    vacios: list[str] = []
    # Un hueco que desaparece cuando esta vacio no es un dato que falte: el
    # descuento no existe en la mayoria de las operaciones.
    ocultables = {h.ocultar_si_vacio for h in plantilla.huecos if h.ocultar_si_vacio}

    # Se recogen los cambios como (inicio, fin, texto) y se aplican de una vez,
    # de atras hacia delante, para que los offsets sigan siendo validos.
    cambios: list[tuple[int, int, str]] = []

    for hueco in plantilla.huecos:
        campo = hueco.campo

        if hueco.ocultar_si_vacio:
            if not valores.get(hueco.ocultar_si_vacio, "").strip():
                cambios.append(
                    (hueco.tag_ini, hueco.tag_fin, _ocultar(plantilla.fuente[hueco.tag_ini : hueco.tag_fin]))
                )
            if not campo:
                continue

        if campo in atributos:
            tag = plantilla.fuente[hueco.tag_ini : hueco.tag_fin]
            for nombre, valor in atributos[campo].items():
                tag = _poner_atributo(tag, nombre, valor)
            cambios.append((hueco.tag_ini, hueco.tag_fin, tag))
            continue

        if campo in generado:
            # Marcado que genera el servidor: el album y las tarjetas de
            # verificacion. Va SIN escapar, a diferencia de todo lo demas,
            # porque es HTML y no texto. Puede hacerse porque no lleva ni un
            # solo dato de la base de datos: los nombres de archivo los inventa
            # album.py y los textos son constantes de verificaciones.py. Lo
            # unico que viene de fuera -el titulo del vehiculo, en el alt- se
            # escapa dentro de album.marcado antes de llegar aqui.
            if hueco.cont_ini is not None:
                cambios.append((hueco.cont_ini, hueco.cont_fin, generado[campo]))
            continue

        if campo == LOGO:
            if logo:
                cambios.append(
                    (hueco.cont_ini, hueco.cont_fin,
                     f'<img class="brand-logo" src="{html_mod.escape(logo, quote=True)}" '
                     f'alt="{html_mod.escape(marca, quote=True)}" '
                     f'style="{hueco.estilo or tipo.logo_estilo}">')
                )
            continue

        if campo == SAFE_ICON:
            # Mismo criterio que el logotipo: sin icono propio no se toca nada y
            # se queda el vector del diseno aprobado.
            if safe_icon:
                cambios.append(
                    (hueco.cont_ini, hueco.cont_fin,
                     f'<img class="safe-icon" src="{html_mod.escape(safe_icon, quote=True)}" '
                     'alt="" aria-hidden="true" '
                     'style="width:34px;height:34px;object-fit:contain">')
                )
            continue

        if campo == DOC_TITLE:
            if doc_title:
                cambios.append(
                    (hueco.cont_ini, hueco.cont_fin, html_mod.escape(doc_title, quote=False))
                )
            continue

        if campo not in valores:
            continue

        valor = valores[campo]
        if not valor.strip() and campo not in ocultables and campo not in OPCIONALES:
            vacios.append(campo)
        if not hueco.es_de_texto:
            # Marcado con data-field pero con hijos dentro: no se toca el
            # contenido. Mejor un hueco sin rellenar que un trozo de diseno
            # borrado sin querer.
            continue
        valor = _respeta_mayusculas(hueco.muestra, valor)
        # Se escapa SIEMPRE y solo despues, y solo en los huecos de la lista, se
        # convierte el separador en <br>. Al reves seria una puerta abierta:
        # cualquier dato de la base podria traer marcado.
        escapado = html_mod.escape(valor, quote=False)
        if campo in HUECOS_CON_SALTO:
            escapado = escapado.replace(SALTO, "<br>")
        cambios.append((hueco.cont_ini, hueco.cont_fin, escapado))

    # Pasos de la barra de progreso: solo cambia la clase.
    for paso, ini, fin in _pasos(plantilla):
        tag = plantilla.fuente[ini:fin]
        cambios.append((ini, fin, _poner_atributo(tag, "class", _clases_paso(paso, invoice.status))))

    salida = plantilla.fuente
    for ini, fin, texto in sorted(cambios, key=lambda c: c[0], reverse=True):
        salida = salida[:ini] + texto + salida[fin:]

    salida = salida.replace(ASSETS_ORIGEN, assets)
    return Documento(
        html=salida, locale=locale, plantilla=plantilla.ruta,
        vacios=sorted(set(vacios)), doc=tipo.clave,
    )


def _ocultar(tag: str) -> str:
    """Esconde un elemento sin quitarlo del documento. Se usa style y no el
    atributo hidden porque el CSS aprobado pone display:flex en la pastilla, y
    display gana a hidden."""
    estilo = re.search(r'\sstyle="([^"]*)"', tag)
    if estilo:
        return _poner_atributo(tag, "style", estilo.group(1).rstrip("; ") + ";display:none")
    return _poner_atributo(tag, "style", "display:none")


_PASO = re.compile(r'<div class="step(?:[^"]*)"[^>]*data-step="(\d)"[^>]*>')


def _pasos(plantilla: Plantilla):
    for m in _PASO.finditer(plantilla.fuente):
        yield int(m.group(1)), m.start(), m.end()


# --- ayudas para el panel ----------------------------------------------------


# Nombre legible de cada hueco, para poder decir "falta la ciudad del cliente"
# en lugar de "falta cliente_ciudad".
ETIQUETAS_HUECO = {
    # --- pagina 2 ---
    "album": "Álbum de fotografías (página 2)",
    "album_estilos": "Rejilla del álbum (la genera el servidor)",
    "album_cuenta": "Cuántas fotografías tiene el álbum",
    "verificaciones": "Tarjetas de verificación marcadas",
    "verificaciones_cuenta": "Cuántas verificaciones se imprimen",
    "verificaciones_panel": "Panel de verificaciones (alto y visibilidad)",
    "resumen_soporte": "Franja de resumen (sale solo si sobra sitio)",
    "pagina2": "La página 2 entera (se esconde sin álbum)",
    "pagina_pie": "Pie «Página 2 de 2»",
    "folio": "Folio",
    "fecha_emision": "Fecha de emisión",
    "vigencia": "Vigencia de la protección",
    "autorizacion": "Autorización",
    "estado": "Estado",
    "titular": "Titular del documento",
    "titular_texto": "Línea bajo el titular",
    "fecha_entrega": "Fecha de entrega",
    "cliente_nombre": "Nombre del cliente",
    "cliente_email": "Email del cliente",
    "cliente_telefono": "Teléfono del cliente",
    "cliente_ciudad": "Ciudad del cliente",
    "vehiculo": "Vehículo",
    "vehiculo_ubicacion": "Ubicación del vehículo",
    "vin": "VIN",
    "anio": "Año",
    "tipo": "Tipo",
    "kilometraje": "Kilometraje",
    "combustible": "Combustible",
    "transmision": "Transmisión",
    "descuento": "Descuento",
    "precio_vehiculo": "Precio del vehículo",
    "importe": "Importe de pre-reserva",
    "moneda": "Moneda",
    "cobertura": "Seguro",
    "transporte": "Transporte",
    "banco": "Banco receptor",
    "beneficiario": "Beneficiario",
    "clabe": "CLABE / CBU",
    "cuenta": "Cuenta bancaria",
    "entrega_modalidad": "Modalidad de entrega",
    "entrega_texto": "Descripción de la modalidad",
    "entrega_alternativa": "Modalidad alternativa",
    "entrega_alternativa_texto": "Descripción de la alternativa",
    "agente_nombre": "Representante",
    "agente_cargo": "Cargo del representante",
    "agente_iniciales": "Iniciales del representante",
    "agente_telefono": "Teléfono del representante",
    "agente_email": "Email del representante",
    "agente_horario": "Horario del representante",
    "url_verificacion": "URL de verificación",
    "codigo_barras": "Código de barras",
    "codigo_qr": "Código QR",
    "foto_1": "Fotografía principal",
    "foto_2": "Fotografía pequeña 1",
    "foto_3": "Fotografía pequeña 2",
    "foto_4": "Fotografía pequeña 3",
    LOGO: "Logotipo de la cabecera",
    SAFE_ICON: "Icono de Compra segura",
    DOC_TITLE: "Título del documento",
    # Huecos de los dos documentos complementarios. Van con la clave fija tal
    # como el cliente marco sus archivos.
    "customer.name": "Nombre del cliente",
    "transaction.folio": "Folio",
    "transaction.authorization": "Autorización",
    "vehicle.model": "Vehículo",
    "vehicle.price": "Precio acordado",
    "payment.deposit_amount": "Importe del apartado",
    "payment.remaining_amount": "Pago restante",
    "delivery.method": "Método de entrega",
    "delivery.estimated": "Entrega estimada",
    "protection.until": "Vigencia de la protección",
    "representative.phone": "Teléfono del representante",
    "representative.email": "Email del representante",
    "doc_estado_frase": "Frase del estado",
    "doc_proxima_titulo": "Próxima etapa (título)",
    "doc_proxima_texto": "Próxima etapa (texto)",
    "doc_registro_titulo": "Estado de la operación (título)",
    "doc_registro_texto": "Estado de la operación (texto)",
    "doc_restante_sub": "Nota bajo el pago restante",
    "doc_entrega_sub": "Nota bajo la entrega estimada",
    "doc_paso_pago": "Paso 2 del guion (pago restante)",
}


def etiqueta(campo: str) -> str:
    return ETIQUETAS_HUECO.get(campo, campo)


def huecos_de(locale: str, doc: str = doctypes.FACTURA) -> list[str]:
    """Nombres de los huecos que tiene una plantilla. Lo usa la pantalla de
    Plantillas para enseñar lo que de verdad hay en el archivo."""
    return sorted({h.campo for h in cargar(locale, doc).huecos if h.campo})


# Claves fijas que el motor sí usa aunque no sean una lectura directa: llevan
# formato, deciden un texto o gobiernan la barra de progreso.
CLAVES_CALCULADAS = {
    "template.locale",
    "invoice.folio",
    "invoice.status",
    "transaction.status",
    "transaction.issue_date",
    "transaction.valid_until",
    "delivery.date",
    "delivery.mode",
    "delivery.text",
    "delivery.alt",
    "pricing.vehicle_price",
    "vehicle.price",
    "pricing.reservation_amount",
    "payment.amount",
    "pricing.currency",
    "pricing.coverage",
    "pricing.transport",
    "payment.account",
    "verification.url_base",
}

# Claves que a dia de hoy no se colocan en el documento, con el motivo. Se
# ensena en la pantalla de Plantillas: es preferible tenerlo escrito a que
# alguien rellene un campo y luego no lo encuentre en el papel.
CLAVES_SIN_HUECO = {
    "vehicle.carfax": (
        "Las tres plantillas aprobadas enseñan el VIN y la palabra REPORTE, "
        "pero no tienen un hueco para la referencia CARFAX."
    ),
    "banking.payment_reference": (
        "El documento usa el folio como referencia de pago, que es justo lo que "
        "se pidió: folio y referencia no pueden ir por separado."
    ),
    "banking.account_label": (
        "La etiqueta de la cuenta (CLABE, CBU) es texto fijo de cada plantilla, "
        "porque forma parte del idioma del documento."
    ),
}


def campos_sin_hueco(locale: str) -> list[tuple[str, str]]:
    """Claves fijas que no acaban en el documento, con el motivo."""
    cubiertos = set(HUECO_A_CLAVE.values()) | CLAVES_CALCULADAS
    fuera = [k for k in FIELD_MAP if k not in cubiertos]
    return sorted((k, CLAVES_SIN_HUECO.get(k, "Sin hueco en la plantilla.")) for k in fuera)
