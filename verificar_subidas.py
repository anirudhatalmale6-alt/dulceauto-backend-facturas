"""
Comprobacion de las subidas y de la edicion de Configuracion (Fase D).

Sin servidor. Se centra en lo que puede hacer dano de verdad:

  - un archivo que dice ser una imagen y no lo es;
  - un SVG con codigo dentro, que acabaria incrustado en la factura y en su PDF;
  - una ruta con ".." que apunte fuera de la carpeta de datos;
  - una CLABE o un CBU mal tecleados en Configuracion, que se copiarian a cada
    factura nueva y mandarian al cliente a transferir a una cuenta inexistente;
  - y que cambiar el logotipo o una fotografia **no toque** los PDF ya emitidos.

    ./.venv/bin/python verificar_subidas.py
"""
import io
import sys
import tempfile
from datetime import date
from pathlib import Path

from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import pdf as pdf_engine
from app import uploads
from app.config import settings
from app.db import Base
from app.invoices import validar_ajuste
from app.models import STATUS_PENDING, Invoice, InvoicePhoto, Setting

ok, fallos = [], []


def check(nombre, condicion, extra=""):
    (ok if condicion else fallos).append(nombre)
    print(("  OK   " if condicion else "  FALLA") + f" {nombre}" + (f"  [{extra}]" if extra else ""))


temporal = Path(tempfile.mkdtemp())
settings.data_dir = temporal
engine = create_engine(f"sqlite:///{(temporal / 'subidas.db').as_posix()}")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)


def imagen(formato="JPEG", tamano=(800, 600), color="#1f6feb") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", tamano, color).save(buffer, formato)
    return buffer.getvalue()


print("\n1 · Que se acepta y que no")
for formato in ("JPEG", "PNG", "WEBP"):
    guardado = uploads.guardar_imagen(imagen(formato), f"foto.{formato.lower()}", "pruebas")
    check(f"{formato} se acepta", guardado.ruta.exists(), guardado.formato)

svg_limpio = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'
guardado = uploads.guardar_imagen(svg_limpio, "logo.svg", "pruebas")
check("un SVG limpio se acepta", guardado.formato == "SVG")


def rechaza(datos, nombre, motivo_esperado=""):
    try:
        uploads.guardar_imagen(datos, nombre, "pruebas")
        return None
    except uploads.SubidaInvalida as exc:
        return str(exc)


mensaje = rechaza(b"", "vacio.jpg")
check("un archivo vacio se rechaza", mensaje is not None, mensaje)

mensaje = rechaza(b"esto no es una imagen, es texto plano", "trampa.jpg")
check("un archivo que solo se llama .jpg se rechaza", mensaje is not None, mensaje)

# El navegador manda el nombre y el tipo, y los pone quien sube el archivo.
mensaje = rechaza(imagen("BMP"), "foto.jpg")
check("un formato no admitido se rechaza aunque se llame .jpg", mensaje is not None, mensaje)

mensaje = rechaza(b"a" * (uploads.MAX_BYTES + 1), "enorme.jpg")
check("un archivo enorme se rechaza", mensaje is not None, mensaje)

svg_malo = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
mensaje = rechaza(svg_malo, "malo.svg")
check("un SVG con script se rechaza", mensaje is not None, mensaje)

svg_evento = b'<svg xmlns="http://www.w3.org/2000/svg"><rect onload="alert(1)"/></svg>'
mensaje = rechaza(svg_evento, "evento.svg")
check("un SVG con manejador de eventos se rechaza", mensaje is not None, mensaje)


print("\n2 · El nombre del archivo lo pone el servidor")
guardado = uploads.guardar_imagen(imagen(), "../../../etc/passwd.jpg", "pruebas")
check("no se conserva el nombre que traia", "passwd" not in guardado.ruta.name, guardado.ruta.name)
check("y queda dentro de la carpeta de datos",
      guardado.ruta.resolve().is_relative_to(temporal.resolve()), str(guardado.ruta))

check("una ruta con .. no se resuelve", uploads.ruta_absoluta("../../etc/passwd") is None)
check("una ruta inexistente devuelve None", uploads.ruta_absoluta("uploads/no-existe.jpg") is None)
check("una ruta buena si se resuelve", uploads.ruta_absoluta(guardado.relativa) is not None)


