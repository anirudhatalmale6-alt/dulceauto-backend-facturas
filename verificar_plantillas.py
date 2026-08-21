"""
Comprobacion del motor de plantillas (Fase C).

Se ejecuta sin servidor y sin tocar la base de datos: se construyen facturas en
memoria y se mira el HTML que sale.

La comprobacion principal es la primera y es la que de verdad importa: se monta
una factura con exactamente los mismos datos que lleva la version aprobada y se
exige que el documento generado sea **identico byte a byte** al archivo
aprobado. Si el motor cambiara un espacio, una comilla o una etiqueta, esa
comprobacion falla. Es la unica manera de poder afirmar que el diseno no se ha
tocado sin depender de mirar capturas.

    ./.venv/bin/python verificar_plantillas.py
"""
import re
import sys
from datetime import date, datetime
from pathlib import Path

from app import documents
from app.documents import ASSETS_PANEL, ASSETS_ORIGEN
from app.locales import (
    format_amount,
    format_date_long,
    format_date_numeric,
    format_date_short,
    status_text,
)
from app.models import Invoice

ok, fallos = [], []


def check(nombre, condicion, extra=""):
    (ok if condicion else fallos).append(nombre)
    print(("  OK   " if condicion else "  FALLA") + f" {nombre}" + (f"  [{extra}]" if extra else ""))


def sin_rutas(html: str) -> str:
    """Deshace la reescritura de rutas para poder comparar con el archivo
    aprobado, que las tiene relativas."""
    return html.replace(ASSETS_PANEL, ASSETS_ORIGEN)


COMUN = dict(
    folio="RES-87241",
    status="pending",
    issue_date=date(2026, 7, 22),
    valid_until=date(2026, 7, 29),
    authorization="AUT-2026-87241",
    customer_name="Juan Pérez García",
    customer_email="juan.perez@gmail.com",
    vehicle_title="2015 Audi A3 1.8T S Line Convertible AT",
    vehicle_vin="19UTC2895KL500992",
    vehicle_year="2015",
    vehicle_mileage="16,678 km",
    pricing_vehicle_price=329000,
    pricing_reservation_amount=3240,
    banking_bank_account="1234567890",
    delivery_date=date(2026, 7, 27),
    delivery_mode="home",
    representative_name="Yoselina de la Cruz",
    representative_hours="Lunes a viernes, 8:00 a. m.–4:00 p. m.",
)

# Los datos de la version aprobada de cada mercado, tal y como estarian en la
# base de datos: fechas como fechas e importes como numeros, no como texto.
MUESTRAS = {
    "es-MX": dict(
        COMUN,
        locale="es-MX",
        customer_phone="55 1234 5678",
        customer_city="Veracruz",
        vehicle_location="Ciudad de México",
        vehicle_type="Convertible",
        vehicle_fuel="Gasolina",
        vehicle_transmission="Automática",
        pricing_discount="9% DE DESCUENTO APLICADO",
        pricing_currency="MXN",
        banking_bank="BBVA México",
        banking_beneficiary="DulceAuto México S.A. de C.V.",
        banking_account_number="012345678901234567",
        representative_role="Representante de operaciones",
        representative_phone="+52 55 9876 5432",
        representative_email="soporte@dulceauto.mx",
        verify_url_base="https://dulceauto.mx/verificar/",
    ),
    "en": dict(
        COMUN,
        locale="en",
        customer_phone="55 1234 5678",
        customer_city="Veracruz",
        vehicle_location="Mexico City",
        vehicle_type="Convertible",
        vehicle_fuel="Gasoline",
        vehicle_transmission="Automatic",
        pricing_discount="9% DISCOUNT APPLIED",
        pricing_currency="MXN",
        banking_bank="BBVA México",
        banking_beneficiary="DulceAuto México S.A. de C.V.",
        banking_account_number="012345678901234567",
        representative_role="Operations representative",
        representative_phone="+52 55 9876 5432",
        representative_email="soporte@dulceauto.mx",
        representative_hours="Monday to Friday, 8:00 a.m.–4:00 p.m.",
        verify_url_base="https://dulceauto.mx/verificar/",
    ),
    "es-AR": dict(
        COMUN,
        locale="es-AR",
        customer_phone="11 1234 5678",
        customer_city="Córdoba",
        vehicle_location="Buenos Aires",
        vehicle_type="Convertible",
        vehicle_fuel="Nafta",
        vehicle_transmission="Automática",
        pricing_discount="9% DE DESCUENTO APLICADO",
        pricing_currency="ARS",
        banking_bank="BBVA Argentina",
        banking_beneficiary="DulceAuto Argentina S.A.",
        banking_account_number="0123456789012345678901",
        representative_role="Representante de operaciones",
        representative_phone="+54 11 9876 5432",
        representative_email="soporte@dulceauto.com.ar",
        # El archivo aprobado en es-AR mantiene el dominio .mx en el QR.
        verify_url_base="https://dulceauto.mx/verificar/",
    ),
}


