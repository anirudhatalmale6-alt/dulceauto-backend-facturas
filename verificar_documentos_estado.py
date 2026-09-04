"""
Comprobacion del Milestone 4: documentos complementarios por estado.

Se ejecuta sin servidor, sobre una base de datos temporal, y genera PDF de
verdad con Chromium.

Lo que se comprueba no es "que salga un documento". Es, en este orden:

  1. que en las plantillas no quede ni un dato del cliente de la maqueta,
     porque saldria impreso en el documento de otro comprador;
  2. que ningun hueco quede MUDO -- marcado pero imposible de rellenar --,
     que es el fallo que no da error y se descubre cuando ya se ha mandado;
  3. que la cifra de dinero sea la formula acordada por escrito;
  4. que generar un documento NO toque el historial de los otros dos;
  5. que el aviso de legibilidad salte de verdad, porque un aviso que nunca
     se ha visto saltar no sirve de nada;
  6. y que la pre-factura siga comportandose exactamente igual que antes.

    ./.venv/bin/python verificar_documentos_estado.py
"""
import re
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import doctypes, documents
from app import pdf as pdf_engine
from app.config import settings
from app.db import Base
from app.models import (
    STATUS_DRAFT,
    STATUS_CANCELLED,
    STATUS_DELIVERED,
    STATUS_PENDING,
    STATUS_SCHEDULED,
    STATUS_VALIDATED,
    Invoice,
)

ok, fallos = [], []


def check(nombre, condicion, extra=""):
    (ok if condicion else fallos).append(nombre)
    print(("  OK   " if condicion else "  FALLA") + f" {nombre}" + (f"  [{extra}]" if extra else ""))


temporal = Path(tempfile.mkdtemp())
settings.data_dir = temporal
ruta_db = temporal / "pruebas.db"
engine = create_engine(f"sqlite:///{ruta_db.as_posix()}")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

DATOS = dict(
    locale="es-MX",
    status=STATUS_VALIDATED,
    issue_date=date(2026, 8, 21),
    valid_until=date(2026, 9, 20),
    authorization="AUT-2026-99010",
    customer_name="Cliente de la prueba de documentos",
    customer_email="cliente@ejemplo.mx",
    customer_phone="55 0000 1111",
    customer_city="Monterrey",
    vehicle_title="2020 Mazda CX-5 Signature",
    vehicle_location="Monterrey",
    vehicle_vin="JH4KA7561PC008269",
    vehicle_year="2020",
    vehicle_type="SUV",
    vehicle_mileage="24,100 km",
    vehicle_fuel="Gasolina",
    vehicle_transmission="Automática",
    pricing_vehicle_price=412500,
    pricing_reservation_amount=5000,
    pricing_currency="MXN",
    banking_bank="BBVA México",
    banking_beneficiary="DulceAuto México S.A. de C.V.",
    banking_account_number="012180001234567899",
    banking_bank_account="0123456789",
    delivery_date=date(2026, 9, 4),
    delivery_mode="home",
    representative_name="Yoselina de la Cruz",
    representative_role="Representante de operaciones",
    representative_phone="81 5555 0102",
    representative_email="soporte@dulceauto.mx",
    representative_hours="Lunes a viernes, 8:00 a. m.–4:00 p. m.",
    verify_url_base="https://dulceauto.mx/verificar/",
)

# Los datos del cliente de la maqueta que mandó el cliente. Ninguno puede
# quedar en la plantilla fuera de un hueco.
MAQUETA = [
    "Garcia Marquez Tomas",
    "$5,000 MXN",
    "$1,150,000 MXN",
    "Audi Q5 Sportback SLine Mild Hybrid",
    "RES-90010",
    "AUT-2026-87252",
    "29/08/2026",
    "55 1234 5678",
    "soporte@dulceauto.com",
]

NUEVOS = (doctypes.PAGO_APARTADO, doctypes.DOCUMENTACION)


