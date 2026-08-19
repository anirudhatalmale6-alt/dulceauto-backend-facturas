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
        account_label="Interbank account number",
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


def format_amount(value, locale: str, with_currency: bool = True) -> str:
    """329000 -> '$329,000.00 MXN' en Mexico y '$329.000,00 ARS' en Argentina."""
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
    return f"{texto} {market.currency}" if with_currency else texto


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
