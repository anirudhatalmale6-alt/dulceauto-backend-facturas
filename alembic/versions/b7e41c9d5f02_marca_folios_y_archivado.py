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
    """Anota en el registro los folios que ya existen, en tres niveles.

    La regla la fijo el cliente y distingue un folio historico de verdad de uno
    que solo nacio en una bateria de pruebas:

      1. El folio todavia tiene factura                    -> se reserva.
      2. Ya no tiene factura, pero hay constancia de que
         llego a emitirse documento (snapshot vivo, o un
         'pdf_generated' en la actividad)                  -> se reserva.
      3. Solo aparece en la actividad, sin factura y sin
         constancia de documento emitido                   -> se informa, pero
                                                              NO se bloquea.

    El motivo del tercer nivel: mantener la regla de seguridad -- nada que haya
    podido formar parte de un documento real se reutiliza -- sin inutilizar
    rangos enteros por folios creados solo por pruebas automaticas.

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

    # Constancia de documento emitido para un folio que ya no tiene factura.
    # Dos fuentes independientes:
    #   - un snapshot que haya sobrevivido a su factura
    #   - una accion 'pdf_generated' en la actividad, que no se borra nunca
    #     porque su entity_id no tiene clave foranea
    con_snapshot = {
        f
        for (f,) in conn.execute(
            sa.text("SELECT DISTINCT folio FROM invoice_snapshot WHERE folio IS NOT NULL")
        )
    }
    con_pdf = {
        f
        for (f,) in conn.execute(
            sa.text(
                "SELECT DISTINCT folio FROM activity_log "
                "WHERE action = 'pdf_generated' AND folio IS NOT NULL AND folio <> ''"
            )
        )
    }

    huerfanos = de_actividad - de_facturas
    huerfanos_con_documento = huerfanos & (con_snapshot | con_pdf)
    huerfanos_de_prueba = huerfanos - huerfanos_con_documento

    a_reservar = de_facturas | huerfanos_con_documento

    insertar = sa.text(
        "INSERT INTO folio_ledger (folio, invoice_id, source, created_at) "
        "SELECT :folio, (SELECT id FROM invoice WHERE folio = :folio), "
        "       :origen, CURRENT_TIMESTAMP "
        "WHERE NOT EXISTS (SELECT 1 FROM folio_ledger WHERE folio = :folio)"
    )
    for folio in sorted(a_reservar):
        conn.execute(
            insertar,
            {
                "folio": folio,
                "origen": "backfill" if folio in de_facturas else "backfill-doc",
            },
        )

    (anotados,) = conn.execute(sa.text("SELECT COUNT(*) FROM folio_ledger")).one()

    def _muestra(conjunto: set) -> str:
        orden = sorted(conjunto)
        if not orden:
            return "ninguno"
        if len(orden) <= 6:
            return ", ".join(orden)
        return ", ".join(orden[:5]) + f", … {orden[-1]}"

    print("[folio_ledger] " + "-" * 58)
    print(f"[folio_ledger] 1. folios con factura ................ {len(de_facturas):5d}  RESERVADOS")
    print(f"[folio_ledger] 2. huérfanos CON documento emitido .... {len(huerfanos_con_documento):5d}  RESERVADOS")
    if huerfanos_con_documento:
        print(f"[folio_ledger]    {_muestra(huerfanos_con_documento)}")
    print(f"[folio_ledger] 3. huérfanos SIN documento (pruebas) .. {len(huerfanos_de_prueba):5d}  no se bloquean")
    if huerfanos_de_prueba:
        print(f"[folio_ledger]    {_muestra(huerfanos_de_prueba)}")
    print(f"[folio_ledger] folios distintos en actividad ........ {len(de_actividad):5d}")
    print(f"[folio_ledger] TOTAL anotado en el registro ......... {anotados:5d}")
    print("[folio_ledger] " + "-" * 58)


def downgrade() -> None:
    with op.batch_alter_table("invoice") as batch:
        batch.drop_constraint("fk_invoice_brand_profile", type_="foreignkey")
        batch.drop_column("archived_at")
        batch.drop_column("brand_doc_title")
        batch.drop_column("brand_name")
        batch.drop_column("brand_profile_id")
    op.drop_table("folio_ledger")
    op.drop_table("brand_profile")