def medidas(pdf: Path) -> tuple[float, float]:
    m = re.search(rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", pdf.read_bytes())
    if not m:
        return (0.0, 0.0)
    x0, y0, x1, y1 = (float(v) for v in m.groups())
    return (x1 - x0, y1 - y0)


def sin_marcar(fuente: str, literal: str) -> int:
    """Cuantas veces aparece ese literal FUERA de un hueco."""
    marcados = []
    for m in re.finditer(r'<(\w+)[^>]*data-field="[^"]+"[^>]*>', fuente):
        fin = fuente.find("</%s>" % m.group(1), m.end())
        marcados.append((m.start(), fin if fin > 0 else m.end()))
    return sum(
        1
        for m in re.finditer(re.escape(literal), fuente)
        if not any(a <= m.start() < b for a, b in marcados)
    )


# Desde que un complementario solo se emite en su estado (regla acordada el
# 29-ago-2026), las pruebas que generan un documento tienen que poner antes la
# factura en el estado que le corresponde. Se deduce de la pareja, no se escribe
# a mano, para que siga valiendo si la pareja cambia.
ESTADO_DE = {v: k for k, v in doctypes.PAREJA_POR_DEFECTO.items()}


def en_su_estado(db, factura, clave):
    """Deja la factura en el estado en el que ese documento se emite."""
    if clave != doctypes.FACTURA:
        factura.status = ESTADO_DE[clave]
        db.commit()
    return factura


print("\n1 · Las plantillas no llevan ni un dato del cliente de la maqueta")
for clave in NUEVOS:
    plantilla = documents.cargar("es-MX", clave)
    # Control positivo: si el literal de control NO aparece, la comprobacion de
    # ausencia de abajo no vale nada, porque estaria mirando un archivo que no
    # es el que creo.
    check(
        f"{clave}: la plantilla es la que creo (control positivo)",
        "data-field=" in plantilla.fuente and len(plantilla.fuente) > 5000,
        f"{len(plantilla.fuente)} bytes, {len(plantilla.huecos)} huecos",
    )
    total = sum(sin_marcar(plantilla.fuente, lit) for lit in MAQUETA)
    detalle = ", ".join(
        f"{lit}x{sin_marcar(plantilla.fuente, lit)}"
        for lit in MAQUETA
        if sin_marcar(plantilla.fuente, lit)
    )
    check(f"{clave}: ningun dato de la maqueta fuera de un hueco", total == 0, detalle)


print("\n1b · El micro-polish esta puesto y no se cae solo")
for clave in NUEVOS:
    fuente = documents.cargar("es-MX", clave).fuente
    # El marco va sobre .page-shell, la caja SIN escalar. Puesto en .page se
    # perderia por abajo y por la derecha, porque el documento va escalado con
    # transform y recortado por el overflow del shell.
    marco = fuente.split("@media print")[-1]
    check(f"{clave}: el marco va en el shell, no en la pagina escalada",
          "border:1px solid #d5dbe5" in marco and "border-radius:12px" in marco)
    check(f"{clave}: y la pagina de dentro sigue sin borde al imprimir",
          "border:none !important" in marco)
    # El grosor de icono igualado: una regla por tamano.
    check(f"{clave}: grosor de icono igualado por tamano",
          fuente.count('svg[width="') >= 8, f"{fuente.count('svg[width=')} reglas")

pago = documents.cargar("es-MX", doctypes.PAGO_APARTADO).fuente
check("la marca de agua no participa en la maqueta (posicion absoluta)",
      ".handshake{" in pago.replace("\n", "") or "position:absolute" in pago)
check("y el banner lleva fijado su alto de antes, para no encoger",
      "min-height:127px" in pago)

print("\n2 · Ningun hueco MUDO (marcado pero imposible de rellenar)")
# Un hueco con hijos dentro lo trata el motor como hueco de atributos y NO le
# cambia el contenido nunca. Es una regla a proposito -- mejor un hueco sin
# rellenar que tragarse marcado -- pero convierte un <br> de mas en la maqueta
# en un dato que se queda para siempre en el del cliente de ejemplo.
NO_SON_DE_TEXTO = (
    set(documents.SOLO_ATRIBUTOS)
    | set(documents.FOTOS)
    # Los huecos de la pagina 2 cuyo CONTENIDO lo escribe el servidor. Tienen
    # hijos en la plantilla, asi que el motor los trataria como de atributos si
    # no fuera porque los resuelve antes, en la rama de marcado generado.
    | set(documents.MARCADO)
    | {
        documents.LOGO,
        documents.SAFE_ICON,
        documents.DOC_TITLE,
    }
)
for clave in (doctypes.FACTURA, *NUEVOS):
    plantilla = documents.cargar("es-MX", clave)
    mudos = [
        h.campo
        for h in plantilla.huecos
        if h.campo and not h.es_de_texto and h.campo not in NO_SON_DE_TEXTO
    ]
    check(f"{clave}: no hay huecos mudos", not mudos, ", ".join(mudos))


print("\n3 · Los ganchos de marca y de foto estan puestos")
for clave in NUEVOS:
    fuente = documents.cargar("es-MX", clave).fuente
    check(f"{clave}: logotipo del perfil de marca", "data-logo" in fuente)
    check(f"{clave}: icono de compra segura", "data-safe-icon" in fuente)
    check(f"{clave}: titulo del documento", "data-doc-title" in fuente)
    check(f"{clave}: envoltorio de impresion", fuente.count("page-shell") >= 2)
    check(
        f"{clave}: ancho de diseno declarado",
        f"--design-width:{doctypes.tipo(clave).ancho}px" in fuente,
    )
check(
    "la foto sale de la factura, no va incrustada",
    "data-field=\"foto_1\"" in documents.cargar("es-MX", doctypes.PAGO_APARTADO).fuente
    and "base64" not in documents.cargar("es-MX", doctypes.PAGO_APARTADO).fuente,
)


with Session() as db:
    factura = Invoice(folio="RES-99010", **DATOS)
    db.add(factura)
    db.commit()

    print("\n4 · Pago restante = precio acordado - apartado validado")
    check(
        "la cuenta es la acordada",
        documents.importe_restante(factura) == 412500 - 5000,
        f"412500 - 5000 = {documents.importe_restante(factura)}",
    )
    html = documents.render(factura, doc=doctypes.DOCUMENTACION).html
    check("y sale impresa", "$407,500.00 MXN" in html)
    # El descuento, la cobertura y el transporte son texto, no importes, asi que
    # no pueden aplicarse dos veces. Se comprueba en lugar de suponerlo.
    factura.pricing_discount = "9% DE DESCUENTO APLICADO"
    factura.pricing_coverage = "Incluido"
    factura.pricing_transport = "Incluido"
    db.commit()
    check(
        "el descuento en texto no mueve la cifra",
        documents.importe_restante(factura) == 407500,
        f"{documents.importe_restante(factura)}",
    )

    # Sin uno de los dos numeros, hueco vacio y aviso. Nunca una cifra inventada.
    apartado = factura.pricing_reservation_amount
    factura.pricing_reservation_amount = None
    db.commit()
    check("sin apartado, no se inventa nada", documents.importe_restante(factura) is None)
    doc_vacio = documents.render(factura, doc=doctypes.DOCUMENTACION)
    check(
        "y la vista previa lo avisa",
        "payment.remaining_amount" in doc_vacio.vacios,
        ", ".join(doc_vacio.vacios),
    )
    factura.pricing_reservation_amount = apartado
    db.commit()

    print("\n5 · Entrega estimada: una fecha, dos fechas, ninguna")
    factura.delivery_date_latest = None
    db.commit()
    html = documents.render(factura, doc=doctypes.DOCUMENTACION).html
    check("con una sola fecha, una linea", "VIERNES 04/09" in html and "A MÁS TARDAR" not in html)
    factura.delivery_date_latest = date(2026, 9, 7)
    db.commit()
    html = documents.render(factura, doc=doctypes.DOCUMENTACION).html
    check(
        "con las dos, el rango",
        "VIERNES 04/09" in html and "A MÁS TARDAR" in html and "LUNES 07/09" in html,
    )
    check("y el salto es un <br> de verdad", "VIERNES 04/09<br>A MÁS TARDAR<br>LUNES 07/09" in html)
    guardada = factura.delivery_date
    factura.delivery_date = None
    db.commit()
    html = documents.render(factura, doc=doctypes.DOCUMENTACION).html
    check("sin fecha de entrega, el hueco queda vacio", "A MÁS TARDAR" not in html)
    factura.delivery_date = guardada
    factura.delivery_date_latest = date(2026, 9, 7)
    db.commit()

    print("\n6 · Los textos y la barra se mueven con el estado")
    # Este es el motivo por el que el cliente eligio la opcion (b): el archivo
    # llevaba escrita la pastilla PAGO VALIDADO y una barra con "Entrega
    # coordinada" pendiente, asi que emitido en Entrega coordinada le habria
    # dicho al comprador que su entrega no esta coordinada.
    vistos = {}
    for estado in (STATUS_PENDING, STATUS_VALIDATED, STATUS_SCHEDULED, STATUS_DELIVERED, STATUS_CANCELLED):
        factura.status = estado
        db.commit()
        vistos[estado] = documents.render(factura, doc=doctypes.PAGO_APARTADO).html

    check(
        "en Pago validado, el paso 3 esta activo",
        'class="step active" data-step="3"' in vistos[STATUS_VALIDATED],
    )
    check(
        "en Entrega coordinada, el paso 4 pasa a activo y el 3 a hecho",
        'class="step done" data-step="3"' in vistos[STATUS_SCHEDULED]
        and 'class="step active" data-step="4"' in vistos[STATUS_SCHEDULED],
    )
    check(
        "en Entrega coordinada NO dice que la entrega esta pendiente",
        "avanzamos con la documentación y coordinación de entrega"
        not in vistos[STATUS_SCHEDULED],
    )
    check(
        "y si dice que ya esta coordinada",
        "Su entrega ya está coordinada" in vistos[STATUS_SCHEDULED],
    )
    check("la pastilla sigue al estado", "ENTREGA COORDINADA" in vistos[STATUS_SCHEDULED])
    check(
        "en Cancelada no promete ninguna gestion",
        "Operación cancelada" in vistos[STATUS_CANCELLED],
    )
    check(
        "los cinco estados dan documentos distintos",
        len({v for v in vistos.values()}) == 5,
        f"{len({v for v in vistos.values()})} distintos de 5",
    )

    # Y lo mismo en el otro documento.
    factura.status = STATUS_SCHEDULED
    db.commit()
    coordinada = documents.render(factura, doc=doctypes.DOCUMENTACION).html
    check(
        "Documentacion validada en Entrega coordinada no reclama el pago",
        "Solo falta acreditar el pago restante" not in coordinada,
    )

    print("\n7 · Cada documento lleva su propio historial")
    factura.status = STATUS_VALIDATED
    db.commit()
    versiones = {}
    for clave in (doctypes.FACTURA, *NUEVOS):
        en_su_estado(db, factura, clave)
        r = pdf_engine.generar(db, factura, clave)
        db.commit()
        versiones[clave] = r
        check(f"{clave}: se genera", r.pdf.exists(), r.pdf.name)
        check(f"{clave}: una sola hoja A4", r.paginas == 1, f"{r.paginas} pagina(s)")
        ancho, alto = medidas(r.pdf)
        check(
            f"{clave}: la hoja es A4",
            abs(ancho - 595.3) < 1.5 and abs(alto - 841.9) < 1.5,
            f"{ancho:.1f} x {alto:.1f} pt",
        )

    check(
        "los tres empiezan en la version 1, cada uno por su cuenta",
        all(r.snapshot.version == 1 for r in versiones.values()),
        ", ".join(f"{k}=v{v.snapshot.version}" for k, v in versiones.items()),
    )
    check(
        "y en carpetas distintas",
        len({r.pdf.parent for r in versiones.values()}) == 3,
    )
    check(
        "la pre-factura conserva su ruta de siempre, sin el tipo por medio",
        versiones[doctypes.FACTURA].pdf.parent.name == "v1"
        and versiones[doctypes.FACTURA].pdf.parent.parent.name == str(factura.id),
        str(versiones[doctypes.FACTURA].pdf.parent),
    )

    # Regenerar UNO no puede mover el numero de los otros dos.
    antes = {c: len(pdf_engine.snapshots_de(db, factura.id, c)) for c in (doctypes.FACTURA, *NUEVOS)}
    en_su_estado(db, factura, doctypes.PAGO_APARTADO)
    r2 = pdf_engine.generar(db, factura, doctypes.PAGO_APARTADO)
    db.commit()
    despues = {c: len(pdf_engine.snapshots_de(db, factura.id, c)) for c in (doctypes.FACTURA, *NUEVOS)}
    check("al regenerar uno, ese sube a v2", r2.snapshot.version == 2)
    check(
        "y los otros dos no se mueven",
        despues[doctypes.FACTURA] == antes[doctypes.FACTURA]
        and despues[doctypes.DOCUMENTACION] == antes[doctypes.DOCUMENTACION],
        f"{antes} -> {despues}",
    )
    check(
        "el PDF de la pre-factura sigue en su disco, intacto",
        versiones[doctypes.FACTURA].pdf.exists(),
    )
    check(
        "el historial por defecto sigue siendo el de la pre-factura",
        all(s.doc_type == doctypes.FACTURA for s in pdf_engine.snapshots_de(db, factura.id)),
    )
    check(
        "y pedidos todos, salen los tres tipos",
        len({s.doc_type for s in pdf_engine.snapshots_de(db, factura.id, None)}) == 3,
    )

    print("\n8 · El nombre del archivo dice de que documento es")
    check(
        "pago de apartado",
        versiones[doctypes.PAGO_APARTADO].pdf.name == "RES-99010-pago-apartado.pdf",
        versiones[doctypes.PAGO_APARTADO].pdf.name,
    )
    check(
        "documentacion validada",
        versiones[doctypes.DOCUMENTACION].pdf.name == "RES-99010-documentacion-validada.pdf",
        versiones[doctypes.DOCUMENTACION].pdf.name,
    )
    check(
        "y la pre-factura conserva el suyo de siempre",
        versiones[doctypes.FACTURA].pdf.name == "RES-99010.pdf",
        versiones[doctypes.FACTURA].pdf.name,
    )

    print("\n9 · La copia congelada esta completa")
    for clave in NUEVOS:
        carpeta = versiones[clave].pdf.parent
        html = (carpeta / "documento.html").read_text(encoding="utf-8")
        faltan = [
            r
            for r in re.findall(r'(?:src|href)="(assets/[^"]+)"', html)
            if not (carpeta / r).exists()
        ]
        check(f"{clave}: no falta ningun archivo que el documento use", not faltan, ", ".join(faltan))
        check(
            f"{clave}: y no queda ningun dato de la maqueta",
            not any(lit in html for lit in MAQUETA),
        )

    print("\n10 · Caso limite realista: sigue cabiendo en una hoja")
    factura.customer_name = "María de los Ángeles Fernández de la Torre y Villaseñor"
    factura.vehicle_title = "2015 Audi A3 1.8T S Line Convertible AT Quattro Edición Especial"
    factura.delivery_text = (
        "Traslado asegurado en transporte terrestre especializado hasta el domicilio "
        "registrado en la Ciudad de México, con seguro a todo riesgo durante el trayecto "
        "y confirmación telefónica previa de fecha y horario con el titular."
    )
    for clave in NUEVOS:
        en_su_estado(db, factura, clave)
        r = pdf_engine.generar(db, factura, clave)
        db.commit()
        check(f"{clave}: con datos largos y dos fechas, una hoja", r.paginas == 1, f"escala {r.escala:.4f}")

    print("\n11 · El aviso de legibilidad salta de verdad")
    # Un aviso que nunca se ha visto saltar no es un aviso. Se fuerza a proposito.
    factura.delivery_text = "Traslado asegurado hasta el domicilio registrado. " * 200
    db.commit()
    for clave in NUEVOS:
        en_su_estado(db, factura, clave)
        antes_n = len(pdf_engine.snapshots_de(db, factura.id, clave))
        carpetas_antes = sorted(p.name for p in versiones[clave].pdf.parent.parent.iterdir())
        try:
            pdf_engine.generar(db, factura, clave)
            db.commit()
            check(f"{clave}: avisa en vez de imprimir ilegible", False, "ha generado el PDF")
        except pdf_engine.PdfIlegible as exc:
            db.rollback()
            check(f"{clave}: avisa en vez de imprimir ilegible", True, str(exc)[:60] + "...")
            check(
                f"{clave}: el aviso dice cuanto sobra",
                "%" in str(exc) and "pt" in str(exc),
            )
            check(
                f"{clave}: no deja ninguna version a medias",
                len(pdf_engine.snapshots_de(db, factura.id, clave)) == antes_n,
            )
            check(
                f"{clave}: ni ninguna carpeta huerfana",
                sorted(p.name for p in versiones[clave].pdf.parent.parent.iterdir())
                == carpetas_antes,
            )

    print("\n12 · Control negativo: la pre-factura NO cambia de comportamiento")
    # Con ese mismo texto imposible, la pre-factura tiene que seguir generando
    # como hasta hoy. Si tambien fallara, habria cambiado algo que ya funcionaba.
    try:
        r = pdf_engine.generar(db, factura, doctypes.FACTURA)
        db.commit()
        check("la pre-factura sigue generando con el mismo texto", r.paginas == 1, f"escala {r.escala:.4f}")
    except pdf_engine.PdfError as exc:
        check("la pre-factura sigue generando con el mismo texto", False, str(exc)[:70])

    print("\n13 · Los mercados sin estos documentos no se rompen")
    otra = Invoice(folio="RES-99011", **{**DATOS, "locale": "en"})
    db.add(otra)
    db.commit()
    for clave in NUEVOS:
        check(f"{clave}: no existe para 'en'", not doctypes.existe_para(clave, "en"))
        en_su_estado(db, otra, clave)
        try:
            pdf_engine.generar(db, otra, clave)
            check(f"{clave}: se niega a generarlo en 'en'", False, "lo ha generado")
        except pdf_engine.PdfError as exc:
            check(f"{clave}: se niega a generarlo en 'en'", "es-MX" in str(exc), str(exc)[:60])
    r = pdf_engine.generar(db, otra, doctypes.FACTURA)
    db.commit()
    check("y la pre-factura en ingles se sigue generando igual", r.paginas == 1)

    print("\n14 · La fotografia sale de la factura, no de la plantilla")
    from PIL import Image

    from app import uploads
    from app.models import InvoicePhoto

    # Una foto reconocible de un color que no existe en la plantilla. Comprobar
    # que "hay una imagen" no diria nada: la plantilla ya trae una.
    rel = "uploads/fotos/prueba-verde.jpg"
    destino = temporal / rel
    destino.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1200, 800), (16, 140, 60)).save(destino, quality=92)
    check("control positivo: la foto de prueba esta en su sitio",
          uploads.ruta_absoluta(rel) is not None)

    conFoto = Invoice(folio="RES-99012", **{**DATOS, "vehicle_title": "2021 Kia Sportage"})
    db.add(conFoto)
    db.commit()
    db.add(InvoicePhoto(invoice_id=conFoto.id, position=1, file_path=rel,
                        original_name="prueba-verde.jpg"))
    db.commit()
    db.refresh(conFoto)
    r = pdf_engine.generar(db, conFoto, doctypes.PAGO_APARTADO)
    db.commit()
    imagen = Image.open(r.pdf.parent / "assets" / "img" / "vehicle-front.jpg").convert("RGB")
    px = imagen.getpixel((imagen.width // 2, imagen.height // 2))
    check("la copia congelada lleva la foto subida, no la de la plantilla",
          px[1] > px[0] + 40 and px[1] > px[2] + 40, f"pixel central {px}")
    html = (r.pdf.parent / "documento.html").read_text(encoding="utf-8")
    check('y el texto alternativo pasa a ser el vehiculo de la factura',
          'alt="2021 Kia Sportage"' in html)
    check("con foto propia sigue siendo una hoja", r.paginas == 1)


    print("\n15 · Un documento solo se emite en el estado que le corresponde")
    # Lo encontro el cliente el 29-ago-2026: se podia emitir "Pago de apartado
    # confirmado" sobre un folio en PAGO PENDIENTE, y el documento salia
    # diciendo a la vez que el pago estaba confirmado y que se seguia esperando.
    from app.models import Setting

    for estado, clave in doctypes.PAREJA_POR_DEFECTO.items():
        db.add(Setting(key=doctypes.clave_ajuste(estado), market=None,
                       value=clave, is_sensitive=False))
    db.commit()

    ESPERADO = {
        STATUS_DRAFT:     {doctypes.FACTURA: True,  doctypes.PAGO_APARTADO: False, doctypes.DOCUMENTACION: False},
        STATUS_PENDING:   {doctypes.FACTURA: True,  doctypes.PAGO_APARTADO: False, doctypes.DOCUMENTACION: False},
        STATUS_VALIDATED: {doctypes.FACTURA: True,  doctypes.PAGO_APARTADO: True,  doctypes.DOCUMENTACION: False},
        STATUS_SCHEDULED: {doctypes.FACTURA: True,  doctypes.PAGO_APARTADO: False, doctypes.DOCUMENTACION: True},
        STATUS_DELIVERED: {doctypes.FACTURA: True,  doctypes.PAGO_APARTADO: False, doctypes.DOCUMENTACION: False},
        STATUS_CANCELLED: {doctypes.FACTURA: True,  doctypes.PAGO_APARTADO: False, doctypes.DOCUMENTACION: False},
    }
    for estado, esperado in ESPERADO.items():
        real = {c: doctypes.puede_generarse(db, c, estado)[0] for c in esperado}
        check(f"estado {estado}: la regla es la acordada", real == esperado,
              ", ".join(f"{c.split('_')[0]}={'si' if v else 'no'}" for c, v in real.items()))

    # Y sobre todo: que el MOTOR lo rechace de verdad, no solo la funcion.
    # Es el punto por el que pasa cualquier via, asi que tocar la URL no lo salta.
    prueba = Invoice(folio="RES-99040", **{**DATOS, "status": STATUS_PENDING})
    db.add(prueba)
    db.commit()
    carpeta_base = temporal / "snapshots" / str(prueba.id)
    filas_antes = len(pdf_engine.snapshots_de(db, prueba.id, None))
    try:
        pdf_engine.generar(db, prueba, doctypes.PAGO_APARTADO)
        db.commit()
        check("el motor rechaza el documento que no toca", False, "lo ha generado")
    except pdf_engine.PdfEstadoNoCorresponde as exc:
        db.rollback()
        check("el motor rechaza el documento que no toca", True, str(exc)[:58] + "...")
        check("y el aviso dice cual es el que si corresponde",
              "Configuración" in str(exc) or "complementario" in str(exc))
        check("sin crear ninguna version",
              len(pdf_engine.snapshots_de(db, prueba.id, None)) == filas_antes)
        check("ni ninguna carpeta en el disco", not carpeta_base.exists())

    # La pre-factura no cambia: se genera en cualquier estado, como siempre.
    for estado in (STATUS_PENDING, STATUS_CANCELLED, STATUS_DELIVERED):
        prueba.status = estado
        db.commit()
        r = pdf_engine.generar(db, prueba, doctypes.FACTURA)
        db.commit()
        check(f"la pre-factura se sigue generando en {estado}", r.paginas == 1)

    # Y en el estado bueno, el complementario sale.
    prueba.status = STATUS_VALIDATED
    db.commit()
    r = pdf_engine.generar(db, prueba, doctypes.PAGO_APARTADO)
    db.commit()
    check("en Pago validado, el complementario si se emite", r.paginas == 1, f"v{r.snapshot.version}")

    # Si el cliente pone "Ninguno" para un estado, no se emite nada ahi.
    fila = db.execute(
        select(Setting).where(Setting.key == doctypes.clave_ajuste(STATUS_VALIDATED),
                              Setting.market.is_(None))
    ).scalar_one()
    fila.value = ""
    db.commit()
    check("con «Ninguno» en Configuracion, ese estado deja de emitir",
          not doctypes.puede_generarse(db, doctypes.PAGO_APARTADO, STATUS_VALIDATED)[0])
    fila.value = doctypes.PAGO_APARTADO
    db.commit()


print("\n" + "=" * 62)
print(f"{len(ok)} comprobaciones correctas, {len(fallos)} fallos")
for f in fallos:
    print("  FALLA:", f)
shutil.rmtree(temporal, ignore_errors=True)
sys.exit(1 if fallos else 0)
