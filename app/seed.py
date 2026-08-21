"""
Siembra inicial.

Se ejecuta al arrancar y solo crea lo que falta, asi que es seguro llamarla
tantas veces como haga falta: no pisa nada que ya exista ni toca datos reales.

Los datos bancarios que deja puestos son de muestra, como se acordo. Se
sustituyen desde Configuracion / Super-admin una vez montado el sistema.
"""
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .locales import MARKETS
from .models import (
    CRED_ADMIN,
    CRED_MASTER,
    STATUS_DRAFT,
    STATUS_VALIDATED,
    STATUS_PENDING,
    Credential,
    Invoice,
    Setting,
)
from .security import hash_password

# --- ajustes globales --------------------------------------------------------

GLOBAL_SETTINGS: dict[str, tuple[str, bool]] = {
    # clave: (valor por defecto, es_sensible)
    "brand.logo_path": ("", True),
    "qr.mode": ("dynamic", True),
    "qr.base_url": ("https://dulceauto.mx/verificar/", True),
    "qr.image_path": ("", True),
    "pdf.engine": ("chromium", False),
    "pdf.page_size": ("A4", False),
    "pdf.single_page": ("1", False),
    "folio.prefix": ("RES-", False),
    "folio.next": ("87242", False),
}

# --- datos bancarios de muestra, por mercado ---------------------------------

MARKET_SETTINGS: dict[str, dict[str, str]] = {
    "es-MX": {
        "banking.bank": "BBVA México",
        "banking.beneficiary": "DulceAuto México S.A. de C.V.",
        "banking.account_label": "CLABE interbancaria (18 dígitos)",
        "banking.account_number": "012180001234567899",
        "banking.bank_account": "0123456789",
        "representative.name": "Yoselina de la Cruz",
        "representative.role": "Representante de operaciones",
        "representative.phone": "55 1234 5678",
        "representative.email": "soporte@dulceauto.mx",
        "representative.hours": "Lunes a viernes, 8:00 a. m.–4:00 p. m.",
    },
    "en": {
        "banking.bank": "BBVA México",
        "banking.beneficiary": "DulceAuto México S.A. de C.V.",
        # La version inglesa usa una CLABE mexicana, igual que el HTML aprobado.
        "banking.account_label": "CLABE (18 digits)",
        "banking.account_number": "012180001234567899",
        "banking.bank_account": "0123456789",
        "representative.name": "Yoselina de la Cruz",
        "representative.role": "Operations representative",
        "representative.phone": "+52 55 1234 5678",
        "representative.email": "support@dulceauto.mx",
        "representative.hours": "Monday to Friday, 8:00 a.m.–4:00 p.m.",
    },
    "es-AR": {
        "banking.bank": "Banco de muestra S.A.",
        "banking.beneficiary": "DulceAuto Argentina S.A.",
        "banking.account_label": "CBU (22 dígitos)",
        "banking.account_number": "2850590994009041813526",
        "banking.bank_account": "9400904181352",
        "representative.name": "Yoselina de la Cruz",
        "representative.role": "Representante de operaciones",
        "representative.phone": "11 1234 5678",
        "representative.email": "soporte@dulceauto.com.ar",
        "representative.hours": "Lunes a viernes, 8:00 a. m.–4:00 p. m.",
    },
}


def _ensure_setting(db: Session, key: str, market: str | None, value: str, sensitive: bool) -> None:
    stmt = select(Setting).where(Setting.key == key, Setting.market == market)
    if db.execute(stmt).scalar_one_or_none() is None:
        db.add(Setting(key=key, market=market, value=value, is_sensitive=sensitive))


def seed_credentials(db: Session) -> None:
    """Crea las dos contrasenas la primera vez, ya hasheadas.

    Quedan marcadas con must_change para que el panel avise de que hay que
    cambiarlas. Los valores iniciales viven en el entorno, nunca en el codigo.
    """
    if db.get(Credential, CRED_ADMIN) is None:
        db.add(
            Credential(
                name=CRED_ADMIN,
                username=settings.initial_admin_user,
                password_hash=hash_password(settings.initial_admin_password),
                must_change=True,
            )
        )
    if db.get(Credential, CRED_MASTER) is None:
        db.add(
            Credential(
                name=CRED_MASTER,
                password_hash=hash_password(settings.initial_master_password),
                must_change=True,
            )
        )


def seed_settings(db: Session) -> None:
    for key, (value, sensitive) in GLOBAL_SETTINGS.items():
        _ensure_setting(db, key, None, value, sensitive)
    for market, values in MARKET_SETTINGS.items():
        for key, value in values.items():
            _ensure_setting(db, key, market, value, key.startswith("banking."))


