"""
Modelo de datos definitivo del backend.

Tres decisiones que conviene no deshacer, porque son las que mantienen viva la
promesa de poder migrar a MySQL o PostgreSQL sin reescribir la aplicacion:

1. Todas las fechas se guardan en UTC y sin zona horaria. sqlite no conserva
   la zona, asi que guardarla daria un comportamiento distinto en cada motor.
   La conversion a hora local se hace al mostrar, nunca al guardar.
2. No se usan tipos ENUM. Cada motor los implementa a su manera y alterarlos
   despues es doloroso. Los estados y los idiomas son texto validado en la
   aplicacion, con las constantes declaradas aqui arriba.
3. Los importes son Numeric(14, 2), no float. sqlite los convierte a coma
   flotante por dentro, pero al pasar a MySQL o PostgreSQL la columna ya es
   decimal exacta y no hay que migrar nada.

Los nombres de columna siguen las claves fijas acordadas con el cliente:
customer_name es customer.name, vehicle_vin es vehicle.vin, y asi con todas.
La correspondencia esta en app/fields.py.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# --- constantes de dominio ---------------------------------------------------

LOCALES = ("es-MX", "en", "es-AR")

# Estados de la operacion.
#
# Describen en que punto esta la reserva para el cliente, y nada mas. Generar el
# PDF o enviarlo son acciones nuestras, no pasos de la operacion: viven en
# pdf_generated_at y sent_at, y no ocupan un estado. Antes si lo hacian, y eso
# hacia imposible saber si una factura con el PDF hecho estaba cobrada o no.
STATUS_DRAFT = "draft"
STATUS_PENDING = "pending"
STATUS_VALIDATED = "payment_validated"
STATUS_SCHEDULED = "delivery_scheduled"
STATUS_DELIVERED = "delivered"
STATUS_CANCELLED = "cancelled"

# En orden, que es como se recorren. Cancelada va aparte porque no es un punto
# del recorrido sino una salida.
STATUS_FLOW = (
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_VALIDATED,
    STATUS_SCHEDULED,
    STATUS_DELIVERED,
)
STATUSES = STATUS_FLOW + (STATUS_CANCELLED,)

# A partir de aqui el vehiculo esta comprometido de verdad. Se cuenta desde que
# el pago esta validado, no desde que se genera o se manda el PDF: pueden
# convivir varias pre-facturas del mismo coche para varios interesados sin que
# ninguna lo bloquee.
COMMITTED_STATUSES = (STATUS_VALIDATED, STATUS_SCHEDULED, STATUS_DELIVERED)

CRED_ADMIN = "admin"
CRED_MASTER = "master"
# Cuenta del Call Center. Es una fila mas de la misma tabla, no una tabla de
# usuarios nueva: el cliente pidio expresamente un unico Operador compartido y
# no una gestion de equipos. El dia que quiera operadores individuales, lo que
# cambia es de donde se leen las filas, no el resto del sistema.
CRED_OPERATOR = "operator"

# Papel de la sesion. Va SIEMPRE en la sesion, nunca deducido de la URL: es lo
# unico que separa al Operador del Admin, y tiene que decidirse en un solo
# sitio para que no se pueda saltar escribiendo una direccion a mano.
ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLES = (ROLE_ADMIN, ROLE_OPERATOR)

# Tipos de nota del Operador, con los nombres del prototipo V1.4.
NOTE_CUSTOMER = "cliente"
NOTE_FOLLOWUP = "seguimiento"
NOTE_FAQ = "faq"
NOTE_TYPES = (NOTE_CUSTOMER, NOTE_FOLLOWUP, NOTE_FAQ)

# De donde salio un folio. Solo sirve para poder mirar el registro y entender
# como se asigno; ninguna decision del programa depende de este valor.
FOLIO_AUTO = "auto"
FOLIO_MANUAL = "manual"
FOLIO_BACKFILL = "backfill"


def utcnow() -> datetime:
    """Ahora en UTC y sin zona, que es como se guarda todo en este proyecto."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- seguridad ---------------------------------------------------------------


