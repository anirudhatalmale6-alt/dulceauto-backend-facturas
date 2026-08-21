"""estados de operacion y marcas de tiempo del PDF

Separa el estado comercial de la reserva de las acciones que hacemos nosotros
sobre el PDF.

Antes, "PDF generado" y "Enviada" ocupaban un estado. Eso hacia imposible saber
si una factura con el PDF hecho estaba cobrada o no: el estado se lo habia
llevado una accion nuestra. Ahora esas dos acciones son marcas de tiempo y el
estado describe unicamente en que punto esta la operacion para el cliente:

    draft -> pending -> payment_validated -> delivery_scheduled -> delivered
    (+ cancelled, que no es un punto del recorrido sino una salida)

Las facturas que estuvieran en "generated" o "sent" pasan a "pending", que es
donde estaban de verdad: se les habia generado el PDF, no se les habia cobrado.
La fecha de la accion se rellena con la de ultima modificacion de la factura,
porque es lo unico que hay. No se intenta reconstruirla con mas precision a
proposito: seria inventar un dato, y con el cliente quedo hablado que en este
momento solo hay datos de desarrollo.

Revision ID: 9c1f4b7d2a10
Revises: 4426779e3621
Create Date: 2026-08-21
"""
import sqlalchemy as sa
from alembic import op

revision = "9c1f4b7d2a10"
down_revision = "4426779e3621"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoice", sa.Column("pdf_generated_at", sa.DateTime(), nullable=True))
    op.add_column("invoice", sa.Column("sent_at", sa.DateTime(), nullable=True))

    # El orden importa: primero se guarda la marca de tiempo y despues se cambia
    # el estado. Al reves, la condicion ya no encontraria ninguna fila.
    op.execute(
        "UPDATE invoice SET pdf_generated_at = updated_at "
        "WHERE status IN ('generated', 'sent') AND pdf_generated_at IS NULL"
    )
    op.execute(
        "UPDATE invoice SET sent_at = updated_at WHERE status = 'sent' AND sent_at IS NULL"
    )
    op.execute("UPDATE invoice SET status = 'pending' WHERE status IN ('generated', 'sent')")


def downgrade() -> None:
    # Se recupera el estado antiguo a partir de las marcas de tiempo, que es la
    # unica informacion que existe. Primero "sent", que es el mas avanzado.
    op.execute("UPDATE invoice SET status = 'generated' WHERE pdf_generated_at IS NOT NULL")
    op.execute("UPDATE invoice SET status = 'sent' WHERE sent_at IS NOT NULL")
    # Los estados nuevos no existian antes de esta revision.
    op.execute(
        "UPDATE invoice SET status = 'pending' "
        "WHERE status IN ('payment_validated', 'delivery_scheduled', 'delivered')"
    )
    op.drop_column("invoice", "sent_at")
    op.drop_column("invoice", "pdf_generated_at")