print("\n3 · Configuracion no acepta datos que romperian una factura")
check("una CLABE corta se rechaza",
      validar_ajuste("banking.account_number", "0121800012345", "es-MX") is not None)
check("una CLABE con el digito de control mal se rechaza",
      validar_ajuste("banking.account_number", "012180001234567890", "es-MX") is not None)
check("una CLABE correcta se acepta",
      validar_ajuste("banking.account_number", "012180001234567899", "es-MX") is None)
check("un CBU correcto se acepta",
      validar_ajuste("banking.account_number", "2850590994009041813526", "es-AR") is None)
check("el CBU se valida como CBU, no como CLABE",
      validar_ajuste("banking.account_number", "012180001234567899", "es-AR") is not None)

check("la URL del QR tiene que ser una URL",
      validar_ajuste("qr.base_url", "dulceauto.mx/verificar/", None) is not None)
check("y no puede llevar espacios",
      validar_ajuste("qr.base_url", "https://dulceauto.mx/veri ficar/", None) is not None)
check("una URL correcta se acepta",
      validar_ajuste("qr.base_url", "https://dulceauto.mx/verificar/", None) is None)
check("el contador de folios tiene que ser numero",
      validar_ajuste("folio.next", "87A41", None) is not None)
check("y mayor que cero", validar_ajuste("folio.next", "0", None) is not None)
check("un contador correcto se acepta", validar_ajuste("folio.next", "87241", None) is None)
check("el prefijo no puede quedar vacio", validar_ajuste("folio.prefix", "", None) is not None)
check("un email sin arroba se rechaza",
      validar_ajuste("representative.email", "soporte.dulceauto.mx", "es-MX") is not None)