def factura(locale, **cambios):
    datos = dict(MUESTRAS[locale])
    datos.update(cambios)
    return Invoice(**datos)


def aprobado(locale) -> str:
    return (documents.TEMPLATES_DIR / documents.get_market(locale).template).read_text(
        encoding="utf-8"
    )


# --- 1 · el documento generado es el aprobado ---------------------------------

print("\n1 · Con los datos de la version aprobada sale el archivo aprobado")
for locale in MUESTRAS:
    generado = sin_rutas(documents.render(factura(locale)).html)
    esperado = aprobado(locale)
    igual = generado == esperado
    extra = ""
    if not igual:
        distintas = [
            (a, b)
            for a, b in zip(esperado.splitlines(), generado.splitlines())
            if a != b
        ]
        extra = f"{len(distintas)} lineas distintas; primera: {distintas[0][1][:90] if distintas else '?'}"
    check(f"{locale}: identico byte a byte al archivo aprobado", igual, extra)


# --- 2 · las plantillas solo llevan atributos anadidos ------------------------

print("\n2 · A la plantilla aprobada solo se le han anadido atributos")
QUITAR = re.compile(r' data-(field|step|hide-if-empty)="[^"]*"| data-logo\b')
for locale in MUESTRAS:
    rel = documents.get_market(locale).template
    marcada = QUITAR.sub("", (documents.TEMPLATES_DIR / rel).read_text(encoding="utf-8"))
    original = QUITAR.sub(
        "", (documents.TEMPLATES_DIR / "aprobado-original" / rel).read_text(encoding="utf-8")
    )
    check(f"{locale}: quitando los data-* queda el original", marcada == original)


# --- 3 · formatos por mercado -------------------------------------------------

print("\n3 · Formatos de fecha por mercado")
check("es-MX: 12 de agosto en corto", format_date_short(date(2026, 8, 12), "es-MX") == "12 Ago 2026",
      format_date_short(date(2026, 8, 12), "es-MX"))
check("en: 12 de agosto en corto", format_date_short(date(2026, 8, 12), "en") == "12 Aug 2026",
      format_date_short(date(2026, 8, 12), "en"))
check("es-AR: fecha larga en espanol", format_date_long(date(2026, 7, 27), "es-AR") == "27 de julio de 2026",
      format_date_long(date(2026, 7, 27), "es-AR"))
check("en: fecha larga en ingles", format_date_long(date(2026, 7, 27), "en") == "27 July 2026",
      format_date_long(date(2026, 7, 27), "en"))
check("vigencia siempre dd/mm/aaaa", format_date_numeric(date(2026, 7, 29)) == "29/07/2026",
      format_date_numeric(date(2026, 7, 29)))
# El mes no puede depender del idioma que tenga instalado el servidor.
check("los meses no vienen del sistema", "MESES" in dir(__import__("app.locales", fromlist=["MESES"])))

