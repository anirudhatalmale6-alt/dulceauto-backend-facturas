"""Las verificaciones de la pagina 2.

Una sola columna de texto en invoice: las claves de las verificaciones que el
administrador ha marcado para esa unidad, separadas por comas. Sin tablas
nuevas, que es lo que pidio el cliente.

Entra permitiendo nulos y sin valor por defecto. Eso deja a TODAS las facturas
anteriores con "ninguna verificacion marcada", que es lo correcto: nadie las
comprobo, asi que su documento no puede afirmarlas. La maqueta ensena las seis
palomitas puestas, pero la maqueta es un dibujo y esto se le entrega a un
comprador.

Las veinte fotografias del album NO necesitan migracion. invoice_photo solo
tenia una restriccion sobre position -que no se repita dentro de una factura- y
esa no cambia; el tope de cuatro estaba escrito en el codigo, no en la tabla.
Se comprobo leyendo el esquema de la base, no de memoria.

Revision ID: e2b7c04a91d3
Revises: d5f83a1c6e77
Create Date: 2026-09-04
"""
import sqlalchemy as sa
from alembic import op

revision = "e2b7c04a91d3"
down_revision = "d5f83a1c6e77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoice") as batch:
        batch.add_column(sa.Column("verifications", sa.String(length=200), nullable=True))


def downgrade() -> None:
    # Volver atras BORRA que verificaciones tenia marcada cada factura. No hay
    # forma de recuperarlo despues, asi que conviene tener la copia de la base
    # que desplegar.sh hace antes de migrar.
    with op.batch_alter_table("invoice") as batch:
        batch.drop_column("verifications")
