"""
Operaciones sobre facturas: crear, editar, guardar borrador y duplicar.

Aqui vive todo lo que decide *que* se guarda y *que* se copia. Las rutas de
main.py solo reciben el formulario y llaman a estas funciones, para que las
reglas esten en un sitio y no repartidas por las vistas.

Dos reglas que conviene no tocar sin pensarlo:

1. Duplicar no confirma una reserva. La copia nace siempre sin cliente, sin
   fechas y sin folio heredado. Lo que se copia esta declarado en
   DUPLICATE_CARRY_FIELDS y nada mas.
2. Los datos bancarios se copian desde Configuracion en el momento de crear la
   factura. Una factura ya emitida tiene que seguir enseñando la cuenta a la que
   se le pidio pagar al cliente, aunque manana se cambie en Configuracion.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .fields import DUPLICATE_CARRY_FIELDS, EDITABLE_FIELDS
from .locales import DEFAULT_LOCALE, MARKETS, validate_account, validate_vin
from .models import (
    STATUS_DRAFT,
    STATUS_GENERATED,
    STATUS_SENT,
    STATUSES,
    Invoice,
    Setting,
)

# Campos que se copian de Configuracion a la factura al crearla. La clave es la
# de Configuracion y la columna se obtiene cambiando el punto por un guion bajo,
# igual que en fields.py.
INHERITED_FROM_SETTINGS = (
    "banking.bank",
    "banking.beneficiary",
    "banking.account_label",
    "banking.account_number",
    "banking.bank_account",
    "representative.name",
    "representative.role",
    "representative.phone",
    "representative.email",
    "representative.hours",
)

# Campos que el formulario entrega como fecha y como importe. El resto son texto.
DATE_FIELDS = ("issue_date", "valid_until", "delivery_date")
AMOUNT_FIELDS = ("pricing_vehicle_price", "pricing_reservation_amount")

# Obligatorios para dejar de ser borrador. En borrador no se exige nada: la
# gracia de un borrador es poder guardarlo a medias.
REQUIRED_TO_LEAVE_DRAFT = (
    ("customer_name", "el nombre del cliente"),
    ("vehicle_title", "el vehículo"),
    ("vehicle_vin", "el VIN"),
    ("pricing_vehicle_price", "el precio del vehículo"),
    ("pricing_reservation_amount", "el importe de la pre-reserva"),
    ("issue_date", "la fecha de emisión"),
)

# Estados que significan que ese vehiculo ya tiene un compromiso en marcha. Se
# usan para avisar, no para bloquear: el cliente pidio permitir varias
# pre-facturas por VIN.
COMMITTED_STATUSES = (STATUS_GENERATED, STATUS_SENT)


# --- conversiones ------------------------------------------------------------


def parse_date(value: str | None) -> date | None:
    """El input type=date entrega siempre AAAA-MM-DD. Se acepta ademas el
    formato DD/MM/AAAA por si el dato llega pegado desde otro sitio."""
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value: str | None) -> Decimal | None:
    """Convierte lo que el operador teclee en un importe.

    Hay que aceptar las dos escrituras, porque el mismo panel sirve a Mexico y a
    Argentina: 329,000.00 y 329.000,00 son la misma cantidad. La regla es que el
    ultimo separador que aparece es el decimal; si solo hay uno y deja tres
    cifras detras, entonces era de miles.
    """
    text = (value or "").strip()
    if not text:
        return None
    text = text.replace("$", "").replace(" ", "").replace(" ", "")
    for code in {m.currency for m in MARKETS.values()}:
        text = text.replace(code, "")
    if not text:
        return None

    ultimo_punto, ultima_coma = text.rfind("."), text.rfind(",")
    if ultimo_punto >= 0 and ultima_coma >= 0:
        decimal_sep = "." if ultimo_punto > ultima_coma else ","
        miles_sep = "," if decimal_sep == "." else "."
        text = text.replace(miles_sep, "").replace(decimal_sep, ".")
    elif ultimo_punto >= 0 or ultima_coma >= 0:
        sep = "." if ultimo_punto >= 0 else ","
        posicion = ultimo_punto if ultimo_punto >= 0 else ultima_coma
        decimales = len(text) - posicion - 1
        # "329.000" son trescientos veintinueve mil, no 329 con tres decimales.
        if decimales == 3 and text.count(sep) >= 1:
            text = text.replace(sep, "")
        else:
            text = text.replace(sep, ".")

    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


# --- folios ------------------------------------------------------------------


def _setting(db: Session, key: str, market: str | None = None) -> Setting | None:
    return db.execute(
        select(Setting).where(Setting.key == key, Setting.market == market)
    ).scalar_one_or_none()


def next_folio(db: Session) -> str:
    """Siguiente folio libre, a partir del contador de Configuracion.

    Se comprueba ademas contra la base de datos: si alguien creo a mano una
    factura con ese folio, el contador se salta hasta encontrar uno libre. La
    columna folio es unica, asi que un choque seria un error 500 en la cara del
    operador.
    """
    prefijo = (_setting(db, "folio.prefix").value if _setting(db, "folio.prefix") else "") or "RES-"
    fila = _setting(db, "folio.next")
    try:
        numero = int((fila.value if fila else "1") or "1")
    except ValueError:
        numero = 1

    ancho = len(fila.value) if fila and fila.value.isdigit() else 5
    while True:
        candidato = f"{prefijo}{numero:0{ancho}d}"
        existe = db.execute(select(Invoice.id).where(Invoice.folio == candidato)).first()
        if not existe:
            break
        numero += 1

    if fila is None:
        fila = Setting(key="folio.next", market=None, value=str(numero), is_sensitive=False)
        db.add(fila)
    fila.value = f"{numero + 1:0{ancho}d}"
    return candidato


# --- lectura del formulario --------------------------------------------------


def apply_form(invoice: Invoice, form) -> None:
    """Vuelca el formulario sobre la factura, campo a campo y con conversion.

    Solo se tocan los campos de EDITABLE_FIELDS. Los bancarios no estan en esa
    lista a proposito: un operador no puede cambiarlos desde el editor, se
    heredan de Configuracion y solo se tocan tras pasar la Master Password.
    """
    for name in EDITABLE_FIELDS:
        if name not in form:
            continue
        crudo = form.get(name)
        if name in DATE_FIELDS:
            setattr(invoice, name, parse_date(crudo))
        elif name in AMOUNT_FIELDS:
            setattr(invoice, name, parse_amount(crudo))
        elif name == "locale":
            setattr(invoice, name, crudo if crudo in MARKETS else DEFAULT_LOCALE)
        elif name == "status":
            setattr(invoice, name, crudo if crudo in STATUSES else STATUS_DRAFT)
        elif name == "vehicle_vin":
            setattr(invoice, name, (crudo or "").strip().upper() or None)
        else:
            texto = (crudo or "").strip()
            setattr(invoice, name, texto or None)

    # El boton "Guardar borrador" manda save_as=draft y manda sobre el desplegable
    # de estado. Se resuelve aqui y no leyendo dos veces el campo status, que
    # dependeria del orden de los elementos en el HTML.
    if form.get("save_as") == STATUS_DRAFT:
        invoice.status = STATUS_DRAFT


def inherit_settings(db: Session, invoice: Invoice) -> None:
    """Copia banco y representante del mercado a la factura. Se llama al crear,
    nunca al editar: reeditar una factura no debe cambiarle la cuenta."""
    for key in INHERITED_FROM_SETTINGS:
        fila = _setting(db, key, invoice.locale)
        setattr(invoice, key.replace(".", "_"), fila.value if fila else None)
    if not invoice.banking_payment_reference:
        invoice.banking_payment_reference = invoice.folio
    if not invoice.verify_url_base:
        base = _setting(db, "qr.base_url")
        invoice.verify_url_base = base.value if base else None


# --- validacion --------------------------------------------------------------


def validate(invoice: Invoice) -> list[str]:
    """Errores que impiden guardar. La exigencia depende del estado: un borrador
    puede estar a medias, una factura que sale de borrador no."""
    errores: list[str] = []

    if invoice.vehicle_vin:
        ok, mensaje = validate_vin(invoice.vehicle_vin)
        if not ok:
            errores.append(mensaje)

    # La cuenta viene de Configuracion, pero se comprueba igual al guardar: si
    # alguien dejo una CLABE mal puesta, mejor enterarse aqui que cuando el
    # cliente intente transferir.
    if invoice.banking_account_number:
        ok, mensaje = validate_account(invoice.banking_account_number, invoice.locale)
        if not ok:
            errores.append(f"Cuenta de {invoice.locale}: {mensaje}")

    if invoice.issue_date and invoice.valid_until and invoice.valid_until < invoice.issue_date:
        errores.append("La vigencia no puede ser anterior a la fecha de emisión.")
    if invoice.issue_date and invoice.delivery_date and invoice.delivery_date < invoice.issue_date:
        errores.append("La fecha de entrega no puede ser anterior a la de emisión.")

    if invoice.status != STATUS_DRAFT:
        faltan = [
            etiqueta for campo, etiqueta in REQUIRED_TO_LEAVE_DRAFT if not getattr(invoice, campo)
        ]
        if faltan:
            errores.append(
                "Para dejar de ser borrador falta " + ", ".join(faltan) + "."
            )

    return errores


# --- agrupacion por VIN ------------------------------------------------------


def vin_history(db: Session, vin: str | None, exclude_id: int | None = None) -> list[Invoice]:
    """Todas las facturas de un mismo vehiculo, de la mas antigua a la mas
    reciente. Es el caso que le interesa al cliente: varios interesados por el
    mismo coche, cada uno con su pre-factura."""
    if not vin:
        return []
    stmt = select(Invoice).where(Invoice.vehicle_vin == vin).order_by(Invoice.created_at)
    if exclude_id is not None:
        stmt = stmt.where(Invoice.id != exclude_id)
    return list(db.execute(stmt).scalars().all())


def committed_siblings(db: Session, vin: str | None, exclude_id: int | None = None) -> list[Invoice]:
    """Facturas del mismo VIN que ya estan generadas o enviadas.

    Sirve para avisar antes de duplicar. El cliente pidio permitir varias
    pre-facturas por vehiculo, asi que esto advierte, no bloquea.
    """
    return [inv for inv in vin_history(db, vin, exclude_id) if inv.status in COMMITTED_STATUSES]


def vin_groups(db: Session) -> list[dict]:
    """Un registro por vehiculo, con su numero de facturas. Es la vista de
    historial por VIN."""
    filas = db.execute(
        select(
            Invoice.vehicle_vin,
            func.count(Invoice.id).label("n"),
            func.max(Invoice.updated_at).label("ultima"),
        )
        .where(Invoice.vehicle_vin.is_not(None))
        .group_by(Invoice.vehicle_vin)
        .order_by(func.count(Invoice.id).desc(), func.max(Invoice.updated_at).desc())
    ).all()

    grupos = []
    for vin, n, ultima in filas:
        facturas = vin_history(db, vin)
        grupos.append(
            {
                "vin": vin,
                "count": n,
                "ultima": ultima,
                "title": next((f.vehicle_title for f in facturas if f.vehicle_title), None),
                "locale": facturas[0].locale if facturas else DEFAULT_LOCALE,
                "invoices": facturas,
                "committed": [f for f in facturas if f.status in COMMITTED_STATUSES],
            }
        )
    return grupos


# --- alta y duplicado --------------------------------------------------------


def create(db: Session, form) -> tuple[Invoice | None, list[str]]:
    """Crea una factura a partir del formulario. No hace commit: de eso se
    encarga commit_creation, que ademas reintenta si el folio se ocupa."""
    invoice = Invoice(status=STATUS_DRAFT, locale=DEFAULT_LOCALE)
    apply_form(invoice, form)
    # El folio siempre lo pone el contador. No se lee del formulario ni aunque
    # llegue: el campo es de solo lectura en pantalla y ademas no esta en
    # EDITABLE_FIELDS, de modo que un envio manipulado tampoco lo cambia.
    invoice.folio = next_folio(db)
    inherit_settings(db, invoice)
    errores = validate(invoice)
    if errores:
        return None, errores

    db.add(invoice)
    return invoice, []


def duplicate(db: Session, source: Invoice, form=None) -> Invoice:
    """Copia una factura para otro interesado.

    Se copia lo declarado en DUPLICATE_CARRY_FIELDS: vehiculo, precios,
    plantilla y entrega. Todo lo demas se reinicia.

    Dos reglas estrictas, acordadas con el cliente:

    1. La copia nace SIEMPRE en borrador. No hay forma de que duplicar deje una
       factura en ningun otro estado. El operador abre la copia, la completa y
       decide desde el editor si la pasa a pago pendiente.
    2. Los datos bancarios y los del representante no se heredan del original:
       se cargan de la Configuracion vigente. Una copia es una operacion nueva y
       no puede arrastrar una cuenta que quiza ya se cambio.
    """
    copia = Invoice()
    for name in DUPLICATE_CARRY_FIELDS:
        setattr(copia, name, getattr(source, name))

    copia.status = STATUS_DRAFT
    copia.duplicated_from_id = source.id
    # Cliente, fechas y autorizacion se capturan de nuevo. No se heredan.
    copia.customer_name = None
    copia.customer_email = None
    copia.customer_phone = None
    copia.customer_city = None
    copia.issue_date = None
    copia.valid_until = None
    copia.delivery_date = None
    copia.authorization = None

    if form is not None:
        for name in ("customer_name", "customer_email", "customer_phone", "customer_city"):
            valor = (form.get(name) or "").strip()
            if valor:
                setattr(copia, name, valor)
        locale = form.get("locale")
        if locale in MARKETS:
            copia.locale = locale

    copia.folio = next_folio(db)
    copia.banking_payment_reference = copia.folio
    # Se llama despues de fijar el mercado: la cuenta depende de el, y la de
    # Mexico no sirve para una factura argentina.
    inherit_settings(db, copia)

    db.add(copia)
    return copia


# --- guardado con reintento de folio -----------------------------------------


class FolioOcupado(Exception):
    """No se ha conseguido un folio libre tras varios intentos."""


def _es_choque_de_folio(exc: IntegrityError) -> bool:
    """Distingue el folio repetido de cualquier otro error de integridad.

    No basta con buscar la palabra "folio": un NOT NULL sobre esa misma columna
    tambien la lleva, y reintentarlo seis veces solo serviria para esconder el
    fallo. Se exige ademas que el motor este hablando de una clave duplicada.

      sqlite      UNIQUE constraint failed: invoice.folio
      postgres    duplicate key value violates unique constraint "invoice_folio_key"
      mysql       Duplicate entry 'RES-87241' for key 'invoice.folio'
    """
    mensaje = str(getattr(exc, "orig", exc)).lower()
    return "folio" in mensaje and ("unique" in mensaje or "duplicate" in mensaje)


def commit_creation(db: Session, construir, intentos: int = 6) -> Invoice:
    """Crea y guarda reintentando si otro operador se lleva el folio primero.

    La cuenta de Admin es compartida, asi que dos personas pueden crear una
    factura casi a la vez. La columna folio es unica, de modo que el segundo en
    llegar recibiria un error de base de datos en plena pantalla.

    Aqui se captura ese choque, se deshace la transaccion y se vuelve a
    construir la factura. Como next_folio comprueba contra la base de datos
    antes de proponer un numero, el reintento coge ya el siguiente libre y
    converge en la primera vuelta.

    construir(db) -> Invoice, ya anadida a la sesion y con su folio puesto.
    """
    for _ in range(intentos):
        invoice = construir(db)
        try:
            db.commit()
            return invoice
        except IntegrityError as exc:
            db.rollback()
            if not _es_choque_de_folio(exc):
                raise
    raise FolioOcupado("No se ha podido asignar un folio libre.")
