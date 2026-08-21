"""
Comprobacion del QR y del codigo de barras.

Aqui no se mira que el dibujo "parezca" un codigo: se **leen con un lector de
verdad**, el mismo tipo de software que usaria un telefono, y se exige que
devuelvan exactamente el folio y el enlace de esa factura. Un codigo mal
generado se ve perfecto y no lo lee nadie; el fallo aparecería en el mostrador,
no aqui.

Se leen dos veces: del archivo SVG del snapshot y de la pagina del PDF ya
impreso, que es lo que el cliente tiene delante cuando escanea.

Necesita dos cosas que solo hacen falta para comprobar, no para funcionar:

    ./.venv/bin/pip install pyzbar         (necesita libzbar0 en el sistema)
    apt-get install poppler-utils          (para pdftoppm)

    ./.venv/bin/python verificar_codigos.py
"""
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ok, fallos = [], []


def check(nombre, condicion, extra=""):
    (ok if condicion else fallos).append(nombre)
    print(("  OK   " if condicion else "  FALLA") + f" {nombre}" + (f"  [{extra}]" if extra else ""))


try:
    from PIL import Image
    from pyzbar.pyzbar import decode
except ImportError as exc:  # pragma: no cover
    print(f"Falta {exc.name}. Esta comprobacion necesita pyzbar y Pillow.")
    sys.exit(2)

if not shutil.which("pdftoppm"):  # pragma: no cover
    print("Falta pdftoppm (paquete poppler-utils). Se necesita para leer el PDF.")
    sys.exit(2)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import codes  # noqa: E402
from app import pdf as pdf_engine  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import STATUS_PENDING, Invoice  # noqa: E402

temporal = Path(tempfile.mkdtemp())
settings.data_dir = temporal
engine = create_engine(f"sqlite:///{(temporal / 'codigos.db').as_posix()}")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

DATOS = dict(
    locale="es-MX",
    status=STATUS_PENDING,
    issue_date=date(2026, 8, 21),
    customer_name="Cliente de la prueba de codigos",
    vehicle_title="2020 Mazda CX-5 Signature",
    vehicle_vin="JH4KA7561PC008269",
    pricing_vehicle_price=412500,
    pricing_reservation_amount=5000,
    pricing_currency="MXN",
    banking_bank="BBVA México",
    banking_account_number="012180001234567899",
    delivery_date=date(2026, 9, 4),
    delivery_mode="home",
    representative_name="Yoselina de la Cruz",
    verify_url_base="https://dulceauto.mx/verificar/",
)


def leer_svg(ruta: Path, ancho: int = 900) -> list[str]:
    """Rasteriza un SVG con Chromium y lo pasa por el lector."""
    from playwright.sync_api import sync_playwright

    png = temporal / "lectura.png"
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page(viewport={"width": ancho, "height": ancho})
        pagina.goto(ruta.resolve().as_uri())
        pagina.wait_for_timeout(150)
        pagina.screenshot(path=str(png))
        navegador.close()
    return [d.data.decode() for d in decode(Image.open(png))]


def leer_pdf(pdf: Path, ppp: int) -> list[tuple[str, str]]:
    destino = temporal / f"pagina{ppp}"
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(ppp), "-f", "1", "-l", "1", str(pdf), str(destino)],
        check=True,
    )
    imagen = next(temporal.glob(f"pagina{ppp}-*.png"))
    return [(d.type, d.data.decode()) for d in decode(Image.open(imagen))]


print("\n1 · Los codigos sueltos dicen lo que tienen que decir")
svg = temporal / "suelto.svg"
svg.write_text(codes.barcode_svg("RES-90210"), encoding="utf-8")
leido = leer_svg(svg)
check("el codigo de barras se lee", leido == ["RES-90210"], str(leido))

svg.write_text(codes.qr_svg("https://dulceauto.mx/verificar/RES-90210"), encoding="utf-8")
leido = leer_svg(svg)
check("el QR se lee", leido == ["https://dulceauto.mx/verificar/RES-90210"], str(leido))

# Un folio distinto tiene que dar un codigo distinto: si saliera el mismo,
# estariamos imprimiendo el codigo de la maqueta en todas las facturas.
svg.write_text(codes.barcode_svg("RES-11111"), encoding="utf-8")
check("otro folio da otro codigo", leer_svg(svg) == ["RES-11111"], str(leer_svg(svg)))

check("sin URL no se inventa un enlace", "<g" not in codes.qr_svg(""))
check("sin folio tampoco", "<g" not in codes.barcode_svg(""))

largo = "RES-2026-000000000087241"
svg.write_text(codes.barcode_svg(largo), encoding="utf-8")
check("un folio largo tambien se lee", leer_svg(svg, 1400) == [largo], str(leer_svg(svg, 1400)))


print("\n2 · Los codigos del snapshot son los de esa factura")
with Session() as db:
    factura = Invoice(folio="RES-90210", **DATOS)
    db.add(factura)
    db.commit()
    resultado = pdf_engine.generar(db, factura)

    carpeta = resultado.pdf.parent / "assets/img"
    qr = carpeta / "reservation-qr.svg"
    barras = carpeta / "reservation-barcode.svg"
    check("el QR se ha escrito en el snapshot", qr.exists())
    check("y el codigo de barras tambien", barras.exists())

    # No pueden ser los de la plantilla: esos llevan el folio de la maqueta.
    from app import documents

    muestra_qr = (documents.TEMPLATES_DIR / "assets/img/reservation-qr.svg").read_text(encoding="utf-8")
    check("no son los archivos de muestra de la plantilla",
          qr.read_text(encoding="utf-8") != muestra_qr)

    check("el QR del snapshot lleva el enlace de esta factura",
          leer_svg(qr) == ["https://dulceauto.mx/verificar/RES-90210"], str(leer_svg(qr)))
    check("y el de barras, su folio", leer_svg(barras) == ["RES-90210"], str(leer_svg(barras)))

    print("\n3 · Y se leen del PDF impreso, que es lo que escanea el cliente")
    for ppp in (200, 300):
        leidos = dict(leer_pdf(resultado.pdf, ppp))
        check(f"{ppp} ppp: el QR se lee de la hoja",
              leidos.get("QRCODE") == "https://dulceauto.mx/verificar/RES-90210", str(leidos))
        check(f"{ppp} ppp: el codigo de barras tambien",
              leidos.get("CODE128") == "RES-90210", str(leidos))

    print("\n4 · Otra factura, otros codigos")
    otra = Invoice(folio="RES-90211", **{**DATOS, "verify_url_base": "https://dulceauto.mx/verificar/"})
    db.add(otra)
    db.commit()
    segundo = pdf_engine.generar(db, otra)
    leidos = dict(leer_pdf(segundo.pdf, 300))
    check("el QR de la segunda apunta a la segunda",
          leidos.get("QRCODE") == "https://dulceauto.mx/verificar/RES-90211", str(leidos))
    check("y su codigo de barras lleva su folio", leidos.get("CODE128") == "RES-90211", str(leidos))
    check("la primera no se ha tocado",
          dict(leer_pdf(resultado.pdf, 300)).get("CODE128") == "RES-90210")


print(f"\n{'=' * 58}\n{len(ok)} comprobaciones correctas, {len(fallos)} fallos")
for f in fallos:
    print(f"  FALLA: {f}")
sys.exit(1 if fallos else 0)
