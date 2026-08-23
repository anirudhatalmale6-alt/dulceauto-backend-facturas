"""
Operaciones sobre facturas: crear, editar, guardar borrador y duplicar.

Aqui vive todo lo que decide *que* se guarda y *que* se copia. Las rutas de
main.py solo reciben el formulario y llaman a estas funciones, para que las
reglas esten en un sitio y no repartidas por las vistas.

Dos reglas que conviene no tocar sin pensarlo:

1. Duplicar no confirma una reserva. La copia nace siempre sin heredar los datos
   del cliente original, sin fechas y sin folio heredado; los datos del nuevo
   interesado, si se escriben, llegan del formulario de duplicar. Lo que se
   copia de la factura de origen esta declarado en DUPLICATE_CARRY_FIELDS y
   nada mas.
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
    COMMITTED_STATUSES,
    FOLIO_AUTO,
    FOLIO_MANUAL,
    STATUS_CANCELLED,
    STATUS_DRAFT,
    STATUSES,
    BrandProfile,
    FolioLedger,
    Invoice,
    InvoiceSnapshot,
    Setting,
    utcnow,
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

# Que estados comprometen el vehiculo se declara en models.py, junto a los
# propios estados, para que no se puedan quedar descolgados el dia que se anada
# uno nuevo. Se importan aqui y se re-exportan porque el resto del codigo los
# venia pidiendo a este modulo.
#
# Se usan para avisar, no para bloquear: el cliente pidio permitir varias
# pre-facturas por VIN.


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


def folio_prefijo(db: Session) -> str:
    fila = _setting(db, "folio.prefix")
    return (fila.value if fila else "") or "RES-"


def folio_ancho(db: Session) -> int:
    """Cuantos digitos lleva el folio, deducido del propio contador."""
    fila = _setting(db, "folio.next")
    return len(fila.value) if fila and fila.value and fila.value.isdigit() else 5


def folio_ocupado(db: Session, folio: str) -> bool:
    """Si ese folio esta en uso o lo estuvo alguna vez.

    Las dos preguntas importan y son distintas. La tabla de facturas dice si hay
    una viva con ese numero; el registro dice si existio alguna vez, aunque su
    factura se haya borrado despues. Un folio que aparezca en cualquiera de las
    dos no se puede volver a emitir.
    """
    if db.execute(select(Invoice.id).where(Invoice.folio == folio)).first():
        return True
    return bool(db.execute(select(FolioLedger.folio).where(FolioLedger.folio == folio)).first())


def anotar_folio(
    db: Session, folio: str, invoice_id: int | None = None, source: str = FOLIO_AUTO
) -> None:
    """Deja el folio anotado para siempre en el registro.

    Se llama dentro de la misma transaccion que crea la factura, a proposito: si
    la creacion se deshace, la anotacion se deshace con ella y el folio sigue
    libre, que es lo correcto porque esa factura nunca existio. En cuanto la
    factura se guarda de verdad, la anotacion queda y ya no la borra nadie.
    """
    if db.get(FolioLedger, folio) is None:
        db.add(FolioLedger(folio=folio, invoice_id=invoice_id, source=source))


def next_folio(db: Session) -> str:
    """Siguiente folio libre, a partir del contador de Configuracion.

    Se comprueba ademas contra la base de datos: si alguien creo a mano una
    factura con ese folio, el contador se salta hasta encontrar uno libre. La
    columna folio es unica, asi que un choque seria un error 500 en la cara del
    operador.

    La comprobacion incluye el registro permanente de folios, de modo que
    tampoco se propone uno que existio y cuya factura ya se borro.
    """
    prefijo = folio_prefijo(db)
    fila = _setting(db, "folio.next")
    try:
        numero = int((fila.value if fila else "1") or "1")
    except ValueError:
        numero = 1

    ancho = folio_ancho(db)
    while True:
        candidato = f"{prefijo}{numero:0{ancho}d}"
        if not folio_ocupado(db, candidato):
            break
        numero += 1

    if fila is None:
        fila = Setting(key="folio.next", market=None, value=str(numero), is_sensitive=False)
        db.add(fila)
    fila.value = f"{numero + 1:0{ancho}d}"
    return candidato


def folio_previsto(db: Session) -> str:
    """Que folio tocaria ahora mismo, SIN consumirlo ni tocar el contador.

    Solo sirve para enseñarlo en pantalla. Se separa de next_folio a proposito:
    next_folio avanza el contador, y llamarlo solo para pintar una pantalla
    obligaba a deshacer la transaccion a media peticion.
    """
    prefijo = folio_prefijo(db)
    ancho = folio_ancho(db)
    fila = _setting(db, "folio.next")
    try:
        numero = int((fila.value if fila else "1") or "1")
    except ValueError:
        numero = 1
    while folio_ocupado(db, f"{prefijo}{numero:0{ancho}d}"):
        numero += 1
    return f"{prefijo}{numero:0{ancho}d}"


class FolioManualInvalido(Exception):
    """El folio escrito a mano no sirve, y hay que decirlo en voz alta."""


def normalizar_folio_manual(db: Session, texto: str) -> str:
    """Convierte lo que el Admin escribe en un folio valido, o falla.

    Se acepta tanto el folio entero (RES-95000) como solo el numero (95000),
    porque las dos formas son naturales al teclear. Lo que no se acepta es otro
    prefijo: el prefijo lo manda Configuracion.

    Falla con excepcion y nunca devuelve "algo parecido". Un folio manual que se
    corrige solo seria peor que un error: el Admin creeria haber emitido un
    numero y estaria guardado con otro.
    """
    prefijo = folio_prefijo(db)
    ancho = folio_ancho(db)
    escrito = (texto or "").strip().upper()
    if not escrito:
        raise FolioManualInvalido("Escribe el folio o déjalo en modo Automático.")

    cuerpo = escrito
    if cuerpo.startswith(prefijo.upper()):
        cuerpo = cuerpo[len(prefijo):]
    cuerpo = cuerpo.strip()

    if not cuerpo.isdigit():
        raise FolioManualInvalido(
            f"El folio tiene que ser {prefijo} seguido de números. Recibido: «{escrito}»."
        )

    numero = int(cuerpo)
    if numero <= 0:
        raise FolioManualInvalido("El número del folio tiene que ser mayor que cero.")

    folio = f"{prefijo}{numero:0{ancho}d}"
    if folio_ocupado(db, folio):
        raise FolioManualInvalido(
            f"El folio {folio} ya se ha usado. Un folio no se reutiliza nunca, "
            "ni aunque su factura se haya cancelado o eliminado."
        )
    return folio


def avanzar_contador_tras_manual(db: Session, folio: str) -> bool:
    """Deja el contador por delante de un folio puesto a mano.

    La regla que pidio el cliente: si el siguiente automatico era RES-90004 y a
    mano se emite RES-95000, el siguiente automatico pasa a RES-95001, para no
    llegar mas adelante a un numero ya usado. Si el folio manual es INFERIOR al
    contador, el contador no retrocede.

    Solo se mueve si el folio manual respeta el prefijo y el numero de digitos
    configurados. Con otro formato no hay forma de compararlo con el contador
    sin inventarse una regla, asi que se deja quieto: el registro de folios ya
    impide que ese numero se reutilice, de modo que no hacer nada es seguro.

    Devuelve True si el contador se ha movido.
    """
    prefijo = folio_prefijo(db)
    ancho = folio_ancho(db)
    if not folio.startswith(prefijo):
        return False
    cuerpo = folio[len(prefijo):]
    if not cuerpo.isdigit() or len(cuerpo) != ancho:
        return False

    fila = _setting(db, "folio.next")
    try:
        actual = int((fila.value if fila else "1") or "1")
    except ValueError:
        actual = 1

    siguiente = int(cuerpo) + 1
    if siguiente <= actual:
        return False
    if fila is None:
        fila = Setting(key="folio.next", market=None, value=str(siguiente), is_sensitive=False)
        db.add(fila)
    fila.value = f"{siguiente:0{ancho}d}"
    return True


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


# --- marca -------------------------------------------------------------------


def perfiles_activos(db: Session) -> list[BrandProfile]:
    """Los perfiles que se pueden elegir al crear o editar una factura."""
    return list(
        db.execute(
            select(BrandProfile).where(BrandProfile.is_active.is_(True)).order_by(BrandProfile.name)
        ).scalars()
    )


def perfil_por_defecto(db: Session) -> BrandProfile | None:
    """El perfil que se propone cuando la factura todavia no tiene ninguno.

    Se elige el activo mas antiguo, que en la practica es el que se dio de alta
    al migrar con la marca que ya se venia usando.
    """
    return db.execute(
        select(BrandProfile)
        .where(BrandProfile.is_active.is_(True))
        .order_by(BrandProfile.id)
        .limit(1)
    ).scalar_one_or_none()


def congelar_marca(db: Session, invoice: Invoice) -> None:
    """Copia a la factura el nombre y el titulo del perfil elegido.

    Se copian, no se leen del perfil al mostrar, por la misma razon que los
    datos bancarios: corregir manana el nombre de la marca no puede cambiar lo
    que decia una factura ya emitida.

    El logotipo y el icono no se copian aqui porque son archivos: de esos se
    encarga la carpeta del snapshot cuando se genera el PDF.
    """
    perfil = db.get(BrandProfile, invoice.brand_profile_id) if invoice.brand_profile_id else None
    if perfil is None:
        return
    invoice.brand_name = perfil.name
    invoice.brand_doc_title = perfil.doc_title or None


def cambiar_marca(db: Session, invoice: Invoice, form) -> bool:
    """Cambia la marca de una factura ya existente, si el formulario la trae.

    Solo se acepta un perfil activo. Si llega el id de uno desactivado, o algo
    que no es un id, no se toca nada: es preferible dejar la marca que tenia a
    dejarla sin ninguna.

    Devuelve True si ha cambiado.
    """
    crudo = (form.get("brand_profile_id") or "").strip() if form is not None else ""
    if not crudo.isdigit():
        return False
    nuevo = int(crudo)
    if nuevo == invoice.brand_profile_id:
        return False
    perfil = db.get(BrandProfile, nuevo)
    if perfil is None or not perfil.is_active:
        return False
    invoice.brand_profile_id = perfil.id
    congelar_marca(db, invoice)
    return True


def inherit_brand(db: Session, invoice: Invoice, form=None) -> None:
    """Elige el perfil de marca de una factura nueva y lo congela."""
    elegido = None
    if form is not None:
        crudo = (form.get("brand_profile_id") or "").strip()
        if crudo.isdigit():
            perfil = db.get(BrandProfile, int(crudo))
            # Un perfil desactivado no se puede elegir para una factura nueva,
            # ni aunque llegue su id en el formulario.
            if perfil is not None and perfil.is_active:
                elegido = perfil
    if elegido is None:
        elegido = perfil_por_defecto(db)
    invoice.brand_profile_id = elegido.id if elegido else None
    congelar_marca(db, invoice)


# --- alta y duplicado --------------------------------------------------------


def create(db: Session, form, folio_manual: str | None = None) -> tuple[Invoice | None, list[str]]:
    """Crea una factura a partir del formulario. No hace commit: de eso se
    encarga commit_creation, que ademas reintenta si el folio se ocupa.

    folio_manual llega ya validado por normalizar_folio_manual y solo despues de
    haber pasado la Master Password. Si no llega, el folio lo pone el contador,
    que es el modo normal.
    """
    invoice = Invoice(status=STATUS_DRAFT, locale=DEFAULT_LOCALE)
    apply_form(invoice, form)
    # En modo automatico el folio lo pone SIEMPRE el contador. No se lee del
    # formulario ni aunque llegue: el campo es de solo lectura en pantalla y
    # ademas no esta en EDITABLE_FIELDS, de modo que un envio manipulado
    # tampoco lo cambia. El unico camino para escribirlo a mano es el modo
    # Manual, que pasa por normalizar_folio_manual y por la Master Password.
    invoice.folio = folio_manual or next_folio(db)
    inherit_settings(db, invoice)
    inherit_brand(db, invoice, form)
    errores = validate(invoice)
    if errores:
        return None, errores

    db.add(invoice)
    # El flush asigna el id sin cerrar la transaccion, para poder dejar el folio
    # anotado ya apuntando a su factura. Si esto se deshace, se deshacen las dos
    # cosas a la vez.
    db.flush()
    anotar_folio(
        db,
        invoice.folio,
        invoice.id,
        FOLIO_MANUAL if folio_manual else FOLIO_AUTO,
    )
    return invoice, []


def _heredar_fotos(source: Invoice, copia: Invoice) -> None:
    """Lleva a la copia las fotografias del vehiculo del original.

    Cada archivo se duplica en disco. Si las dos facturas compartieran la misma
    ruta, sustituir una fotografia en la copia borraria el archivo y dejaria sin
    imagen a la factura original, que puede llevar meses emitida.
    """
    from . import uploads
    from .models import InvoicePhoto

    for foto in getattr(source, "photos", []):
        copiada = uploads.copiar(foto.file_path)
        if copiada is None:
            # El archivo ya no esta en el disco. Se omite esa posicion en vez de
            # crear una fotografia que apunta a la nada.
            continue
        copia.photos.append(
            InvoicePhoto(
                position=foto.position,
                file_path=copiada,
                original_name=foto.original_name,
            )
        )


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

    Las fotografias del vehiculo SI acompanan a la copia, porque son datos del
    vehiculo como el VIN o el kilometraje, y duplicar es normalmente atender a
    otro interesado por el mismo coche. Se copian los archivos, no la ruta: ver
    uploads.copiar().
    """
    copia = Invoice()
    for name in DUPLICATE_CARRY_FIELDS:
        setattr(copia, name, getattr(source, name))
    _heredar_fotos(source, copia)

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
    # La marca SI se hereda del original: duplicar es normalmente atender a otro
    # interesado por el mismo coche, y el coche es de la misma marca. El nombre
    # y el titulo se vuelven a copiar del perfil vigente, no del original, por si
    # el perfil se ha corregido desde entonces.
    copia.brand_profile_id = source.brand_profile_id
    congelar_marca(db, copia)

    db.add(copia)
    db.flush()
    anotar_folio(db, copia.folio, copia.id, FOLIO_AUTO)
    return copia