print("\n4 · Formatos de importe por mercado")
check("Mexico separa con coma", format_amount(329000, "es-MX") == "$329,000.00 MXN",
      format_amount(329000, "es-MX"))
check("Argentina separa con punto", format_amount(329000, "es-AR") == "$329.000,00 ARS",
      format_amount(329000, "es-AR"))
check("la moneda de la factura manda sobre la del mercado",
      format_amount(1000, "es-AR", currency="MXN") == "$1.000,00 MXN",
      format_amount(1000, "es-AR", currency="MXN"))
doc = documents.render(factura("es-AR"))
check("en el documento argentino el importe va con punto",
      ">$329.000,00 ARS<" in doc.html)
check("y la pre-reserva sin moneda al lado", ">$3.240,00<" in doc.html)


# --- 5 · estado y barra de progreso -------------------------------------------

print("\n5 · El estado que ve el cliente")
check("es-MX pendiente", status_text("pending", "es-MX") == "Pago pendiente")
check("en borrador", status_text("draft", "en") == "Draft")
check("es-AR cancelada", status_text("cancelled", "es-AR") == "Cancelada")

check("es-MX pago validado", status_text("payment_validated", "es-MX") == "Pago validado")
check("en pago validado", status_text("payment_validated", "en") == "Payment verified")
check("en entrega coordinada", status_text("delivery_scheduled", "en") == "Delivery scheduled")
# El estado se llama "Entregada" por dentro y "Entrega completada" en la
# pastilla: lo decidio el cliente, porque es lo que lee el comprador.
check("es-MX entregada se lee como entrega completada",
      status_text("delivered", "es-MX") == "Entrega completada")
check("en entregada", status_text("delivered", "en") == "Delivery completed")

html_pendiente = documents.render(factura("es-MX", status="pending")).html
html_borrador = documents.render(factura("es-MX", status="draft")).html
html_validado = documents.render(factura("es-MX", status="payment_validated")).html
html_coordinada = documents.render(factura("es-MX", status="delivery_scheduled")).html
html_entregada = documents.render(factura("es-MX", status="delivered")).html
html_cancelada = documents.render(factura("es-MX", status="cancelled")).html


def clase_paso(html, paso):
    m = re.search(r'<div class="([^"]*)"[^>]*data-step="%d"' % paso, html)
    return m.group(1) if m else "?"


def barra(html):
    return [clase_paso(html, n).replace("step", "").strip() or "-" for n in (1, 2, 3, 4)]


check("borrador: solo el paso 1 activo", barra(html_borrador) == ["active", "-", "-", "-"],
      barra(html_borrador))
check("pago pendiente: 1 hecho, 2 activo", barra(html_pendiente) == ["done", "active", "-", "-"],
      barra(html_pendiente))
check("pago validado: 1 y 2 hechos, 3 activo",
      barra(html_validado) == ["done", "done", "active", "-"], barra(html_validado))
check("entrega coordinada: 1, 2 y 3 hechos, 4 activo",
      barra(html_coordinada) == ["done", "done", "done", "active"], barra(html_coordinada))
check("entregada: los cuatro hechos",
      barra(html_entregada) == ["done", "done", "done", "done"], barra(html_entregada))
check("cancelada: 1 hecho y ninguno activo",
      barra(html_cancelada) == ["done", "-", "-", "-"], barra(html_cancelada))

# El paso 3 conserva el nombre del documento aprobado. El cliente pidio
# expresamente no cambiarlo por "Pago validado".
check("el paso 3 sigue llamandose como en el documento aprobado",
      "Documentación y trámites" in html_validado)

# Generar el PDF o enviarlo son acciones nuestras: no son estados y no pueden
# mover la barra ni cambiar lo que lee el cliente.
con_pdf = documents.render(
    factura("es-MX", status="pending",
            pdf_generated_at=datetime(2026, 8, 21, 10, 0),
            sent_at=datetime(2026, 8, 21, 10, 5))
).html
check("generar y enviar el PDF no mueven la barra", barra(con_pdf) == barra(html_pendiente),
      barra(con_pdf))
