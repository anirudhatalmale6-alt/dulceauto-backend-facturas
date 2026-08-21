"""
Reglas por mercado.

Un solo backend sirve las tres plantillas. Lo que cambia entre mercados esta
todo aqui y en ningun otro sitio: moneda, formato de importes, etiqueta y
longitud de la cuenta, terminologia del documento y ruta de la plantilla.

El formato de importe no es un detalle menor. Mexico escribe 329,000.00 y
Argentina 329.000,00. Con los separadores invertidos, un mismo numero se lee
como dos cantidades muy distintas.
"""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Market:
    code: str  # es-MX | en | es-AR
    label: str
    flag: str
    currency: str
    thousands: str
    decimal: str
    account_label: str
    account_digits: int
    document_label: str  # INE, ID, DNI
    template: str  # ruta relativa de la plantilla aprobada
    html_lang: str


MARKETS: dict[str, Market] = {
    "es-MX": Market(
        code="es-MX",
        label="México",
        flag="MX",
        currency="MXN",
        thousands=",",
        decimal=".",
        account_label="CLABE interbancaria (18 dígitos)",
        account_digits=18,
        document_label="INE",
        template="es-MX/factura.html",
        html_lang="es-MX",
    ),
    "en": Market(
        code="en",
        label="English",
        flag="US",
        currency="MXN",
        thousands=",",
        decimal=".",
        # La version inglesa usa una CLABE mexicana, y el propio HTML aprobado
        # dice "CLABE (18 digits)". El panel decia otra cosa: se corrige aqui,
        # que es donde estaba el error, sin tocar la plantilla.
        account_label="CLABE (18 digits)",
        account_digits=18,
        document_label="ID",
        template="en/invoice.html",
        html_lang="en",
    ),
    "es-AR": Market(
        code="es-AR",
        label="Argentina",
        flag="AR",
        currency="ARS",
        thousands=".",
        decimal=",",
        account_label="CBU (22 dígitos)",
        account_digits=22,
        document_label="DNI",
        template="es-AR/factura.html",
        html_lang="es-AR",
    ),
}

DEFAULT_LOCALE = "es-MX"


def get_market(locale: str | None) -> Market:
    return MARKETS.get(locale or "", MARKETS[DEFAULT_LOCALE])


def format_amount(value, locale: str, with_currency: bool = True, currency: str | None = None) -> str:
    """329000 -> '$329,000.00 MXN' en Mexico y '$329.000,00 ARS' en Argentina.

    La moneda se puede forzar: la factura guarda la suya y manda sobre la del
    mercado, porque una operacion firmada en pesos mexicanos sigue siendo en
    pesos mexicanos aunque manana se cambie el mercado por defecto.
    """
    if value is None:
        return ""
    market = get_market(locale)
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (ArithmeticError, ValueError):
        return str(value)

    entero, _, decimales = f"{amount:.2f}".partition(".")
    negativo = entero.startswith("-")
    entero = entero.lstrip("-")

    grupos = []
    while len(entero) > 3:
        grupos.insert(0, entero[-3:])
        entero = entero[:-3]
    grupos.insert(0, entero)

    texto = market.thousands.join(grupos) + market.decimal + decimales
    if negativo:
        texto = "-" + texto
    texto = "$" + texto
    return f"{texto} {currency or market.currency}" if with_currency else texto


# --- fechas ------------------------------------------------------------------
#
# Los nombres de los meses van escritos aqui y no se piden al sistema. El
# formato de strftime depende del "locale" instalado en la maquina, y una
# maquina sin el idioma configurado devuelve los meses en ingles sin avisar: la
# factura mexicana saldria con "July" el dia que el servidor cambie de sitio.

MESES = {
    "es": (
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ),
    "en": (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ),
}

MESES_CORTOS = {
    "es": ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
}


def _idioma(locale: str) -> str:
    return "en" if get_market(locale).code == "en" else "es"


def format_date_short(value, locale: str) -> str:
    """'22 Jul 2026'. Es el formato de la cabecera en las tres plantillas."""
    if not value:
        return ""
    return f"{value.day} {MESES_CORTOS[_idioma(locale)][value.month - 1]} {value.year}"


def format_date_long(value, locale: str) -> str:
    """'27 de julio de 2026' en espanol y '27 July 2026' en ingles. Es el
    formato de la fecha de entrega."""
    if not value:
        return ""
    idioma = _idioma(locale)
    mes = MESES[idioma][value.month - 1]
    if idioma == "en":
        return f"{value.day} {mes} {value.year}"
    return f"{value.day} de {mes} de {value.year}"