# --- archivar y eliminar -----------------------------------------------------


def tiene_historico(db: Session, invoice: Invoice) -> bool:
    """Si esta factura llego alguna vez a convertirse en documento.

    Se miran las tres huellas posibles, no solo los snapshots: pudo generarse un
    PDF y borrarse la carpeta a mano, o pudo enviarse. Cualquiera de las tres
    convierte la factura en algo que el cliente ya vio, y eso no se destruye.
    """
    if invoice.pdf_generated_at is not None or invoice.sent_at is not None:
        return True
    return bool(
        db.execute(
            select(InvoiceSnapshot.id).where(InvoiceSnapshot.invoice_id == invoice.id)
        ).first()
    )


def motivo_para_no_eliminar(db: Session, invoice: Invoice) -> str | None:
    """Por que NO se puede eliminar esta factura, o None si si se puede.

    La regla acordada: solo se elimina de verdad una factura Cancelada que nunca
    llego a emitir documento. Con historico se archiva, porque destruirla
    romperia la trazabilidad de algo que el cliente ya tuvo en la mano.
    """
    if invoice.status != STATUS_CANCELLED:
        return "Solo se puede eliminar una factura Cancelada."
    if tiene_historico(db, invoice):
        return (
            "Esta factura ya generó documento, así que no se elimina: se archiva, "
            "para no romper la trazabilidad."
        )
    return None


