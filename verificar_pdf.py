"""
Comprobacion de la generacion de PDF y del snapshot historico (Fase D).

Se ejecuta sin servidor, sobre una base de datos temporal, y genera PDF de
verdad con Chromium. Lo que se comprueba no es que "salga un archivo": es que
salga en A4, en una sola pagina, y sobre todo que la copia congelada este
completa, porque una copia a la que le falte un archivo se ve bien hoy y se ve
mal dentro de dos anos, que es justo cuando ya no hay manera de arreglarla.

    ./.venv/bin/python verificar_pdf.py
"""
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import documents
from app import pdf as pdf_engine
from app.config import settings
from app.db import Base
from app.models import STATUS_PENDING, Invoice

ok, fallos = [], []


def check(nombre, condicion, extra=""):
    (ok if condicion else fallos).append(nombre)
    print(("  OK   " if condicion else "  FALLA") + f" {nombre}" + (f"  [{extra}]" if extra else ""))


# Base y carpeta de datos propias: no se toca nada de la instalacion real.
temporal = Path(tempfile.mkdtemp())
settings.data_dir = temporal
ruta_db = temporal / "pruebas.db"
engine = create_engine(f"sqlite:///{ruta_db.as_posix()}")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

DATOS = dict(
    locale="es-MX",
    status=STATUS_PENDING,
    issue_date=date(2026, 8, 21),
    valid_until=date(2026, 8, 28),
    authorization="AUT-2026-99001",
    customer_name="Cliente de la prueba del PDF",
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
    representative_phone="55 1234 5678",
    representative_email="soporte@dulceauto.mx",
    representative_hours="Lunes a viernes, 8:00 a. m.–4:00 p. m.",
    verify_url_base="https://dulceauto.mx/verificar/",
)