def format_date_numeric(value) -> str:
    """'29/07/2026'. Es el formato de la vigencia, igual en los tres mercados."""
    if not value:
        return ""
    return f"{value.day:02d}/{value.month:02d}/{value.year}"


# --- textos del documento ----------------------------------------------------
#
# Lo que el documento dice y depende del idioma. Las frases largas estan copiadas
# literalmente de las tres plantillas aprobadas: si el motor pone un texto por
# defecto, tiene que ser exactamente el que se aprobo, ni una coma distinta.

DOC_TEXTS = {
    "es-MX": {
        "incluido": "Incluido",
        "aria_verificar": "Verificar la reserva {folio}",
        "alt_barras": "Código de barras del folio {folio}",
        "alt_qr": "Código QR para verificar la reserva {folio}",
    },
    "en": {
        "incluido": "Included",
        "aria_verificar": "Verify reservation {folio}",
        "alt_barras": "Barcode for reference {folio}",
        "alt_qr": "QR code to verify reservation {folio}",
    },
    "es-AR": {
        "incluido": "Incluido",
        "aria_verificar": "Verificar la reserva {folio}",
        "alt_barras": "Código de barras del folio {folio}",
        "alt_qr": "Código QR para verificar la reserva {folio}",
    },
}


def doc_text(locale: str, clave: str) -> str:
    return DOC_TEXTS.get(locale, DOC_TEXTS[DEFAULT_LOCALE]).get(clave, "")


# Estado que ve el cliente en el documento. No es el mismo que el del panel:
# "PDF generado" y "Enviada" son estados internos de gestion y en el documento
# se traducen a lo unico que le importa al cliente, que es si el pago sigue
# pendiente. Un documento no debe dar por avanzado un paso que no lo esta.
DOC_STATUS = {
    "es-MX": {
        "draft": "Borrador",
        "pending": "Pago pendiente",
        "generated": "Pago pendiente",
        "sent": "Pago pendiente",
        "cancelled": "Cancelada",
    },
    "en": {
        "draft": "Draft",
        "pending": "Payment pending",
        "generated": "Payment pending",
        "sent": "Payment pending",
        "cancelled": "Cancelled",
    },
    "es-AR": {
        "draft": "Borrador",
        "pending": "Pago pendiente",
        "generated": "Pago pendiente",
        "sent": "Pago pendiente",
        "cancelled": "Cancelada",
    },
}


def status_text(status: str, locale: str) -> str:
    tabla = DOC_STATUS.get(locale, DOC_STATUS[DEFAULT_LOCALE])
    return tabla.get(status, tabla["pending"])


# Modalidades de entrega. El documento aprobado enseña las dos: la elegida
# arriba, con enlace, y la otra debajo. Los textos son los de las plantillas
# aprobadas, palabra por palabra, incluido el voseo argentino. Lo unico que
# decide el backend es cual de las dos va primero.
# Cada modalidad tiene dos redacciones, y no una: la misma frase no sirve
# arriba y abajo. "También puedes solicitar..." esta escrita como alternativa y
# leida en primera posicion suena mal. Las de "alternativa" para domicilio y las
# de "principal" para sede las decidio el cliente el 21-ago-2026; el resto son
# las del documento aprobado, palabra por palabra.
DELIVERY_TEXTS = {
    "es-MX": {
        "home": {
            "titulo": "Entrega a domicilio (transporte terrestre asegurado)",
            "principal": "Traslado asegurado hasta el domicilio registrado.",
            "alternativa": (
                "También puedes solicitar la entrega a domicilio mediante transporte "
                "terrestre asegurado hasta la dirección registrada."
            ),
        },
        "branch": {
            "titulo": "Entrega en una sede o concesionario cercano",
            "principal": (
                "La entrega se realizará en una sede o concesionario cercano, sin cargos "
                "adicionales. La disponibilidad, ubicación y los detalles de entrega se "
                "confirmarán durante la coordinación."
            ),
            "alternativa": (
                "También puedes solicitar la entrega en una sede o concesionario cercano, "
                "sin cargos adicionales. La disponibilidad, ubicación y los detalles de "
                "entrega se confirmarán durante la coordinación."
            ),
        },
    },
    "en": {
        "home": {
            "titulo": "Home delivery (insured ground transport)",
            "principal": "Insured transport to the registered address.",
            "alternativa": "You may also request insured home delivery to the registered address.",
        },
        "branch": {
            "titulo": "Delivery to a nearby branch or dealership",
            "principal": (
                "Delivery will be arranged at a nearby branch or dealership at no "
                "additional charge. Availability, location, and delivery details will be "
                "confirmed during coordination."
            ),
            "alternativa": (
                "You may also request delivery to a nearby available branch or dealership "
                "at no additional charge. Availability, location, and delivery details will "
                "be confirmed during coordination."
            ),
        },
    },
    "es-AR": {
        "home": {
            "titulo": "Entrega a domicilio (transporte terrestre asegurado)",
            "principal": "Traslado asegurado hasta el domicilio registrado.",
            "alternativa": (
                "También podés solicitar la entrega a domicilio mediante transporte "
                "terrestre asegurado hasta la dirección registrada."
            ),
        },
        "branch": {
            "titulo": "Entrega en una sede o concesionario cercano",
            "principal": (
                "La entrega se realizará en una sede o concesionario cercano, sin cargos "
                "adicionales. La disponibilidad, ubicación y los detalles de entrega se "
                "confirmarán durante la coordinación."
            ),
            "alternativa": (
                "También podés solicitar la entrega en una sede o concesionario cercano, "
                "sin cargos adicionales. La disponibilidad, ubicación y los detalles de "
                "entrega se confirmarán durante la coordinación."
            ),
        },
    },
}