def archivar(db: Session, invoice: Invoice) -> None:
    """Saca la factura del listado normal sin tocar nada suyo.

    Conserva folio, snapshots, fotografias e historial. Es reversible.
    """
    if invoice.archived_at is None:
        invoice.archived_at = utcnow()


def desarchivar(db: Session, invoice: Invoice) -> None:
    invoice.archived_at = None


def eliminar(db: Session, invoice: Invoice) -> str:
    """Borra la factura de verdad. Devuelve el folio, que sobrevive.

    Lo importante de esta funcion es lo que NO borra. La fila del registro de
    folios se queda, asi que el numero sigue reservado para siempre: eliminar no
    libera nunca un folio. El historial de actividad tampoco se va, porque su
    entity_id es un entero suelto y sin clave foranea.

    Las fotografias se borran del disco a mano: el cascade de la ORM se lleva
    las filas, pero los archivos se quedarian ocupando sitio para siempre.
    """
    from . import uploads

    folio = invoice.folio
    for foto in list(invoice.photos):
        uploads.borrar(foto.file_path)
    db.delete(invoice)
    return folio


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


def commit_creation(
    db: Session, construir, intentos: int = 6, *, reintentar: bool = True
) -> Invoice:
    """Crea y guarda reintentando si otro operador se lleva el folio primero.

    La cuenta de Admin es compartida, asi que dos personas pueden crear una
    factura casi a la vez. La columna folio es unica, de modo que el segundo en
    llegar recibiria un error de base de datos en plena pantalla.

    Aqui se captura ese choque, se deshace la transaccion y se vuelve a
    construir la factura. Como next_folio comprueba contra la base de datos
    antes de proponer un numero, el reintento coge ya el siguiente libre y
    converge en la primera vuelta.

    construir(db) -> Invoice, ya anadida a la sesion y con su folio puesto.

    reintentar=False para el folio escrito a mano. Ahi el reintento seria un
    fallo grave y no una ayuda: cogeria un numero distinto del que el Admin
    escribio y se lo guardaria en silencio, de modo que creeria haber emitido
    RES-95000 cuando en la base hay otro. Con el folio manual el choque tiene
    que llegar a la pantalla.
    """
    if not reintentar:
        invoice = construir(db)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if _es_choque_de_folio(exc):
                raise FolioOcupado(
                    "Ese folio se ha ocupado mientras guardabas. No se ha creado nada; "
                    "vuelve a intentarlo con otro número."
                ) from exc
            raise
        return invoice

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


