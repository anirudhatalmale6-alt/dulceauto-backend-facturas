"""
Comprobacion de la Fase C de punta a punta.

El motor de plantillas se comprueba aparte, sin navegador, en
verificar_plantillas.py. Aqui se comprueba lo que solo se puede ver con un
navegador de verdad: que la vista previa enseñe el documento real, que el CSS
aprobado se cargue, que el panel no se le meta dentro y que lo que se escribe en
el editor salga en la factura.

    python verificar_fase_c.py [http://127.0.0.1:8731] [/tmp/fase-c]
"""
import sqlite3
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8731").rstrip("/")
SHOTS = sys.argv[2] if len(sys.argv) > 2 else "/tmp/fase-c"
Path(SHOTS).mkdir(parents=True, exist_ok=True)

USER, PASSWORD = "admin", "DulceAuto2026"
# VIN propio de esta comprobacion: distinto de los de la Fase B, para que las
# dos no se pisen los recuentos por vehiculo.
VIN = "JH4KA7561PC008269"
CLIENTE = "Cliente de la Fase C"
DB = Path(__file__).parent / "data" / "dulceauto.db"


def limpiar_datos_de_pruebas_anteriores():
    """Borra la factura que dejo la ejecucion anterior, para que esta se pueda
    repetir tantas veces como haga falta y siga significando lo mismo."""
    if not DB.exists():
        return
    con = sqlite3.connect(DB)
    ids = [
        f[0]
        for f in con.execute(
            "SELECT id FROM invoice WHERE vehicle_vin = ? OR customer_name = ?", (VIN, CLIENTE)
        )
    ]
    if ids:
        huecos = ",".join("?" * len(ids))
        con.execute(f"DELETE FROM invoice_photo WHERE invoice_id IN ({huecos})", ids)
        con.execute(f"DELETE FROM invoice_snapshot WHERE invoice_id IN ({huecos})", ids)
        con.execute(f"UPDATE invoice SET duplicated_from_id = NULL WHERE duplicated_from_id IN ({huecos})", ids)
        con.execute(f"DELETE FROM invoice WHERE id IN ({huecos})", ids)
        con.commit()
    con.close()
    print(f"  (limpieza: {len(ids)} facturas de pruebas anteriores borradas)")


print("\n0 · Punto de partida limpio")
limpiar_datos_de_pruebas_anteriores()

ok, fallos = [], []


def check(nombre, condicion, extra=""):
    (ok if condicion else fallos).append(nombre)
    print(("  OK   " if condicion else "  FALLA") + f" {nombre}" + (f"  [{extra}]" if extra else ""))


def fill(page, campo, valor):
    page.fill(f'[name="{campo}"]', valor)