class Credential(Base):
    """Las dos contrasenas del sistema, siempre hasheadas.

    Hay exactamente dos filas: 'admin' (acceso al panel) y 'master' (segunda
    barrera para Configuracion). Se separan en filas distintas y no en columnas
    de una tabla de usuarios porque son barreras independientes: la master la
    conoce solo el propietario, aunque la cuenta de panel sea compartida.
    """

    __tablename__ = "credential"

    name: Mapped[str] = mapped_column(String(16), primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    must_change: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# --- configuracion -----------------------------------------------------------


class Setting(Base):
    """Ajustes de Configuracion, opcionalmente por mercado.

    market a None significa que el ajuste es global (logo, modo de QR, URL de
    verificacion). Con un valor de LOCALES, el ajuste es de ese mercado: banco,
    beneficiario, CLABE o CBU, cuenta.
    """

    __tablename__ = "setting"
    __table_args__ = (UniqueConstraint("key", "market", name="uq_setting_key_market"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str | None] = mapped_column(String(8), nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Marca los ajustes que solo deberian tocarse tras pasar la Master Password.
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# --- marca -------------------------------------------------------------------


class BrandProfile(Base):
    """Una marca con la que se emiten facturas: nombre, logotipo, icono de
    "Compra segura" y titulo del documento.

    Es una ficha de marca y nada mas. No lleva cuentas bancarias, ni usuarios,
    ni datos fiscales propios: eso seria un sistema multiempresa, que el cliente
    descarto expresamente para esta version.

    No se borran. Un perfil que ya no se use se marca is_active=False y deja de
    ofrecerse al crear facturas nuevas, pero las que ya lo tienen conservan su
    marca. Borrarlo de verdad dejaria sin logotipo a una factura todavia no
    emitida, que es justo lo que el cliente pidio evitar.
    """

    __tablename__ = "brand_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Titulo del documento: es lo que se ve en la pestana del navegador y lo que
    # Chromium copia a los metadatos Title del PDF.
    doc_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    logo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    safe_icon_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# --- folios ------------------------------------------------------------------


class FolioLedger(Base):
    """Todos los folios que han existido alguna vez.

    Existe por una razon concreta. next_folio no reutiliza un folio porque
    comprueba contra la tabla de facturas, pero esa comprobacion vive en que la
    fila siga estando: al permitir borrar una factura cancelada, el sistema
    perderia la memoria de ese folio y podria volver a emitirlo.

    Aqui se anota el folio en el momento de asignarlo y no se borra nunca. La
    fila sobrevive al archivado y al borrado de la factura, asi que la promesa
    de "un folio usado no se recicla" deja de depender del ciclo de vida de la
    factura.

    El folio es la clave primaria a proposito: es la propia base de datos la que
    impide anotar dos veces el mismo, sin tener que confiar en el codigo.

    invoice_id se guarda como entero suelto y SIN clave foranea, igual que en
    activity_log: si se borra la factura, la anotacion tiene que quedarse.
    """

    __tablename__ = "folio_ledger"

    folio: Mapped[str] = mapped_column(String(32), primary_key=True)
    invoice_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=FOLIO_AUTO)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# --- facturas ----------------------------------------------------------------


class Invoice(Base):
    """Una pre-factura.

    Los datos bancarios y los del representante se copian aqui al crear la
    factura en lugar de leerse de Configuracion al mostrarla. Es a proposito:
    si manana se cambia la cuenta bancaria en Configuracion, una factura ya
    emitida tiene que seguir enseñando la cuenta a la que se le pidio pagar al
    cliente. Lo mismo vale para el snapshot y sus activos.
    """

    __tablename__ = "invoice"
    __table_args__ = (
        Index("ix_invoice_vin", "vehicle_vin"),
        Index("ix_invoice_status", "status"),
        Index("ix_invoice_locale", "locale"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # invoice.folio / template.locale
    folio: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="es-MX")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_DRAFT)

    # transaction.*
    issue_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    authorization: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # customer.*
    customer_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    customer_email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    customer_city: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # vehicle.*
    vehicle_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vehicle_location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    vehicle_vin: Mapped[str | None] = mapped_column(String(17), nullable=True)
    vehicle_year: Mapped[str | None] = mapped_column(String(8), nullable=True)
    vehicle_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vehicle_mileage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vehicle_fuel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vehicle_transmission: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vehicle_carfax: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # pricing.*
    pricing_vehicle_price: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    pricing_reservation_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    pricing_discount: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pricing_coverage: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pricing_transport: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pricing_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # banking.* — congelados en la factura, ver docstring
    banking_bank: Mapped[str | None] = mapped_column(String(120), nullable=True)
    banking_beneficiary: Mapped[str | None] = mapped_column(String(160), nullable=True)
    banking_account_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    banking_account_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    banking_bank_account: Mapped[str | None] = mapped_column(String(40), nullable=True)
    banking_payment_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # delivery.*
    delivery_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    delivery_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delivery_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_alt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # representative.*
    representative_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    representative_role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    representative_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    representative_email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    representative_hours: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # verification.*
    verify_url_base: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Acciones sobre el PDF. No son estados de la operacion: una factura puede
    # tener el PDF hecho y seguir esperando el pago. Se guardan como marcas de
    # tiempo para poder decir *cuando* paso, que es lo que interesa.
    pdf_generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Trazabilidad de la duplicacion. Duplicar NO confirma reserva: la copia
    # nace siempre como borrador y con folio propio.
    duplicated_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoice.id", ondelete="SET NULL"), nullable=True
    )

    # brand.* — la marca con la que se emite este documento.
    #
    # El perfil se guarda para poder seguir editando la factura y para saber de
    # donde salio, pero el nombre y el titulo se COPIAN aqui al crearla, igual
    # que se hace con banking_*. Cambiar el perfil manana no puede cambiar lo
    # que decia un documento ya emitido. El logotipo y el icono se congelan
    # aparte, en la carpeta del snapshot, que es el mecanismo que ya existia.
    brand_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("brand_profile.id", ondelete="SET NULL"), nullable=True
    )
    brand_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    brand_doc_title: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Archivado. Una factura archivada desaparece del listado normal pero no se
    # toca: conserva su folio, sus snapshots y su historial. Es la salida por
    # defecto para una cancelada que ya llego a emitir documento.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    photos: Mapped[list["InvoicePhoto"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoicePhoto.position"
    )
    snapshots: Mapped[list["InvoiceSnapshot"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceSnapshot.version.desc()",
    )


class InvoicePhoto(Base):
    """Las cuatro fotografias del vehiculo. position va de 1 a 4."""

    __tablename__ = "invoice_photo"
    __table_args__ = (UniqueConstraint("invoice_id", "position", name="uq_photo_invoice_pos"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoice.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(String(255), nullable=False)
    original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="photos")


class InvoiceSnapshot(Base):
    """Copia congelada de una factura generada.

    assets_dir guarda una copia propia del logo, del QR y de las fotografias
    de ese momento. Sin esa copia, reemplazar el logo en Configuracion
    cambiaria retroactivamente todas las facturas antiguas, que es justo lo que
    el cliente pidio evitar.
    """

    __tablename__ = "invoice_snapshot"
    __table_args__ = (UniqueConstraint("invoice_id", "version", name="uq_snapshot_invoice_ver"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoice.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    folio: Mapped[str] = mapped_column(String(32), nullable=False)
    locale: Mapped[str] = mapped_column(String(8), nullable=False)
    pdf_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    html_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assets_dir: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="snapshots")


# --- actividad ---------------------------------------------------------------


class ActivityLog(Base):
    """Registro basico. En V1 la cuenta es unica y compartida, asi que el actor
    es siempre 'Admin'; la columna existe igualmente para el dia que se separen
    las cuentas por empleado y no haya que migrar la tabla."""

    __tablename__ = "activity_log"
    __table_args__ = (Index("ix_activity_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="Admin")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    folio: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# --- call center -------------------------------------------------------------


class OperatorFaq(Base):
    """Guia de respuestas aprobadas que lee el Operador.

    Existe para que cambiar una respuesta no obligue a tocar codigo ni a
    desplegar: el Admin la edita y el Operador la ve en la siguiente peticion.

    'answer' es opcional a proposito. Una pregunta puede estar recogida y
    todavia no tener respuesta autorizada; en ese caso NO puede publicarse como
    activa, y esa regla no se deja al criterio de la pantalla: la comprueba
    tambien 'puede_publicarse'.
    """

    __tablename__ = "operator_faq"
    __table_args__ = (Index("ix_faq_orden", "sort_order", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def puede_publicarse(self) -> bool:
        return bool((self.answer or "").strip())


class OperatorNote(Base):
    """Nota escrita por el Operador durante una llamada.

    Dos decisiones deliberadas:

    - invoice_id va CON clave foranea y CASCADE, al reves que activity_log: una
      nota es contexto de esa factura y no tiene sentido sin ella. El folio se
      guarda ademas en texto para poder leer el historico sin cruzar tablas.
    - Es de solo anadir. No hay ruta de edicion ni de borrado para el Operador,
      porque una nota que se puede reescribir despues no sirve como constancia
      de lo que se dijo en la llamada.
    """

    __tablename__ = "operator_note"
    __table_args__ = (Index("ix_nota_factura", "invoice_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoice.id", ondelete="CASCADE"), nullable=False
    )
    folio: Mapped[str] = mapped_column(String(32), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False, default=NOTE_CUSTOMER)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="Operador")
    # Las sugerencias de FAQ nacen sin publicar y solo el Admin las convierte en
    # una entrada de la guia. Sin esta marca no habria forma de distinguir una
    # sugerencia ya atendida de una pendiente.
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    invoice: Mapped[Invoice] = relationship()