# --- validacion de Configuracion ---------------------------------------------


def validar_ajuste(clave: str, valor: str, market: str | None) -> str | None:
    """Comprueba un ajuste antes de guardarlo. Devuelve el motivo o None.

    Se valida lo que puede romper algo de verdad, no todo por costumbre. Una
    CLABE mal tecleada en Configuracion se copia despues a cada factura nueva y
    el cliente transfiere a una cuenta que no existe: el error se descubre con
    el dinero por medio.
    """
    if clave == "banking.account_number" and valor:
        correcto, mensaje = validate_account(valor, market or DEFAULT_LOCALE)
        if not correcto:
            return f"{MARKETS[market].label if market in MARKETS else market}: {mensaje}"

    if clave == "folio.next":
        if not valor.isdigit():
            return "El contador de folios tiene que ser un número."
        if int(valor) <= 0:
            return "El contador de folios tiene que ser mayor que cero."

    if clave == "folio.prefix" and not valor:
        return "El prefijo del folio no puede quedar vacío."

    if clave in ("qr.base_url", "verification.url_base") and valor:
        if not valor.startswith(("http://", "https://")):
            return "La URL del QR tiene que empezar por http:// o https://"
        if " " in valor:
            return "La URL del QR no puede llevar espacios."

    if clave.endswith(".email") and valor and "@" not in valor:
        return f"{clave}: no parece un email."

    return None