def medidas(pdf: Path) -> tuple[float, float]:
    """Ancho y alto de la primera pagina, en puntos, leidos del MediaBox."""
    m = re.search(rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", pdf.read_bytes())
    if not m:
        return (0.0, 0.0)
    x0, y0, x1, y1 = (float(v) for v in m.groups())
    return (x1 - x0, y1 - y0)


with Session() as db:
    factura = Invoice(folio="RES-99001", **DATOS)
    db.add(factura)
    db.commit()

    print("\n1 · Se genera el PDF")
    r1 = pdf_engine.generar(db, factura)
    db.commit()
    check("el archivo existe", r1.pdf.exists())
    peso = r1.pdf.stat().st_size
    check("y pesa algo", peso > 50_000, f"{peso} bytes")
    # Chromium incrusta las imagenes sin recomprimir. Sin reducirlas antes, esta
    # misma factura pesaba 6,8 MB, que es demasiado para mandarsela por correo a
    # un cliente.
    check("pero no una barbaridad", peso < 2_500_000, f"{peso / 1024 / 1024:.2f} MB")
    check("una sola pagina", r1.paginas == 1, f"{r1.paginas}")

    ancho, alto = medidas(r1.pdf)
    # A4 son 595 x 842 puntos. Se admite un punto de margen por el redondeo.
    check("tamano A4", abs(ancho - 595.3) < 1.5 and abs(alto - 841.9) < 1.5,
          f"{ancho:.1f} x {alto:.1f} pt")
    check("queda anotada la fecha de generacion", factura.pdf_generated_at is not None)
    check("y no ha cambiado el estado de la operacion", factura.status == STATUS_PENDING)

    print("\n2 · La copia congelada esta completa")
    carpeta = r1.pdf.parent
    html = (carpeta / "documento.html").read_text(encoding="utf-8")
    check("se guarda el documento", (carpeta / "documento.html").exists())
    check("con rutas relativas, no las del panel",
          'src="assets/' in html and "/plantillas/assets/" not in html)

    # Esta es la comprobacion importante. La hoja de estilo pide las
    # tipografias con rutas relativas a si misma; si al copiarlas se resuelve
    # mal el "..", el PDF se imprime con otra letra y nadie se entera hasta que
    # lo compara con el aprobado.
    css = (carpeta / "assets/css/factura.css").read_text(encoding="utf-8")
    referencias = [
        m.group(1)
        for m in re.finditer(r"url\(['\"]?([^'\")]+)['\"]?\)", css)
        if not m.group(1).startswith(("http", "data:"))
    ]
    faltan = [
        r for r in referencias
        if not (carpeta / "assets/css" / r).resolve().exists()
    ]
    check("estan todos los archivos que pide el CSS", not faltan, ", ".join(sorted(set(faltan))[:3]))
    tipografias = list((carpeta / "assets/fonts").glob("*.woff2")) if (carpeta / "assets/fonts").exists() else []
    check("las tipografias se han copiado", len(tipografias) >= 4, f"{len(tipografias)} archivos")

    imagenes = [m.group(1) for m in re.finditer(r'src="assets/([^"]+)"', html)]
    check("y las imagenes del documento tambien",
          all((carpeta / "assets" / i).exists() for i in imagenes), f"{len(imagenes)} imagenes")

    print("\n2bis · Las fotografias se dejan a la resolucion del papel")
    from PIL import Image

    fotos = sorted((carpeta / "assets/img").glob("*.jpg"))
    check("las fotos estan copiadas", len(fotos) >= 4, f"{len(fotos)}")
    for foto in fotos:
        original = documents.TEMPLATES_DIR / "assets/img" / foto.name
        with Image.open(foto) as copia, Image.open(original) as orig:
            check(f"{foto.name}: reducida respecto al original",
                  copia.width <= orig.width, f"{orig.width}px -> {copia.width}px")
            # 300 ppp sobre el hueco que ocupa en la hoja. Nada baja de 400 px,
            # que es el suelo que fija el propio codigo.
            check(f"{foto.name}: no se queda corta", copia.width >= 400, f"{copia.width}px")

    print("\n3 · Lo congelado no cambia despues")
    factura.customer_name = "Nombre cambiado despues de imprimir"
    factura.banking_account_number = "002180001234567896"
    db.commit()
    html_despues = (carpeta / "documento.html").read_text(encoding="utf-8")
    check("el documento guardado conserva el cliente de entonces",
          "Cliente de la prueba del PDF" in html_despues)
    check("y la cuenta bancaria de entonces", "012180001234567899" in html_despues)
    check("el PDF sigue siendo el mismo archivo", r1.pdf.exists())

    print("\n4 · Cada generacion es una version nueva")
    r2 = pdf_engine.generar(db, factura)
    db.commit()
    check("la version sube", r2.snapshot.version == 2, f"v{r2.snapshot.version}")
    check("la anterior sigue estando", r1.pdf.exists() and r2.pdf.exists())
    check("y son archivos distintos", r1.pdf != r2.pdf)
    nuevo = (r2.pdf.parent / "documento.html").read_text(encoding="utf-8")
    check("la nueva version si recoge el cambio",
          "Nombre cambiado despues de imprimir" in nuevo)
    check("se listan las dos", len(pdf_engine.snapshots_de(db, factura.id)) == 2)

    print("\n5 · La escala se recalcula por factura")
    # Un texto mucho mas largo alarga el documento. Con una escala fija saldria
    # una segunda pagina; recalculando, tiene que seguir cabiendo en una.
    factura.delivery_text = (
        "Entrega concertada con el cliente en el domicilio registrado. " * 12
    )
    factura.vehicle_title = "2020 Mazda CX-5 Signature edición especial de aniversario"
    db.commit()
    r3 = pdf_engine.generar(db, factura)
    db.commit()
    check("con mucho mas texto sigue siendo una pagina", r3.paginas == 1, f"{r3.paginas}")
    check("y para conseguirlo ha reducido la escala", r3.escala < r1.escala,
          f"{r1.escala:.4f} -> {r3.escala:.4f}")
    check("el documento era mas alto", r3.altura_px > r1.altura_px,
          f"{r1.altura_px:.0f}px -> {r3.altura_px:.0f}px")
    ancho3, alto3 = medidas(r3.pdf)
    check("y la hoja sigue siendo A4", abs(ancho3 - 595.3) < 1.5 and abs(alto3 - 841.9) < 1.5,
          f"{ancho3:.1f} x {alto3:.1f} pt")

    print("\n6 · Contar paginas")
    check("cuenta una pagina en un PDF de una", pdf_engine.contar_paginas(r1.pdf) == 1)

    print("\n7 · Los tres mercados se imprimen")
    for n, locale in enumerate(("en", "es-AR"), start=2):
        otra = Invoice(folio=f"RES-9900{n}", **{**DATOS, "locale": locale})
        db.add(otra)
        db.commit()
        r = pdf_engine.generar(db, otra)
        db.commit()
        check(f"{locale}: una pagina", r.paginas == 1, f"{r.paginas}")
        texto = (r.pdf.parent / "documento.html").read_text(encoding="utf-8")
        esperado = documents.get_market(locale).template.split("/")[0]
        check(f"{locale}: usa su plantilla", esperado in str(r.snapshot.locale) or r.snapshot.locale == locale)
        check(f"{locale}: y su formato de importe",
              ("$412.500,00" if locale == "es-AR" else "$412,500.00") in texto)

# --- 8 · dos operadores pulsando a la vez -------------------------------------
#
# Este caso costo un error 500 de verdad. El cerrojo solo protegia la parte de
# Chromium, asi que tres peticiones simultaneas calculaban las tres la misma
# version, escribian en la misma carpeta y una borraba los archivos de otra a
# media copia. Ahora el reparto de version va tambien dentro del cerrojo y se
# confirma antes de soltarlo.
print("\n8 · Varios operadores generando el mismo PDF a la vez")
import threading  # noqa: E402

with Session() as db:
    simultanea = Invoice(folio="RES-99050", **DATOS)
    db.add(simultanea)
    db.commit()
    id_simultanea = simultanea.id

errores, versiones = [], []
barrera = threading.Barrier(3)


def generar_a_la_vez():
    try:
        with Session() as propia:
            factura_propia = propia.get(Invoice, id_simultanea)
            barrera.wait()          # que salgan los tres a la vez
            r = pdf_engine.generar(propia, factura_propia)
            versiones.append(r.snapshot.version)
    except Exception as exc:  # noqa: BLE001
        errores.append(f"{type(exc).__name__}: {exc}")


hilos = [threading.Thread(target=generar_a_la_vez) for _ in range(3)]
for h in hilos:
    h.start()
for h in hilos:
    h.join()

check("ninguno de los tres falla", not errores, " | ".join(errores)[:120])
check("cada uno recibe una version distinta", sorted(versiones) == [1, 2, 3], str(sorted(versiones)))
with Session() as db:
    guardados = pdf_engine.snapshots_de(db, id_simultanea)
    check("quedan las tres anotadas", len(guardados) == 3, f"{len(guardados)}")
    archivos = [pdf_engine.ruta_absoluta(s.pdf_path) for s in guardados]
    check("y los tres PDF estan en el disco", all(a and a.exists() for a in archivos))
    check("cada uno en su carpeta",
          len({a.parent for a in archivos if a}) == 3)
    # Si una carpeta hubiera pisado a otra, faltarian archivos en alguna.
    for a in archivos:
        if a:
            check(f"{a.parent.name}: la copia esta completa",
                  (a.parent / "assets/css/factura.css").exists()
                  and len(list((a.parent / "assets/fonts").glob("*.woff2"))) >= 4)


print(f"\n{'=' * 58}\n{len(ok)} comprobaciones correctas, {len(fallos)} fallos")
for f in fallos:
    print(f"  FALLA: {f}")
sys.exit(1 if fallos else 0)