# --- facturas de ejemplo -----------------------------------------------------
#
# Son las mismas tres de la maqueta V1.3, para que el panel no arranque vacio y
# se pueda comprobar el comportamiento con los tres idiomas desde el primer dia.
# Se crean solo si no hay ninguna factura.

SAMPLE_INVOICES = [
    dict(
        folio="RES-87241",
        locale="es-MX",
        status=STATUS_PENDING,
        issue_date=date(2026, 7, 22),
        valid_until=date(2026, 7, 29),
        authorization="AUT-2026-87241",
        customer_name="Juan Pérez García",
        customer_email="juan.perez@gmail.com",
        customer_phone="55 1234 5678",
        customer_city="Veracruz",
        vehicle_title="2015 Audi A3 1.8T S Line Convertible AT",
        vehicle_location="Ciudad de México",
        vehicle_vin="19UTC2895KL500992",
        vehicle_year="2015",
        vehicle_type="Convertible",
        vehicle_mileage="16,678 km",
        vehicle_fuel="Gasolina",
        vehicle_transmission="Automática",
        vehicle_carfax="CARFAX",
        pricing_vehicle_price=329000,
        pricing_reservation_amount=3240,
        pricing_discount="9% DE DESCUENTO APLICADO",
        pricing_currency="MXN",
        delivery_date=date(2026, 7, 27),
        delivery_mode="home",
    ),
    dict(
        folio="RES-87240",
        locale="es-AR",
        # Muestra de una operacion ya cobrada: es la que ensena la barra de
        # progreso avanzada y la que hace saltar el aviso de vehiculo
        # comprometido.
        status=STATUS_VALIDATED,
        pdf_generated_at=datetime(2026, 8, 12, 17, 30),
        issue_date=date(2026, 8, 12),
        valid_until=date(2026, 8, 19),
        authorization="AUT-2026-87240",
        customer_name="María González",
        customer_email="maria@example.com",
        customer_phone="11 2345 6789",
        customer_city="Buenos Aires",
        vehicle_title="2023 Chevrolet Suburban",
        vehicle_location="Buenos Aires",
        vehicle_vin="1GNSKJKC7PR123456",
        vehicle_year="2023",
        vehicle_type="SUV",
        vehicle_mileage="12.400 km",
        vehicle_fuel="Nafta",
        vehicle_transmission="Automática",
        pricing_vehicle_price=329000,
        pricing_reservation_amount=3240,
        pricing_currency="ARS",
        delivery_date=date(2026, 8, 20),
        delivery_mode="branch",
    ),
    dict(
        folio="RES-87239",
        locale="en",
        status=STATUS_DRAFT,
        issue_date=date(2026, 8, 11),
        valid_until=date(2026, 8, 18),
        authorization="AUT-2026-87239",
        customer_name="Michael Reed",
        customer_email="michael@example.com",
        customer_phone="+1 555 0134",
        customer_city="Houston",
        vehicle_title="2019 BMW X3 xDrive30i",
        vehicle_location="Ciudad de México",
        vehicle_vin="5UXTR9C58KL500881",
        vehicle_year="2019",
        vehicle_type="SUV",
        vehicle_mileage="41,205 km",
        vehicle_fuel="Gasoline",
        vehicle_transmission="Automatic",
        pricing_vehicle_price=329000,
        pricing_reservation_amount=3240,
        pricing_currency="MXN",
        delivery_date=date(2026, 8, 21),
        delivery_mode="home",
    ),
]


def _market_value(db: Session, market: str, key: str) -> str | None:
    stmt = select(Setting).where(Setting.key == key, Setting.market == market)
    row = db.execute(stmt).scalar_one_or_none()
    return row.value if row else None


def seed_invoices(db: Session) -> None:
    if db.execute(select(Invoice.id).limit(1)).scalar_one_or_none() is not None:
        return
    for data in SAMPLE_INVOICES:
        market = data["locale"]
        invoice = Invoice(**data)
        # Los datos bancarios y del representante se copian desde Configuracion
        # en el momento de crear la factura, no se leen despues.
        for key in (
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
        ):
            setattr(invoice, key.replace(".", "_"), _market_value(db, market, key))
        invoice.banking_payment_reference = invoice.folio
        invoice.verify_url_base = "https://dulceauto.mx/verificar/"
        db.add(invoice)


def run(db: Session) -> None:
    seed_credentials(db)
    seed_settings(db)
    db.commit()
    seed_invoices(db)
    db.commit()


__all__ = ["run", "MARKETS", "GLOBAL_SETTINGS", "MARKET_SETTINGS"]
