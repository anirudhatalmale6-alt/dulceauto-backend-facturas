"""guia de FAQs y notas del Operador de Call Center

Bloque B. Dos tablas nuevas y nada mas: no toca ninguna columna existente ni
reescribe ninguna fila, de modo que aplicarla no puede alterar una factura.

1. operator_faq: la guia de respuestas aprobadas que lee el Operador. Se
   siembra con las preguntas y respuestas que el propio cliente escribio en
   DulceAuto_CallCenter_Operador_V1.4.html, para que la guia no nazca vacia.

   La pregunta que en ese prototipo aparece marcada "missing" se importa SIN
   respuesta y con active=0: el alcance dice que una pregunta sin respuesta
   aprobada no debe publicarse, y sembrarla activa con un texto inventado
   seria exactamente lo contrario.

2. operator_note: las notas de llamada. Con clave foranea real y CASCADE,
   a diferencia de activity_log, porque una nota es contexto de esa factura y
   no significa nada sin ella.

La cuenta del Operador NO necesita migracion: la tabla credential ya guarda una
fila por credencial, asi que es una fila mas ('operator'), sembrada por seed.py.

Revision ID: c3a91f6e2b48
Revises: b7e41c9d5f02
Create Date: 2026-08-24
"""
import sqlalchemy as sa
from alembic import op

revision = "c3a91f6e2b48"
down_revision = "b7e41c9d5f02"
branch_labels = None
depends_on = None


# Guia inicial, copiada literalmente del prototipo V1.4 del cliente.
# (categoria, pregunta, respuesta o None, activa)
FAQS_INICIALES = [
    (
        "Reserva y pago",
        "¿Cómo se realiza el pago?",
        "Claro. El pago se realiza por SPEI a la cuenta indicada en tu pre-factura. "
        "Antes de hacerlo, podemos confirmar juntos el folio y el monto para que "
        "tengas la tranquilidad de que todo coincide.",
    ),
    (
        "Reserva y pago",
        "¿Cuánto tarda la validación del pago?",
        "Una vez recibido el comprobante, la validación puede tardar hasta 24 horas "
        "hábiles. En cuanto quede confirmada, continuamos con el siguiente paso de "
        "tu reserva.",
    ),
    (
        "Entrega",
        "¿Puedo recibir el vehículo en mi domicilio?",
        "Sí, con gusto. Cuando tu reserva indica entrega a domicilio, coordinamos el "
        "traslado mediante transporte asegurado y te confirmamos previamente la fecha "
        "y el horario.",
    ),
    (
        "Entrega",
        "¿Puedo cambiar la fecha de entrega?",
        "Sí, podemos registrar tu solicitud. Primero validamos disponibilidad con "
        "logística y después te confirmamos la nueva fecha para no prometer un horario "
        "que todavía no esté asegurado.",
    ),
    (
        "Vehículo y documentos",
        "¿Puedo revisar el vehículo antes de finalizar la compra?",
        "Sí. Al recibirlo puedes revisarlo personalmente o con un mecánico de tu "
        "confianza antes de finalizar la operación.",
    ),
    (
        "Vehículo y documentos",
        "¿Qué documentos recibiré al finalizar la compra?",
        "Recibirás la documentación indicada para la operación del vehículo. Si "
        "quieres, puedo revisar contigo lo que aparece específicamente en tu "
        "pre-factura.",
    ),
    (
        "Garantías y protección",
        "¿El vehículo cuenta con garantía?",
        "Sí. La guía actual contempla una garantía mecánica básica de 30 días o "
        "1,000 km, lo que ocurra primero.",
    ),
    (
        "Garantías y protección",
        "¿Cómo verifican los vehículos antes de publicarlos?",
        "Antes de publicarlos se revisa la documentación y se realiza una inspección "
        "del vehículo, incluyendo evidencia fotográfica y verificaciones del estado "
        "general.",
    ),
    (
        "Proceso general",
        "¿Cómo funciona el proceso de compra?",
        "Es sencillo: eliges el vehículo, haces la reserva, realizas el pago, "
        "validamos la operación, coordinamos la entrega y finalmente recibes el "
        "vehículo.",
    ),
    (
        "Proceso general",
        "¿Es seguro comprar un auto en línea?",
        "El proceso está diseñado para darte claridad en cada etapa: verificamos el "
        "vehículo y la documentación, validamos el pago y coordinamos la entrega de "
        "acuerdo con la información de tu reserva.",
    ),
    (
        "Proceso general",
        "¿Puedo comunicarme con alguien si tengo dudas?",
        "Por supuesto. Si durante el proceso tienes alguna duda, puedes comunicarte "
        "con nuestro equipo de atención y revisaremos tu reserva por folio para "
        "orientarte.",
    ),
    # Sin respuesta aprobada: entra inactiva y sin texto inventado.
    (
        "Pendiente",
        "¿Qué pasa si el vehículo tiene algún problema después de la entrega?",
        None,
    ),
]


def upgrade() -> None:
    op.create_table(
        "operator_faq",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_faq_orden", "operator_faq", ["sort_order", "id"])

    op.create_table(
        "operator_note",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("folio", sa.String(length=32), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False, server_default="cliente"),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False, server_default="Operador"),
        sa.Column("handled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoice.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_nota_factura", "operator_note", ["invoice_id", "created_at"])

    _sembrar_guia()


def _sembrar_guia() -> None:
    """Carga la guia del prototipo, solo si la tabla esta vacia.

    La condicion importa: si esta migracion se aplicara dos veces sobre una base
    que ya tiene FAQs editadas por el Admin, duplicar las originales le
    reescribiria la guia por debajo.
    """
    conn = op.get_bind()
    (ya_hay,) = conn.execute(sa.text("SELECT COUNT(*) FROM operator_faq")).one()
    if ya_hay:
        print(f"[operator_faq] la guia ya tiene {ya_hay} entradas, no se siembra")
        return

    insertar = sa.text(
        "INSERT INTO operator_faq "
        "(category, question, answer, active, sort_order, created_at, updated_at) "
        "VALUES (:cat, :q, :a, :activa, :orden, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    activas = pendientes = 0
    for orden, (categoria, pregunta, respuesta) in enumerate(FAQS_INICIALES, start=1):
        tiene_respuesta = bool(respuesta and respuesta.strip())
        conn.execute(
            insertar,
            {
                "cat": categoria,
                "q": pregunta,
                "a": respuesta,
                "activa": tiene_respuesta,
                "orden": orden * 10,
            },
        )
        if tiene_respuesta:
            activas += 1
        else:
            pendientes += 1

    print("[operator_faq] " + "-" * 56)
    print(f"[operator_faq] respuestas aprobadas (activas) ...... {activas:5d}")
    print(f"[operator_faq] preguntas sin respuesta (inactivas) . {pendientes:5d}")
    print(f"[operator_faq] TOTAL sembrado ...................... {activas + pendientes:5d}")
    print("[operator_faq] " + "-" * 56)


def downgrade() -> None:
    op.drop_index("ix_nota_factura", table_name="operator_note")
    op.drop_table("operator_note")
    op.drop_index("ix_faq_orden", table_name="operator_faq")
    op.drop_table("operator_faq")