check("ni cambian lo que lee el cliente", ">Pago pendiente<" in con_pdf)
check("el documento sale igual con PDF hecho que sin el", con_pdf == html_pendiente)
check("el borrador se dice, no se disimula", ">Borrador<" in html_borrador)


# --- 5bis · titular y linea de debajo ------------------------------------------

print("\n5bis · Titular y linea de debajo, por estado")
from app.locales import DOC_HEADLINE, headline  # noqa: E402

# En "Pago pendiente" tienen que ser EXACTAMENTE los textos aprobados. No se
# comparan contra lo que yo haya escrito: se leen del archivo aprobado.
for locale in MUESTRAS:
    original = (documents.TEMPLATES_DIR / "aprobado-original" / documents.get_market(locale).template).read_text(encoding="utf-8")
    titular_aprobado = re.search(r'<div class="intro-top">\s*\n\s*<h2>([^<]*)</h2>', original).group(1)
    texto_aprobado = re.search(r'<div class="intro-sub">\s*\n\s*<p>([^<]*)</p>', original).group(1)
    t, x = headline("pending", locale)
    check(f"{locale}: en pago pendiente, el titular es el aprobado", t == titular_aprobado,
          f"{t!r} vs {titular_aprobado!r}")
    check(f"{locale}: y la linea de debajo tambien", x == texto_aprobado,
          f"{x[:40]!r} vs {texto_aprobado[:40]!r}")

entregada = documents.render(factura("es-MX", status="delivered")).html
check("entregada: el titular cambia", ">Entrega completada</h2>" in entregada)
check("y ya no pide confirmar el pago", "Confirma el pago" not in entregada)
check("la linea de debajo es la nueva",
      "La recepción del vehículo ha sido confirmada correctamente." in entregada)

cancelada = documents.render(factura("es-MX", status="cancelled")).html
check("cancelada lo dice en el titular", ">Operación cancelada</h2>" in cancelada)

borrador = documents.render(factura("es-MX", status="draft")).html
check("un borrador se anuncia como tal", ">Pre-factura en preparación</h2>" in borrador)

ar = documents.render(factura("es-AR", status="draft")).html
check("Argentina usa el voseo en el texto nuevo", "Completá los datos requeridos" in ar)
check("y no el 'completa' mexicano", "Completa los datos requeridos" not in ar)

en_val = documents.render(factura("en", status="payment_validated")).html
check("en ingles, pago validado", ">Your payment has been verified</h2>" in en_val)
check("con su linea", "documentation and processing required" in en_val)

# Los seis estados tienen texto en los tres mercados: si manana se anade uno,
# esta comprobacion lo caza antes de que salga una factura con el titular vacio.
from app.models import STATUSES  # noqa: E402

faltan = [
    f"{loc}/{est}" for loc in DOC_HEADLINE for est in STATUSES if est not in DOC_HEADLINE[loc]
]
check("los seis estados tienen titular en los tres mercados", not faltan, ", ".join(faltan))


# --- 6 · modalidad de entrega -------------------------------------------------

print("\n6 · Modalidad de entrega")
casa = documents.render(factura("es-MX", delivery_mode="home")).html
sede = documents.render(factura("es-MX", delivery_mode="branch")).html
check("a domicilio: la modalidad enlazada es la de domicilio",
      'data-field="entrega_modalidad">Entrega a domicilio (transporte terrestre asegurado)</a>' in casa)
check("en sede: la modalidad enlazada es la de sede",
      'data-field="entrega_modalidad">Entrega en una sede o concesionario cercano</a>' in sede)
check("y entonces la alternativa es la de domicilio",
      'data-field="entrega_alternativa">Entrega a domicilio (transporte terrestre asegurado)</strong>' in sede)
check("con domicilio arriba, el texto es el aprobado, sin reescribir",
      "Traslado asegurado hasta el domicilio registrado." in casa)

