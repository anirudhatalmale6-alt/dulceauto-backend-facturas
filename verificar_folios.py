"""
Comprobacion de la asignacion de folios.

Se ejecuta sin servidor, sobre una base de datos temporal, porque lo que se
comprueba aqui no se puede provocar desde el navegador: el choque entre dos
operadores que crean una factura en el mismo instante.

    ./.venv/bin/python verificar_folios.py
"""
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.invoices import FolioOcupado, commit_creation, next_folio
from app.models import STATUS_DRAFT, Invoice, InvoicePhoto, Setting

ok, fallos = 0, []


def check(nombre, condicion, extra=""):
    global ok
    if condicion:
        ok += 1
        print(f"  OK    {nombre}" + (f"  [{extra}]" if extra else ""))
    else:
        fallos.append(nombre)
        print(f"  FALLA {nombre}" + (f"  [{extra}]" if extra else ""))


ruta = Path(tempfile.mkdtemp()) / "folios.db"
engine = create_engine(f"sqlite:///{ruta.as_posix()}")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)


def nueva(db, folio=None):
    """Factura minima. Si no se le da folio, coge el que toque del contador."""
    inv = Invoice(status=STATUS_DRAFT, locale="es-MX")
    inv.folio = folio or next_folio(db)
    db.add(inv)
    return inv


with Session() as db:
    db.add(Setting(key="folio.prefix", market=None, value="RES-"))
    db.add(Setting(key="folio.next", market=None, value="87241"))
    db.commit()

    print("\n1 · El contador avanza")
    a = commit_creation(db, nueva)
    b = commit_creation(db, nueva)
    check("primer folio", a.folio == "RES-87241", a.folio)
    check("el siguiente no lo repite", b.folio == "RES-87242", b.folio)
    check("mantiene el ancho del contador", len(b.folio.split("-")[1]) == 5, b.folio)

    print("\n2 · Si el folio que toca ya esta ocupado, se salta")
    # Alguien creo a mano la factura que le tocaria al contador.
    db.add(Invoice(folio="RES-87243", status=STATUS_DRAFT, locale="es-MX"))
    db.commit()
    c = commit_creation(db, nueva)
    check("coge el siguiente libre en vez de fallar", c.folio == "RES-87244", c.folio)

    print("\n3 · Choque simultaneo entre dos operadores")
    # Este es el caso que pidio el cliente: dos personas creando una factura a
    # la vez con la misma cuenta Admin. El segundo en llegar se encuentra el
    # folio ya insertado y la base de datos rechaza el suyo.
    #
    # Se simula haciendo que el PRIMER intento de construccion use a proposito
    # un folio que ya existe. commit_creation debe recoger el error de clave
    # unica, deshacer y volver a intentarlo con uno libre.
    intentos = {"n": 0}

    def construir_con_choque(session):
        intentos["n"] += 1
        if intentos["n"] == 1:
            return nueva(session, folio=c.folio)  # folio ya ocupado
        return nueva(session)

    antes = db.execute(select(Invoice.id)).all()
    d = commit_creation(db, construir_con_choque)
    despues = db.execute(select(Invoice.id)).all()

    check("hubo que reintentar", intentos["n"] == 2, f"{intentos['n']} intentos")
    check("la factura se guarda igualmente", d.id is not None)
    check("con un folio distinto al que choco", d.folio != c.folio, f"{c.folio} -> {d.folio}")
    check("y no se queda ninguna factura a medias", len(despues) == len(antes) + 1)

    print("\n4 · Si no hay manera, se avisa en vez de insistir")
    def siempre_choca(session):
        return nueva(session, folio=d.folio)

    try:
        commit_creation(db, siempre_choca, intentos=3)
        check("se rinde con un error propio", False, "no lanzo nada")
    except FolioOcupado:
        check("se rinde con un error propio", True)
    except Exception as exc:  # pragma: no cover
        check("se rinde con un error propio", False, type(exc).__name__)

    print("\n5 · Un error que no sea de folio no se traga")
    # Reintentar a ciegas cualquier error de integridad esconderia fallos
    # reales. Solo se reintenta el choque de folio.
    def viola_otra_cosa(session):
        inv = nueva(session)
        session.flush()
        # Dos fotografias en la misma posicion: choque de clave unica que no
        # tiene nada que ver con el folio.
        session.add(InvoicePhoto(invoice_id=inv.id, position=1, file_path="a.jpg"))
        session.add(InvoicePhoto(invoice_id=inv.id, position=1, file_path="b.jpg"))
        return inv

    try:
        commit_creation(db, viola_otra_cosa)
        check("un error ajeno al folio sale a la superficie", False, "no lanzo nada")
    except FolioOcupado:
        check("un error ajeno al folio sale a la superficie", False, "lo confundio con folio")
    except IntegrityError:
        check("un error ajeno al folio sale a la superficie", True)

    db.rollback()

    print("\n6 · Un NOT NULL sobre el folio tampoco se confunde con un choque")
    # Buscar solo la palabra "folio" en el mensaje haria que un NOT NULL se
    # reintentara seis veces y acabara disfrazado de folio ocupado.
    def folio_vacio(session):
        inv = Invoice(status=STATUS_DRAFT, locale="es-MX", folio=None)
        session.add(inv)
        return inv

    try:
        commit_creation(db, folio_vacio, intentos=2)
        check("un NOT NULL sale a la superficie", False, "no lanzo nada")
    except FolioOcupado:
        check("un NOT NULL sale a la superficie", False, "lo confundio con folio ocupado")
    except IntegrityError:
        check("un NOT NULL sale a la superficie", True)

print(f"\n{'=' * 58}\n{ok} comprobaciones correctas, {len(fallos)} fallos")
sys.exit(1 if fallos else 0)
