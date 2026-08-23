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
                    )
                )
            return

        self.pila.append(
            {
                "tag": tag,
                "campo": campo,
                "oculta": oculta,
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
                    )
                )
            if marco["tag"] == tag:
                break


_CACHE: dict[str, Plantilla] = {}


def cargar(locale: str) -> Plantilla:
    """Plantilla del mercado, leida y anotada. Se guarda en memoria y se vuelve
    a leer sola si el archivo cambia en disco, para no tener que reiniciar el
    servidor cada vez que se retoca una plantilla."""
    ruta = TEMPLATES_DIR / get_market(locale).template
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
SOLO_ATRIBUTOS = ("url_verificacion", "codigo_barras", "codigo_qr")

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


def construir_valores(invoice) -> dict[str, str]:
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
    return valores


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
    plantilla = cargar(locale)
    valores = construir_valores(invoice)
    atributos = construir_atributos(invoice, codigos, qr_src)
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

        if campo == LOGO:
            if logo:
                cambios.append(
                    (hueco.cont_ini, hueco.cont_fin,
                     f'<img class="brand-logo" src="{html_mod.escape(logo, quote=True)}" '
                     f'alt="{html_mod.escape(marca, quote=True)}" '
                     'style="max-height:34px;max-width:220px">')
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
        if not valor.strip() and campo not in ocultables:
            vacios.append(campo)
        if not hueco.es_de_texto:
            # Marcado con data-field pero con hijos dentro: no se toca el
            # contenido. Mejor un hueco sin rellenar que un trozo de diseno
            # borrado sin querer.
            continue
        valor = _respeta_mayusculas(hueco.muestra, valor)
        cambios.append((hueco.cont_ini, hueco.cont_fin, html_mod.escape(valor, quote=False)))

    # Pasos de la barra de progreso: solo cambia la clase.
    for paso, ini, fin in _pasos(plantilla):
        tag = plantilla.fuente[ini:fin]
        cambios.append((ini, fin, _poner_atributo(tag, "class", _clases_paso(paso, invoice.status))))

    salida = plantilla.fuente
    for ini, fin, texto in sorted(cambios, key=lambda c: c[0], reverse=True):
        salida = salida[:ini] + texto + salida[fin:]

    salida = salida.replace(ASSETS_ORIGEN, assets)
    return Documento(html=salida, locale=locale, plantilla=plantilla.ruta, vacios=sorted(set(vacios)))


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
}


def etiqueta(campo: str) -> str:
    return ETIQUETAS_HUECO.get(campo, campo)


def huecos_de(locale: str) -> list[str]:
    """Nombres de los huecos que tiene una plantilla. Lo usa la pantalla de
    Plantillas para enseñar lo que de verdad hay en el archivo."""
    return sorted({h.campo for h in cargar(locale).huecos if h.campo})


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