# Cada modalidad tiene dos redacciones. La que empieza por "También puedes
# solicitar" esta escrita como alternativa: arriba no puede aparecer nunca.
principal_sede = sede.split('data-field="entrega_texto">')[1].split("</p>")[0]
alternativa_casa = sede.split('data-field="entrega_alternativa_texto">')[1].split("</p>")[0]
check("con la sede arriba, su texto no empieza por 'También'",
      not principal_sede.strip().startswith("También"), principal_sede[:60])
check("y usa la redaccion de modalidad principal",
      principal_sede.startswith("La entrega se realizará en una sede"), principal_sede[:60])
check("el domicilio, ya como alternativa, cambia de redaccion",
      alternativa_casa.startswith("También puedes solicitar la entrega a domicilio"),
      alternativa_casa[:60])
check("ninguna de las dos frases se repite en el mismo documento",
      principal_sede != alternativa_casa)

sede_en = documents.render(factura("en", delivery_mode="branch")).html
check("en ingles, la sede arriba usa 'Delivery will be arranged'",
      "Delivery will be arranged at a nearby branch" in sede_en)
check("y el domicilio como alternativa, 'You may also request insured home delivery'",
      "You may also request insured home delivery to the registered address." in sede_en)

sede_ar = documents.render(factura("es-AR", delivery_mode="branch")).html
check("el voseo argentino se respeta",
      "También podés solicitar" in documents.render(factura("es-AR")).html)
check("y tambien en la alternativa nueva",
      "También podés solicitar la entrega a domicilio" in sede_ar)
check("Argentina no usa el 'puedes' mexicano",
      "También puedes solicitar" not in sede_ar)
check("el ingles no se cuela en la version argentina",
      "You may also request" not in sede_ar)
propio = documents.render(
    factura("es-MX", delivery_text="Entrega en 48 horas en su domicilio.")
).html
check("un texto escrito a mano sustituye al de la plantilla",
      "Entrega en 48 horas en su domicilio." in propio
      and "Traslado asegurado hasta el domicilio registrado." not in propio)
raro = documents.render(factura("es-MX", delivery_mode="lo-que-sea")).html
check("una modalidad desconocida no rompe el documento",
      'data-field="entrega_modalidad">Entrega a domicilio' in raro)


# --- 7 · huecos vacios --------------------------------------------------------

print("\n7 · Lo que falta se queda en blanco, nunca con el dato de ejemplo")
vacia = documents.render(
    factura("es-MX", customer_name=None, customer_email=None, customer_city=None)
)
check("no queda ni rastro del cliente de la maqueta", "Juan Pérez García" not in vacia.html)
check("el hueco se queda vacio", 'data-field="cliente_nombre"></span>' in vacia.html)
check("y se avisa de que falta", "cliente_nombre" in vacia.vacios and "cliente_email" in vacia.vacios)
check("el pie legal tambien se queda sin nombre",
      'data-field="cliente_nombre"></strong>' in vacia.html)

sin_descuento = documents.render(factura("es-MX", pricing_discount=None))
pill = re.search(r'<span class="discount-pill"[^>]*>', sin_descuento.html).group(0)
check("sin descuento, la pastilla desaparece", 'style="display:none"' in pill, pill)
check("y no se cuenta como dato que falte", "descuento" not in sin_descuento.vacios)
check("con descuento, la pastilla se ve",
      'class="discount-pill" data-hide-if-empty' in documents.render(factura("es-MX")).html)


# --- 8 · folio, verificacion y mayusculas -------------------------------------

print("\n8 · Folio, URL de verificacion y mayusculas")
otra = documents.render(factura("es-MX", folio="RES-90001"))
check("el folio se cambia en los tres sitios donde sale",
      otra.html.count("RES-90001") >= 3 and "RES-87241" not in otra.html,
      f"{otra.html.count('RES-90001')} apariciones")
check("la URL de verificacion lleva ese folio",
      'href="https://dulceauto.mx/verificar/RES-90001"' in otra.html)
