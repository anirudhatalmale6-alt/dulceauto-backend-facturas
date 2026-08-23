"""perfiles de marca, registro permanente de folios y archivado

Bloque A del tuneup V1.1. Tres cosas, una sola migracion:

1. brand_profile: la ficha de marca (nombre, logotipo, icono de "Compra
   segura" y titulo del documento). La factura guarda ademas el nombre y el
   titulo COPIADOS, igual que ya hace con los datos bancarios, para que
   corregir manana un perfil no cambie un documento ya emitido.

2. folio_ledger: todos los folios que han existido. Se rellena con los que ya
   hay en la base, de modo que ningun numero anterior a esta migracion se
   pueda reciclar aunque despues se borre su factura.

3. invoice.archived_at: sacar del listado una cancelada sin destruirla.

Es puramente aditiva. Crea tablas y columnas nuevas y rellena la tabla nueva
leyendo lo que ya hay; no modifica ni borra ninguna fila existente.

Sobre el relleno del registro: hoy la unica fuente posible es invoice.folio,
porque el folio se asigna siempre desde el contador y no se lee nunca del
formulario, de modo que un folio no cambia despues de creado, y porque hasta
esta version no existia ninguna forma de borrar una factura. Aun asi se cruza
tambien con activity_log.folio, que no tiene clave foranea y por tanto conserva
folios de operaciones antiguas. Sale gratis y sirve de comprobacion
independiente: si apareciera alguno que no esta en invoice, se anota igual.

Revision ID: b7e41c9d5f02
Revises: 9c1f4b7d2a10
Create Date: 2026-08-23
"""
import sqlalchemy as sa
from alembic import op

revision = "b7e41c9d5f02"
down_revision = "9c1f4b7d2a10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brand_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("doc_title", sa.String(length=200), nullable=True),
        sa.Column("logo_path", sa.String(length=255), nullable=True),
        sa.Column("safe_icon_path", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # El folio es la clave primaria a proposito: que sea la propia base de datos
    # la que impida anotar dos veces el mismo, sin depender del codigo.
    # invoice_id va sin clave foranea, igual que en activity_log: la anotacion
    # tiene que sobrevivir al borrado de la factura, que es justo para lo que
    # existe esta tabla.
    op.create_table(
        "folio_ledger",
        sa.Column("folio", sa.String(length=32), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="auto"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("folio"),
    )

    with op.batch_alter_table("invoice") as batch:
        batch.add_column(sa.Column("brand_profile_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("brand_name", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("brand_doc_title", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key(
            "fk_invoice_brand_profile",
            "brand_profile",
            ["brand_profile_id"],
            ["id"],
            ondelete="SET NULL",
        )

    _rellenar_registro_de_folios()


def _rellenar_registro_de_folios() -> None:
    """Anota en el registro todos los folios que ya existen.

    Se marcan como 'backfill' para poder distinguir luego, mirando la tabla,
    cuales venian de antes de que existiera el registro.
    """
    conn = op.get_bind()

    de_facturas = {
        f for (f,) in conn.execute(sa.text("SELECT folio FROM invoice WHERE folio IS NOT NULL"))
    }
    de_actividad = {
        f
        for (f,) in conn.execute(
            sa.text(
                "SELECT DISTINCT folio FROM activity_log "
                "WHERE folio IS NOT NULL AND folio <> ''"
            )
        )
    }

    insertar = sa.text(
        "INSERT INTO folio_ledger (folio, invoice_id, source, created_at) "
        "SELECT :folio, (SELECT id FROM invoice WHERE folio = :folio), "
        "       'backfill', CURRENT_TIMESTAMP "
        "WHERE NOT EXISTS (SELECT 1 FROM folio_ledger WHERE folio = :folio)"
    )
    for folio in sorted(de_facturas | de_actividad):
        conn.execute(insertar, {"folio": folio})

    (anotados,) = conn.execute(sa.text("SELECT COUNT(*) FROM folio_ledger")).one()
    solo_en_actividad = sorted(de_actividad - de_facturas)

    print(
        f"[folio_ledger] folios en facturas: {len(de_facturas)} | "
        f"folios en actividad: {len(de_actividad)} | anotados en total: {anotados}"
    )
    if solo_en_actividad:
        # Folios que la actividad recuerda y que ya no tienen factura. Se anotan
        # igual, porque existieron y la regla es que un folio usado no vuelve.
        # Se avisa porque cambia algo que se nota: esos numeros tampoco se
        # podran escribir a mano en el modo Manual.
        muestra = ", ".join(solo_en_actividad[:5])
        if len(solo_en_actividad) > 5:
            muestra += f", … {solo_en_actividad[-1]}"
        print(
            f"[folio_ledger] AVISO: {len(solo_en_actividad)} folios aparecen en la "
            f"actividad pero ya no tienen factura ({muestra}). Se reservan igual."
        )
    else:
        print("[folio_ledger] las dos fuentes coinciden, sin diferencias.")


def downgrade() -> None:
    with op.batch_alter_table("invoice") as batch:
        batch.drop_constraint("fk_invoice_brand_profile", type_="foreignkey")
        batch.drop_column("archived_at")
        batch.drop_column("brand_doc_title")
        batch.drop_column("brand_name")
        batch.drop_column("brand_profile_id")
    op.drop_table("folio_ledger")
    op.drop_table("brand_profile")