print("\n4 · Las fotos y el logotipo entran en el PDF y se congelan")
DATOS = dict(
    locale="es-MX",
    status=STATUS_PENDING,
    issue_date=date(2026, 8, 21),
    customer_name="Cliente con fotos",
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

with Session() as db:
    factura = Invoice(folio="RES-95001", **DATOS)
    db.add(factura)
    db.commit()

    # Cuatro fotos de colores distintos: se puede comprobar cual ha ido a cada
    # hueco mirando el color del pixel, sin depender de nombres de archivo.
    COLORES = {1: (31, 111, 235), 2: (180, 83, 9), 3: (21, 128, 61), 4: (126, 34, 206)}
    for posicion, color in COLORES.items():
        subida = uploads.guardar_imagen(
            imagen(color="#%02x%02x%02x" % color), f"foto{posicion}.jpg", "facturas/1"
        )
        db.add(
            InvoicePhoto(invoice_id=factura.id, position=posicion, file_path=subida.relativa)
        )

    logo = uploads.guardar_imagen(imagen("PNG", (600, 140), "#0b1f3a"), "logo.png", "logo")
    db.add(Setting(key="brand.logo_path", market=None, value=logo.relativa, is_sensitive=True))
    db.commit()
    db.refresh(factura)

    primera = pdf_engine.generar(db, factura)
    carpeta = primera.pdf.parent

    for posicion, color in COLORES.items():
        nombre = pdf_engine.documents.ARCHIVO_FOTO[f"foto_{posicion}"]
        ruta = carpeta / "assets/img" / nombre
        with Image.open(ruta) as img:
            centro = img.convert("RGB").getpixel((img.width // 2, img.height // 2))
        cerca = all(abs(a - b) <= 8 for a, b in zip(centro, color))
        check(f"la foto {posicion} es la que se subio", cerca, f"{centro} vs {color}")

    html = (carpeta / "documento.html").read_text(encoding="utf-8")
    check("el logotipo entra en el documento", 'class="brand-logo"' in html)
    # El texto alternativo del diseno describe el coche de la maqueta. Se
    # sustituye solo cuando la fotografia ya no es la suya.
    check("el texto alternativo pasa a ser el vehiculo de la factura",
          html.count('alt="2020 Mazda CX-5 Signature"') == 4,
          f"{html.count('alt=\"2020 Mazda CX-5 Signature\"')} veces")
    check("y ya no describe al Audi de la maqueta", "Audi A3" not in html)
    check("y su archivo esta dentro del snapshot", (carpeta / "assets/img/logo.png").exists())
    # Con fotografias, la pre-factura son DOS hojas: la segunda es el album.
    # Antes esta comprobacion exigia una sola, que era lo correcto cuando la
    # pagina 2 no existia. Se compara contra documents.paginas(), que es la
    # misma funcion que usa el generador para decidir, y no contra un numero
    # escrito aqui: asi las dos no pueden separarse.
    esperadas = pdf_engine.documents.paginas(factura)
    check("las hojas que salen son las que toca con álbum",
          primera.paginas == esperadas == 2, f"{primera.paginas} vs {esperadas}")
    # Y las fotografias del album estan dentro del snapshot con su nombre. Sin
    # esto el PDF sale con la pagina 2 llena de imagenes rotas, y no se nota
    # hasta abrirlo: el HTML es correcto, lo que falta es el archivo.
    del_album = sorted((carpeta / "assets/img/album").glob("*.jpg")) if (carpeta / "assets/img/album").is_dir() else []
    check("y el álbum del snapshot trae una imagen por fotografía",
          len(del_album) == len(factura.photos), f"{len(del_album)} de {len(factura.photos)}")

    print("\n5 · Cambiar las fotos o el logotipo no toca lo ya emitido")
    nueva = uploads.guardar_imagen(imagen(color="#ff0000"), "otra.jpg", "facturas/1")
    foto1 = db.execute(
        select(InvoicePhoto).where(
            InvoicePhoto.invoice_id == factura.id, InvoicePhoto.position == 1
        )
    ).scalar_one()
    foto1.file_path = nueva.relativa
    otro_logo = uploads.guardar_imagen(imagen("PNG", (600, 140), "#ff0000"), "logo2.png", "logo")
    db.execute(select(Setting)).scalars()
    fila = db.execute(
        select(Setting).where(Setting.key == "brand.logo_path")
    ).scalar_one()
    fila.value = otro_logo.relativa
    db.commit()
    db.refresh(factura)

    with Image.open(carpeta / "assets/img/vehicle-front.jpg") as img:
        centro = img.convert("RGB").getpixel((img.width // 2, img.height // 2))
    check("la copia congelada sigue con la foto de entonces",
          all(abs(a - b) <= 8 for a, b in zip(centro, COLORES[1])), str(centro))

    with Image.open(carpeta / "assets/img/logo.png") as img:
        centro_logo = img.convert("RGB").getpixel((5, 5))
    check("y con el logotipo de entonces", centro_logo[0] < 60, str(centro_logo))

    segunda = pdf_engine.generar(db, factura)
    with Image.open(segunda.pdf.parent / "assets/img/vehicle-front.jpg") as img:
        centro2 = img.convert("RGB").getpixel((img.width // 2, img.height // 2))
    check("la version nueva si recoge la foto nueva", centro2[0] > 200, str(centro2))
    check("y las dos versiones conviven", primera.pdf.exists() and segunda.pdf.exists())

    print("\n6 · Sin logotipo propio se conserva la marca aprobada")
    fila.value = ""
    db.commit()
    db.refresh(factura)
    tercera = pdf_engine.generar(db, factura)
    html3 = (tercera.pdf.parent / "documento.html").read_text(encoding="utf-8")
    check("vuelve la marca del diseno", 'class="brand-name"' in html3 and "brand-logo" not in html3)
    check("y no queda ningun logo suelto en el snapshot",
          not list((tercera.pdf.parent / "assets/img").glob("logo.*")))


    print("\n7 · Al duplicar, las fotografias acompanan al vehiculo")
    # Duplicar es atender a otro interesado por el mismo coche: las fotografias
    # son datos del vehiculo, como el VIN, y tienen que ir en la copia.
    from app.invoices import duplicate

    fila.value = logo.relativa          # se deja el logotipo como estaba
    db.commit()
    db.refresh(factura)
    originales = {f.position: f.file_path for f in factura.photos}

    copia = duplicate(db, factura)
    db.commit()
    db.refresh(copia)
    heredadas = {f.position: f.file_path for f in copia.photos}

    check("la copia hereda las cuatro fotografias", sorted(heredadas) == [1, 2, 3, 4],
          str(sorted(heredadas)))
    check("cada una en la misma posicion y con la misma imagen",
          all(
              Image.open(uploads.ruta_absoluta(heredadas[p])).convert("RGB").getpixel((5, 5))
              == Image.open(uploads.ruta_absoluta(originales[p])).convert("RGB").getpixel((5, 5))
              for p in heredadas
          ))
    check("pero son archivos distintos en el disco, no la misma ruta",
          all(heredadas[p] != originales[p] for p in heredadas),
          "compartir la ruta borraria la foto del original al sustituirla en la copia")
    check("y la copia sigue naciendo en borrador", copia.status == "draft", copia.status)
    check("con folio propio", copia.folio != factura.folio, f"{factura.folio} -> {copia.folio}")

    # La prueba que de verdad importa: sustituir una fotografia en la copia no
    # puede dejar sin imagen a la factura original.
    nueva = uploads.guardar_imagen(imagen(color="#ff00ff"), "otra.jpg", "facturas/2")
    de_la_copia = next(f for f in copia.photos if f.position == 1)
    uploads.borrar(de_la_copia.file_path)          # es lo que hace la ruta de subida
    de_la_copia.file_path = nueva.relativa
    db.commit()

    ruta_original_1 = uploads.ruta_absoluta(originales[1])
    check("sustituir una foto en la copia no toca el archivo del original",
          ruta_original_1 is not None)
    if ruta_original_1 is not None:
        with Image.open(ruta_original_1) as img:
            esperado_1 = img.convert("RGB").getpixel((5, 5))
        db.refresh(factura)
        con_original = pdf_engine.generar(db, factura)
        with Image.open(con_original.pdf.parent / "assets/img/vehicle-front.jpg") as img:
            impreso_1 = img.convert("RGB").getpixel((img.width // 2, img.height // 2))
        check("y el original sigue imprimiendo su fotografia de siempre",
              all(abs(a - b) <= 8 for a, b in zip(impreso_1, esperado_1)),
              f"{impreso_1} vs {esperado_1}")

    de_la_copia_pdf = pdf_engine.generar(db, copia)
    check("la copia imprime con las fotografias heredadas",
          de_la_copia_pdf.paginas == pdf_engine.documents.paginas(copia) == 2,
          f"{de_la_copia_pdf.paginas}")
    for posicion in (2, 3, 4):
        nombre = pdf_engine.documents.ARCHIVO_FOTO[f"foto_{posicion}"]
        check(f"copia: hueco {posicion} relleno",
              (de_la_copia_pdf.pdf.parent / "assets/img" / nombre).exists())

    # Una factura sin fotografias se duplica igual, sin inventarse ninguna.
    sin_fotos = Invoice(folio="RES-95900", **{**DATOS, "vehicle_vin": "1HGCM82633A004352"})
    db.add(sin_fotos)
    db.commit()
    copia_vacia = duplicate(db, sin_fotos)
    db.commit()
    db.refresh(copia_vacia)
    check("duplicar una factura sin fotografias no falla ni inventa ninguna",
          len(copia_vacia.photos) == 0, str(len(copia_vacia.photos)))

    # Y si el archivo ya no esta en el disco, no se crea una fotografia que
    # apunte a la nada: se omite esa posicion.
    perdida = uploads.guardar_imagen(imagen(color="#123456"), "perdida.jpg", "facturas/9")
    db.add(InvoicePhoto(invoice_id=sin_fotos.id, position=1, file_path=perdida.relativa))
    db.commit()
    uploads.borrar(perdida.relativa)
    db.refresh(sin_fotos)
    copia_rota = duplicate(db, sin_fotos)
    db.commit()
    db.refresh(copia_rota)
    check("una fotografia que ya no esta en el disco no se hereda rota",
          len(copia_rota.photos) == 0, str([f.file_path for f in copia_rota.photos]))


print(f"\n{'=' * 58}\n{len(ok)} comprobaciones correctas, {len(fallos)} fallos")
for f in fallos:
    print(f"  FALLA: {f}")
sys.exit(1 if fallos else 0)