check("el alt del codigo de barras tambien",
      'alt="Código de barras del folio RES-90001"' in otra.html)
check("y el del QR", 'alt="Código QR para verificar la reserva RES-90001"' in otra.html)
check("en ingles el alt esta en ingles",
      'alt="Barcode for reference RES-87241"' in documents.render(factura("en")).html)
sin_url = documents.render(factura("es-MX", verify_url_base=None))
check("sin URL configurada no se inventa un enlace", 'href=""' in sin_url.html)

minusculas = documents.render(factura("es-MX", pricing_discount="9% de descuento aplicado")).html
check("la pastilla del descuento respeta la mayuscula del diseno",
      ">9% DE DESCUENTO APLICADO<" in minusculas)
check("pero el nombre del cliente no se pone en mayusculas",
      ">Juan Pérez García<" in minusculas)


# --- 9 · seguridad del texto --------------------------------------------------

print("\n9 · El texto del operador no puede romper el documento")
malicia = documents.render(
    factura("es-MX", customer_name='<script>alert(1)</script>', vehicle_location='Ciudad & "Puerto"')
).html
check("una etiqueta escrita en un campo sale como texto",
      "&lt;script&gt;" in malicia and "<script>alert(1)</script>" not in malicia)
check("los ampersand y las comillas se escapan", "Ciudad &amp; &quot;Puerto&quot;" in malicia
      or "Ciudad &amp; \"Puerto\"" in malicia)
check("y el documento sigue teniendo su estructura",
      malicia.count("<article class=\"invoice\">") == 1)


# --- 10 · iniciales y detalles ------------------------------------------------

print("\n10 · Detalles")
check("las particulas no cuentan para las iniciales",
      documents.iniciales("Yoselina de la Cruz") == "YC", documents.iniciales("Yoselina de la Cruz"))
check("un solo nombre da dos letras", documents.iniciales("Madonna") == "MA")
check("sin representante, sin iniciales", documents.iniciales(None) == "")
check("los tres archivos tienen los mismos huecos",
      documents.huecos_de("es-MX") == documents.huecos_de("en") == documents.huecos_de("es-AR"),
      f"{len(documents.huecos_de('es-MX'))} huecos")
check("y hay una etiqueta legible para cada uno",
      all(h in documents.ETIQUETAS_HUECO for h in documents.huecos_de("es-MX")),
      ", ".join(h for h in documents.huecos_de("es-MX") if h not in documents.ETIQUETAS_HUECO))
sin_hueco = dict(documents.campos_sin_hueco("es-MX"))
check("las claves que no llegan al documento estan declaradas con su motivo",
      "vehicle.carfax" in sin_hueco and sin_hueco["vehicle.carfax"],
      ", ".join(sin_hueco))
# La etiqueta de la cuenta que ensena el panel tiene que decir lo mismo que la
# plantilla aprobada. La inglesa decia "Interbank account number" mientras el
# documento ponia "CLABE (18 digits)".
from app.locales import MARKETS  # noqa: E402

for locale, marca in (("es-MX", "CLABE"), ("en", "CLABE (18 digits)"), ("es-AR", "CBU")):
    check(f"{locale}: la etiqueta de la cuenta del panel concuerda con la plantilla",
          marca.lower() in MARKETS[locale].account_label.lower()
          and MARKETS[locale].account_label.split(" (")[0].lower() in aprobado(locale).lower(),
          MARKETS[locale].account_label)

check("las rutas de los archivos se reescriben para el panel",
      ASSETS_PANEL + "css/factura.css" in documents.render(factura("es-MX")).html)
check("y no queda ninguna ruta relativa suelta",
      ASSETS_ORIGEN not in documents.render(factura("es-MX")).html)


print(f"\n{'=' * 58}\n{len(ok)} comprobaciones correctas, {len(fallos)} fallos")
for f in fallos:
    print(f"  FALLA: {f}")
sys.exit(1 if fallos else 0)