def elegir(page, campo, valor):
    page.select_option(f'[name="{campo}"]', valor)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 980})

    page.goto(f"{BASE}/acceso")
    fill(page, "username", USER)
    fill(page, "password", PASSWORD)
    page.click('button[type="submit"]')

    # -------------------------------------------------------------------------
    print("\n1 · Una factura nueva para trabajar sobre ella")
    page.goto(f"{BASE}/facturas/nueva")
    fill(page, "customer_name", CLIENTE)
    fill(page, "customer_email", "cliente.fasec@ejemplo.mx")
    fill(page, "customer_city", "Monterrey")
    fill(page, "vehicle_title", "2020 Mazda CX-5 Signature")
    fill(page, "vehicle_vin", VIN)
    fill(page, "vehicle_year", "2020")
    fill(page, "pricing_vehicle_price", "412500")
    fill(page, "pricing_reservation_amount", "5000")
    fill(page, "issue_date", "2026-08-21")
    fill(page, "delivery_date", "2026-09-04")
    page.click('button[type="submit"]:not([name])')
    url = page.url
    factura_id = url.split("/facturas/")[1].split("/")[0]
    check("la factura se ha creado", "/editar" in url, url)
    folio = page.input_value("[data-folio]")

    # -------------------------------------------------------------------------
    print("\n2 · La vista previa enseña el documento real")
    page.goto(f"{BASE}/facturas/{factura_id}/vista-previa")
    marco = page.frame_locator("iframe.preview-frame")
    documento = marco.locator("article.invoice")
    check("hay un iframe con el documento", page.locator("iframe.preview-frame").count() == 1)
    check("y dentro esta la factura, no una imitacion", documento.count() == 1)
    check(
        "el iframe apunta a la URL que se imprime",
        page.locator("iframe.preview-frame").get_attribute("src")
        == f"/facturas/{factura_id}/documento",
    )

    # El CSS aprobado tiene que estar cargado de verdad: si fallara, el
    # documento seguiria teniendo el texto y pareceria que funciona.
    fondo = marco.locator(".invoice").first.evaluate("e => getComputedStyle(e).backgroundColor")
    ancho = marco.locator(".invoice").first.evaluate("e => e.getBoundingClientRect().width")
    check("el CSS de la plantilla se ha cargado", fondo not in ("", "rgba(0, 0, 0, 0)"), fondo)
    check("y el documento mide su ancho de diseno", 830 <= ancho <= 900, f"{ancho:.0f}px")

    # El panel no puede colarse dentro del documento: si lo hiciera, la vista
    # previa dejaria de parecerse al PDF.
    hojas = marco.locator("link[rel=stylesheet]").evaluate_all(
        "els => els.map(e => e.getAttribute('href'))"
    )
    check("el documento solo carga el CSS de la factura", hojas == ["/plantillas/assets/css/factura.css"], str(hojas))
    check("no hay ni rastro del CSS del panel", not any("panel" in (h or "") for h in hojas))

    # -------------------------------------------------------------------------
    print("\n3 · Lo que se escribio en el editor sale en la factura")
    texto = documento.inner_text()
    check("el folio", folio in texto, folio)
    check("el cliente", CLIENTE in texto)
    check("el vehiculo", "2020 Mazda CX-5 Signature" in texto)
    check("el VIN", VIN in texto)
    check("el precio, con el formato de Mexico", "$412,500.00 MXN" in texto, )
    check("el importe de la pre-reserva", "$5,000.00" in texto)
    check("la fecha de emision en formato corto", "21 Ago 2026" in texto)
    check("la fecha de entrega en formato largo", "4 de septiembre de 2026" in texto)
    check("no queda nada del cliente de la maqueta", "Juan Pérez García" not in texto)
    check("ni del vehiculo de la maqueta", "Audi A3" not in texto)

    page.screenshot(path=f"{SHOTS}/c1-vista-previa.png")

    # -------------------------------------------------------------------------
    print("\n4 · Faltan datos: se dicen, y el hueco se queda en blanco")
    check("el aviso de datos que faltan aparece", page.locator(".warning-box").count() >= 1)
    aviso = page.locator(".warning-box").first.inner_text()
    check("y nombra alguno de los que faltan", "Autorización" in aviso or "Teléfono" in aviso, aviso[:90])

    # -------------------------------------------------------------------------
    print("\n5 · El zoom no re-maqueta el documento")
    for zoom, escala in ((0.5, "0.5"), (1.0, "1.0")):
        page.goto(f"{BASE}/facturas/{factura_id}/vista-previa?zoom={zoom}")
        marco = page.frame_locator("iframe.preview-frame")
        estilo = page.locator("iframe.preview-frame").get_attribute("style")
        interno = marco.locator(".invoice").first.evaluate("e => e.getBoundingClientRect().width")
        check(f"zoom {int(zoom * 100)}%: se reduce con scale", f"scale({escala})" in estilo, estilo[:60])
        check(f"zoom {int(zoom * 100)}%: el documento sigue midiendo lo mismo por dentro",
              830 <= interno <= 900, f"{interno:.0f}px")
    page.goto(f"{BASE}/facturas/{factura_id}/vista-previa?zoom=7")
    check("un zoom inventado no rompe la pantalla", page.locator("iframe.preview-frame").count() == 1)

    # -------------------------------------------------------------------------
    print("\n6 · Cambiar el mercado cambia de plantilla")
    page.goto(f"{BASE}/facturas/{factura_id}/editar")
    elegir(page, "locale", "en")
    page.click('button[type="submit"]:not([name])')
    page.goto(f"{BASE}/facturas/{factura_id}/vista-previa")
    ingles = page.frame_locator("iframe.preview-frame").locator("article.invoice").inner_text()
    check("el documento pasa a ingles", "Customer information" in ingles or "Payment pending" in ingles)
    check("y la fecha de entrega tambien", "4 September 2026" in ingles, )
    check("la pagina dice que plantilla se esta usando", "invoice.html" in page.locator(".toolbar").first.inner_text())

    page.goto(f"{BASE}/facturas/{factura_id}/editar")
    elegir(page, "locale", "es-AR")
    page.click('button[type="submit"]:not([name])')
    page.goto(f"{BASE}/facturas/{factura_id}/vista-previa")
    argentina = page.frame_locator("iframe.preview-frame").locator("article.invoice").inner_text()
    check("en Argentina el precio se escribe con punto", "$412.500,00" in argentina)
    check("y la cuenta se llama CBU", "CBU" in argentina)
    page.screenshot(path=f"{SHOTS}/c2-argentina.png")

    page.goto(f"{BASE}/facturas/{factura_id}/editar")
    elegir(page, "locale", "es-MX")
    page.click('button[type="submit"]:not([name])')

    # -------------------------------------------------------------------------
    print("\n7 · La modalidad de entrega decide el orden de los dos bloques")
    page.goto(f"{BASE}/facturas/{factura_id}/editar")
    check("la modalidad es un desplegable, no texto libre",
          page.locator('select[name="delivery_mode"]').count() == 1)
    elegir(page, "delivery_mode", "branch")
    page.click('button[type="submit"]:not([name])')
    page.goto(f"{BASE}/facturas/{factura_id}/documento")
    enlace = page.locator('[data-field="entrega_modalidad"]').inner_text()
    alterna = page.locator('[data-field="entrega_alternativa"]').inner_text()
    check("elegida la sede, arriba va la sede", "sede o concesionario" in enlace, enlace[:60])
    check("y la alternativa pasa a ser el domicilio", "domicilio" in alterna, alterna[:60])
    # La frase que empieza por "También puedes solicitar" esta escrita para ir
    # debajo. Arriba tiene que salir la redaccion de modalidad principal.
    arriba = page.locator('[data-field="entrega_texto"]').inner_text()
    abajo = page.locator('[data-field="entrega_alternativa_texto"]').inner_text()
    check("el texto de arriba no empieza por 'También'",
          not arriba.strip().startswith("También"), arriba[:60])
    check("y es la redaccion de sede como principal",
          arriba.startswith("La entrega se realizará en una sede"), arriba[:60])
    check("el domicilio, abajo, usa su redaccion de alternativa",
          abajo.startswith("También puedes solicitar la entrega a domicilio"), abajo[:60])

    page.goto(f"{BASE}/facturas/{factura_id}/editar")
    elegir(page, "delivery_mode", "home")
    fill(page, "delivery_text", "Entrega en 48 horas en su domicilio.")
    page.click('button[type="submit"]:not([name])')
    page.goto(f"{BASE}/facturas/{factura_id}/documento")
    check("un texto propio sustituye al de la plantilla",
          "Entrega en 48 horas en su domicilio." in page.locator(".vehicle-body").inner_text())

    # -------------------------------------------------------------------------
    print("\n8 · El estado del panel y lo que lee el cliente")
    page.goto(f"{BASE}/facturas/{factura_id}/documento")
    # La pastilla del diseno va en mayusculas y el motor lo respeta, asi que se
    # compara sin distinguirlas.
    check("un borrador se declara borrador",
          "borrador" in page.locator(".status-pill").inner_text().lower(),
          page.locator(".status-pill").inner_text())
    check("y el primer paso es el activo",
          "active" in (page.locator('[data-step="1"]').get_attribute("class") or ""))

    page.goto(f"{BASE}/facturas/{factura_id}/editar")
    fill(page, "customer_phone", "81 5555 4444")
    fill(page, "authorization", "AUT-2026-FASEC")
    fill(page, "valid_until", "2026-09-01")
    fill(page, "vehicle_location", "Monterrey")
    fill(page, "vehicle_type", "SUV")
    fill(page, "vehicle_mileage", "24,100 km")
    fill(page, "vehicle_fuel", "Gasolina")
    fill(page, "vehicle_transmission", "Automática")
    elegir(page, "status", "pending")
    page.click('button[type="submit"]:not([name])')
    page.goto(f"{BASE}/facturas/{factura_id}/documento")
    check("al pasar a pago pendiente, el paso 1 queda hecho",
          "done" in (page.locator('[data-step="1"]').get_attribute("class") or ""))
    check("y el paso 2 activo",
          "active" in (page.locator('[data-step="2"]').get_attribute("class") or ""))
    check("la vigencia sale en dd/mm/aaaa", "01/09/2026" in page.locator(".transaction-card").inner_text())

    page.goto(f"{BASE}/facturas/{factura_id}/vista-previa")
    check("ya no falta ningun dato", page.locator(".warning-box").count() == 0)
    page.screenshot(path=f"{SHOTS}/c3-completa.png")

    # -------------------------------------------------------------------------
    print("\n9 · El recorrido de estados llega hasta el final")
    for estado, esperado, pastilla in [
        ("payment_validated", ["done", "done", "active", "-"], "PAGO VALIDADO"),
        ("delivery_scheduled", ["done", "done", "done", "active"], "ENTREGA COORDINADA"),
        ("delivered", ["done", "done", "done", "done"], "ENTREGA COMPLETADA"),
    ]:
        page.goto(f"{BASE}/facturas/{factura_id}/editar")
        elegir(page, "status", estado)
        page.click('button[type="submit"]:not([name])')
        page.goto(f"{BASE}/facturas/{factura_id}/documento")
        barra = [
            (page.locator(f'[data-step="{n}"]').get_attribute("class") or "")
            .replace("step", "").strip() or "-"
            for n in (1, 2, 3, 4)
        ]
        check(f"{estado}: la barra avanza donde toca", barra == esperado, barra)
        check(f"{estado}: la pastilla lo dice",
              pastilla in page.locator(".status-pill").inner_text().upper(),
              page.locator(".status-pill").inner_text())
    # El nombre del tercer paso es el del documento aprobado y no se toca.
    check("el paso 3 conserva su nombre aprobado",
          "Documentación y trámites" in page.locator('[data-step="3"]').inner_text(),
          page.locator('[data-step="3"]').inner_text())

    print("\n10 · La pantalla de Plantillas enseña lo que hay de verdad")
    page.goto(f"{BASE}/plantillas")
    check("las tres plantillas siguen ahi", page.locator(".template").count() == 3)
    check("y dice cuantos huecos tiene cada una", "campos marcados" in page.locator(".template").first.inner_text())
    check("estan escritas las claves que no llegan al documento",
          "vehicle.carfax" in page.content())
    ingles_tarjeta = page.locator(".template", has_text="English").inner_text()
    check("la ficha inglesa dice CLABE, como su plantilla",
          "CLABE (18 digits)" in ingles_tarjeta and "Interbank account number" not in ingles_tarjeta,
          [l for l in ingles_tarjeta.splitlines() if "CLABE" in l or "Interbank" in l])
    page.screenshot(path=f"{SHOTS}/c4-plantillas.png")
    page.locator(".template").first.locator("text=Ver vista previa").click()
    page.wait_for_load_state()
    check("desde ahi se llega a una vista previa", "/vista-previa" in page.url, page.url)

    # -------------------------------------------------------------------------
    print("\n11 · Los archivos de la plantilla se sirven de verdad")
    for ruta, marca in [
        ("/plantillas/assets/css/factura.css", ".invoice"),
        ("/plantillas/assets/img/vehicle-front.jpg", None),
    ]:
        r = page.request.get(BASE + ruta)
        check(f"{ruta} responde", r.status == 200, str(r.status))
        if marca:
            check("y es el CSS aprobado", marca in r.text())

    # -------------------------------------------------------------------------
    print("\n12 · El documento no cambia con el tema del panel")
    for tema in ("light", "soft", "night"):
        page.goto(f"{BASE}/facturas/{factura_id}/vista-previa")
        page.evaluate(f"setTheme('{tema}')")
        page.wait_for_timeout(200)
        marco = page.frame_locator("iframe.preview-frame")
        fondo = marco.locator(".invoice").first.evaluate("e => getComputedStyle(e).backgroundColor")
        color = marco.locator(".invoice h2").first.evaluate("e => getComputedStyle(e).color")
        check(f"tema {tema}: la factura mantiene su fondo", fondo == "rgb(255, 255, 255)", fondo)
        check(f"tema {tema}: y su color de texto", color not in ("rgb(255, 255, 255)",), color)
        page.screenshot(path=f"{SHOTS}/c5-tema-{tema}.png")

    # -------------------------------------------------------------------------
    print("\n13 · Nada de esto se puede ver sin sesion")
    page.context.clear_cookies()
    for ruta in [f"/facturas/{factura_id}/documento", f"/facturas/{factura_id}/vista-previa"]:
        page.goto(BASE + ruta)
        check(f"sin sesion, {ruta} lleva al acceso", page.url.endswith("/acceso"), page.url)

    browser.close()

print(f"\n{'=' * 58}\n{len(ok)} comprobaciones correctas, {len(fallos)} fallos")
for f in fallos:
    print(f"  FALLA: {f}")
if not fallos:
    print("Fase C verificada.")
sys.exit(1 if fallos else 0)
