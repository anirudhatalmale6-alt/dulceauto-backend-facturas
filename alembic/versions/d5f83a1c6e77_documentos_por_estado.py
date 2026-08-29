"""documentos complementarios por estado

Milestone 4. Dos cambios, los dos aditivos.

1. invoice.delivery_date_latest
   La segunda fecha de entrega, "a mas tardar". Nace NULL en todas las facturas
   que ya existen, que es exactamente lo que tiene que pasar: sin segunda fecha
   el documento ensena una sola linea, igual que hasta ahora.

2. invoice_snapshot.doc_type
   El tipo de documento del que es copia congelada ese snapshot. Todo lo emitido
   hasta hoy es la pre-factura, asi que se rellena con 'factura'.

   Y con el, la clave unica pasa de (invoice_id, version) a
   (invoice_id, doc_type, version). Sin ese cambio, la pre-factura y los dos
   documentos complementarios de una misma reserva compartirian una sola
   numeracion de versiones: generar el "Pago de apartado confirmado" haria que
   la siguiente pre-factura fuese la v3, y el listado del historial los
   mezclaria. Era justo lo que el cliente pidio evitar por escrito.

Lo que esta migracion NO hace, a proposito
------------------------------------------
No mueve, no renombra y no borra ni una sola carpeta del historial que ya existe
en disco. Las rutas guardadas en pdf_path, html_path y assets_dir son un dato,
no una promesa: si se reescriben, los PDF ya emitidos dejan de abrirse. Los
snapshots de la pre-factura siguen en data/snapshots/<id>/v<n>/ y los
documentos nuevos se guardan en data/snapshots/<id>/<tipo>/v<n>/.

SQLite no sabe cambiar una restriccion en su sitio, asi que las dos operaciones
van dentro de batch_alter_table, que es lo que ya usan las migraciones
anteriores de este proyecto.

Revision ID: d5f83a1c6e77
Revises: c3a91f6e2b48
Create Date: 2026-08-29
"""
import sqlalchemy as sa
from alembic import op

revision = "d5f83a1c6e77"
down_revision = "c3a91f6e2b48"
branch_labels = None
depends_on = None

NOMBRE_VIEJO = "uq_snapshot_invoice_ver"
NOMBRE_NUEVO = "uq_snapshot_invoice_doc_ver"


def upgrade() -> None:
    with op.batch_alter_table("invoice") as batch:
        batch.add_column(sa.Column("delivery_date_latest", sa.Date(), nullable=True))

    # La columna entra permitiendo nulos, se rellena, y solo entonces se declara
    # obligatoria. Al reves, una tabla con filas fallaria al crear la columna.
    with op.batch_alter_table("invoice_snapshot") as batch:
        batch.add_column(sa.Column("doc_type", sa.String(length=32), nullable=True))

    op.execute("UPDATE invoice_snapshot SET doc_type = 'factura' WHERE doc_type IS NULL")

    with op.batch_alter_table(
        "invoice_snapshot",
        table_args=(sa.UniqueConstraint("invoice_id", "doc_type", "version", name=NOMBRE_NUEVO),),
    ) as batch:
        batch.alter_column(
            "doc_type",
            existing_type=sa.String(length=32),
            nullable=False,
            server_default="factura",
        )
        try:
            batch.drop_constraint(NOMBRE_VIEJO, type_="unique")
        except Exception:  # noqa: BLE001
            # En SQLite la restriccion viaja dentro del CREATE TABLE que
            # batch_alter_table vuelve a escribir, asi que puede no existir como
            # objeto que se pueda soltar. No es un fallo: la tabla nueva se crea
            # ya sin ella y con la nueva puesta.
            pass


def downgrade() -> None:
    with op.batch_alter_table(
        "invoice_snapshot",
        table_args=(sa.UniqueConstraint("invoice_id", "version", name=NOMBRE_VIEJO),),
    ) as batch:
        try:
            batch.drop_constraint(NOMBRE_NUEVO, type_="unique")
        except Exception:  # noqa: BLE001
            pass
        batch.drop_column("doc_type")

    with op.batch_alter_table("invoice") as batch:
        batch.drop_column("delivery_date_latest")