DELIVERY_MODES = ("home", "branch")


def delivery_texts(locale: str) -> dict:
    return DELIVERY_TEXTS.get(locale, DELIVERY_TEXTS[DEFAULT_LOCALE])


def delivery_label(mode: str, locale: str) -> str:
    """Nombre de la modalidad para el desplegable del editor."""
    return delivery_texts(locale).get(mode, {}).get("titulo", mode)


# --- validaciones bancarias --------------------------------------------------
#
# Comprobar la longitud detecta un numero incompleto, pero no un digito mal
# tecleado. Ambos formatos llevan digitos de control precisamente para eso, y
# validarlos cuesta muy poco: cazan el error en el panel y no cuando el cliente
# intenta transferir.


def validate_clabe(clabe: str) -> tuple[bool, str]:
    """CLABE mexicana: 18 digitos, el ultimo es de control.

    Se ponderan los 17 primeros con 3, 7, 1 repetidos, se toma cada producto
    modulo 10, se suman, y el digito de control es (10 - suma % 10) % 10.
    """
    digits = "".join(ch for ch in (clabe or "") if ch.isdigit())
    if len(digits) != 18:
        return False, f"La CLABE debe tener 18 dígitos y tiene {len(digits)}."
    pesos = (3, 7, 1)
    suma = sum(int(d) * pesos[i % 3] % 10 for i, d in enumerate(digits[:17]))
    esperado = (10 - suma % 10) % 10
    if esperado != int(digits[17]):
        return False, "El dígito de control de la CLABE no cuadra. Revise el número."
    return True, ""


def validate_cbu(cbu: str) -> tuple[bool, str]:
    """CBU argentino: 22 digitos en dos bloques con un digito de control cada uno.

    Bloque 1: 8 digitos, ponderacion 7 1 3 9 sobre los 7 primeros.
    Bloque 2: 14 digitos, ponderacion 3 9 7 1 sobre los 13 primeros.
    """
    digits = "".join(ch for ch in (cbu or "") if ch.isdigit())
    if len(digits) != 22:
        return False, f"El CBU debe tener 22 dígitos y tiene {len(digits)}."

    b1, b2 = digits[:8], digits[8:]

    suma1 = sum(int(d) * w for d, w in zip(b1[:7], (7, 1, 3, 9)* 2))
    if (10 - suma1 % 10) % 10 != int(b1[7]):
        return False, "El dígito de control del primer bloque del CBU no cuadra."

    suma2 = sum(int(d) * w for d, w in zip(b2[:13], (3, 9, 7, 1) * 4))
    if (10 - suma2 % 10) % 10 != int(b2[13]):
        return False, "El dígito de control del segundo bloque del CBU no cuadra."

    return True, ""


def validate_account(number: str, locale: str) -> tuple[bool, str]:
    """Aplica la validacion que corresponda al mercado de la factura."""
    if get_market(locale).code == "es-AR":
        return validate_cbu(number)
    return validate_clabe(number)


def validate_vin(vin: str) -> tuple[bool, str]:
    """VIN: 17 caracteres. No se admiten I, O ni Q porque el estandar las
    excluye justamente para que no se confundan con 1 y 0."""
    v = (vin or "").strip().upper()
    if len(v) != 17:
        return False, f"El VIN debe tener 17 caracteres y tiene {len(v)}."
    invalidos = set(v) & {"I", "O", "Q"}
    if invalidos:
        return False, f"El VIN no puede contener {', '.join(sorted(invalidos))}."
    if not all(c.isalnum() for c in v):
        return False, "El VIN solo admite letras y números."
    return True, ""
