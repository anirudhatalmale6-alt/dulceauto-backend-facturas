"""
Claves fijas.

Este archivo es el contrato entre la base de datos y las tres plantillas. Es la
sustitucion definitiva de las claves derivadas del texto que se usaron en el
Milestone 1, donde cambiar una frase en es-MX renombraba la clave y desenganchaba
en silencio las versiones en ingles y en es-AR.

A partir de aqui, el texto de la factura puede cambiar tantas veces como haga
falta: la clave no se mueve.

Regla de nomenclatura: la clave es grupo.campo y la columna es grupo_campo.
customer.name vive en Invoice.customer_name. Sin excepciones, para que la
correspondencia se pueda comprobar de un vistazo y no haya que memorizarla.
"""

# Clave fija -> atributo del modelo Invoice.
FIELD_MAP: dict[str, str] = {
    # invoice / template
    "invoice.folio": "folio",
    "invoice.status": "status",
    "template.locale": "locale",
    # transaction
    "transaction.folio": "folio",
    "transaction.issue_date": "issue_date",
    "transaction.valid_until": "valid_until",
    "transaction.authorization": "authorization",
    "transaction.status": "status",
    # customer
    "customer.name": "customer_name",
    "customer.email": "customer_email",
    "customer.phone": "customer_phone",
    "customer.city": "customer_city",
    # vehicle
    "vehicle.title": "vehicle_title",
    "vehicle.location": "vehicle_location",
    "vehicle.vin": "vehicle_vin",
    "vehicle.year": "vehicle_year",
    "vehicle.type": "vehicle_type",
    "vehicle.mileage": "vehicle_mileage",
    "vehicle.fuel": "vehicle_fuel",
    "vehicle.transmission": "vehicle_transmission",
    "vehicle.carfax": "vehicle_carfax",
    # pricing
    "vehicle.price": "pricing_vehicle_price",
    "pricing.vehicle_price": "pricing_vehicle_price",
    "pricing.reservation_amount": "pricing_reservation_amount",
    "payment.amount": "pricing_reservation_amount",
    "pricing.discount": "pricing_discount",
    "pricing.coverage": "pricing_coverage",
    "pricing.transport": "pricing_transport",
    "pricing.currency": "pricing_currency",
    # banking
    "banking.bank": "banking_bank",
    "banking.beneficiary": "banking_beneficiary",
    "banking.account_label": "banking_account_label",
    "banking.account_number": "banking_account_number",
    "payment.account": "banking_account_number",
    "banking.bank_account": "banking_bank_account",
    "banking.payment_reference": "banking_payment_reference",
    # delivery
    "delivery.date": "delivery_date",
    "delivery.date_latest": "delivery_date_latest",
    "delivery.mode": "delivery_mode",
    "delivery.text": "delivery_text",
    "delivery.alt": "delivery_alt",
    # representative
    "representative.name": "representative_name",
    "representative.role": "representative_role",
    "representative.phone": "representative_phone",
    "representative.email": "representative_email",
    "representative.hours": "representative_hours",
    # verification
    "verification.url_base": "verify_url_base",
}

# Campos que el operador edita a mano en el formulario, en el orden de las seis
# secciones de la maqueta V1.3. Los datos bancarios no estan aqui: se heredan
# de Configuracion y solo se cambian tras pasar la Master Password.
#
# El folio NO esta en esta lista: se asigna solo desde el contador de
# Configuracion y queda de solo lectura. Un folio editado a mano se
# desincronizaria de la referencia bancaria, que se genera a partir de el.
EDITABLE_FIELDS: tuple[str, ...] = (
    "locale",
    "status",
    "issue_date",
    "valid_until",
    "authorization",
    "customer_name",
    "customer_email",
    "customer_phone",
    "customer_city",
    "vehicle_title",
    "vehicle_location",
    "vehicle_vin",
    "vehicle_year",
    "vehicle_type",
    "vehicle_mileage",
    "vehicle_fuel",
    "vehicle_transmission",
    "vehicle_carfax",
    "pricing_vehicle_price",
    "pricing_reservation_amount",
    "pricing_discount",
    "pricing_coverage",
    "pricing_transport",
    "pricing_currency",
    "delivery_date",
    "delivery_date_latest",
    "delivery_mode",
    "delivery_text",
    "delivery_alt",
    "representative_name",
    "representative_role",
    "representative_phone",
    "representative_email",
    "representative_hours",
)

# Campos que se copian tal cual al duplicar una factura. Todo lo que no este en
# esta lista se reinicia en la copia: cliente, folio, fechas y estado. Es la
# regla que impide que duplicar confirme una reserva por accidente.
#
# Los datos bancarios y los del representante NO se copian del original. Una
# copia es una operacion nueva para un cliente nuevo, asi que se cargan de la
# Configuracion vigente en el momento de crearla. De ese modo una factura ya
# emitida conserva para siempre la cuenta a la que se pidio pagar, pero una
# duplicacion posterior nunca arrastra por accidente una cuenta que ya se
# cambio.
DUPLICATE_CARRY_FIELDS: tuple[str, ...] = (
    "locale",
    "vehicle_title",
    "vehicle_location",
    "vehicle_vin",
    "vehicle_year",
    "vehicle_type",
    "vehicle_mileage",
    "vehicle_fuel",
    "vehicle_transmission",
    "vehicle_carfax",
    "pricing_vehicle_price",
    "pricing_reservation_amount",
    "pricing_discount",
    "pricing_coverage",
    "pricing_transport",
    "pricing_currency",
    "delivery_mode",
    "delivery_text",
    "delivery_alt",
)


def resolve(invoice, key: str):
    """Valor de una clave fija sobre una factura. Devuelve None si la clave no
    existe, en lugar de reventar: una plantilla con una clave mal escrita debe
    salir con el hueco vacio, no tirar la generacion del PDF entera."""
    attr = FIELD_MAP.get(key)
    return getattr(invoice, attr, None) if attr else None
