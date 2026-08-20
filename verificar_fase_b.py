"""
Comprobacion de la Fase B de punta a punta.

Crear, editar, guardar borrador, buscar, duplicar y agrupar por VIN. Igual que
en la Fase A, no comprueba que las paginas carguen: comprueba que hagan lo que
tienen que hacer, y sobre todo las dos reglas que serian graves si fallaran:

  - duplicar no puede confirmar una reserva ni heredar el estado del origen;
  - una factura no puede salir de borrador con datos obligatorios en blanco.

    python verificar_fase_b.py [http://127.0.0.1:8731] [/tmp/fase-b]
"""
import sys

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8731").rstrip("/")
SHOTS = sys.argv[2] if len(sys.argv) > 2 else "/tmp/fase-b"

USER, PASSWORD = "admin", "DulceAuto2026"

# VIN validos de 17 caracteres, sin I, O ni Q.
VIN_A = "1HGCM82633A004352"
VIN_B = "WBA3A5C55DF598765"

ok, fallos = [], []


def check(nombre, condicion, extra=""):
    (ok if condicion else fallos).append(nombre)
    print(("  OK   " if condicion else "  FALLA") + f" {nombre}" + (f"  [{extra}]" if extra else ""))


def fill(page, campo, valor):
    page.fill(f'[name="{campo}"]', valor)


def valor(page, campo):
    return page.input_value(f'[name="{campo}"]')


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})

    page.goto(f"{BASE}/acceso")
    fill(page, "username", USER)
    fill(page, "password", PASSWORD)
    page.click('button[type="submit"]')

    # -------------------------------------------------------------------------
    print("\n1 · Crear un borrador a medias")
    page.goto(f"{BASE}/facturas/nueva")
    fill(page, "customer_name", "Cliente de prueba Fase B")
    fill(page, "vehicle_title", "2021 Honda Civic Sport")
    fill(page, "vehicle_vin", VIN_A)
    page.click('button[name="save_as"][value="draft"]')

    check("un borrador se guarda aunque falten datos", "/editar" in page.url, page.url)
    folio_a = valor(page, "folio")
    check("el folio se asigna solo", folio_a.startswith("RES-"), folio_a)
    check("el estado queda en borrador", valor(page, "status") == "draft")
    check("el cliente se ha guardado", valor(page, "customer_name") == "Cliente de prueba Fase B")
    check(
        "los datos bancarios se heredan de Configuracion",
        page.locator('input[value="012180001234567899"]').count() == 1,
    )
    page.screenshot(path=f"{SHOTS}/01-borrador.png")
    url_a = page.url

    # -------------------------------------------------------------------------
    print("\n2 · Salir de borrador exige los datos obligatorios")
    page.select_option('[name="status"]', "pending")
    page.click('button.btn.blue[type="submit"]')
    check("no deja salir de borrador con huecos", page.locator(".alert.error").count() == 1)
    aviso = page.locator(".alert.error").inner_text() if page.locator(".alert.error").count() else ""
    check("dice exactamente que falta", "precio" in aviso.lower() and "emisión" in aviso.lower(), aviso[:90])
    check(
        "no borra lo que el operador acababa de teclear",
        valor(page, "customer_name") == "Cliente de prueba Fase B",
    )

    page.goto(url_a)
    check("y la factura sigue siendo borrador en la base", valor(page, "status") == "draft")

    # -------------------------------------------------------------------------
    print("\n3 · Completarla y sacarla de borrador")
    fill(page, "issue_date", "2026-08-20")
    fill(page, "valid_until", "2026-08-27")
    fill(page, "pricing_vehicle_price", "412.500,00")   # escritura argentina
    fill(page, "pricing_reservation_amount", "5,000.00")  # escritura mexicana
    page.select_option('[name="status"]', "pending")
    page.click('button.btn.blue[type="submit"]')
    check("ahora si guarda", page.locator(".alert.error").count() == 0)
    check("el estado cambia a pago pendiente", valor(page, "status") == "pending")
    check("lee 412.500,00 como cuatrocientos doce mil", valor(page, "pricing_vehicle_price").startswith("412500"), valor(page, "pricing_vehicle_price"))
    check("lee 5,000.00 como cinco mil", valor(page, "pricing_reservation_amount").startswith("5000"), valor(page, "pricing_reservation_amount"))

    # -------------------------------------------------------------------------
    print("\n4 · Validaciones")
    fill(page, "vehicle_vin", "1HGCM82633A0043")  # 15 caracteres
    page.click('button.btn.blue[type="submit"]')
    check("un VIN corto se rechaza", page.locator(".alert.error").count() == 1)
    check("y explica el motivo", "17" in page.locator(".alert.error").inner_text())

    page.goto(url_a)
    fill(page, "vehicle_vin", "1HGCM82633A0O435")
    page.click('button.btn.blue[type="submit"]')
    check("un VIN con la letra O se rechaza", page.locator(".alert.error").count() == 1)

    page.goto(url_a)
    fill(page, "valid_until", "2026-08-01")  # anterior a la emision
    page.click('button.btn.blue[type="submit"]')
    check("una vigencia anterior a la emision se rechaza", page.locator(".alert.error").count() == 1)

    page.goto(url_a)
    check("ninguno de esos intentos ha llegado a guardarse", valor(page, "vehicle_vin") == VIN_A)

    # -------------------------------------------------------------------------
    print("\n5 · Folio duplicado")
    page.goto(f"{BASE}/facturas/nueva")
    fill(page, "folio", folio_a)
    fill(page, "customer_name", "Choque de folio")
    page.click('button[name="save_as"][value="draft"]')
    check("no deja repetir un folio", page.locator(".alert.error").count() == 1)
    check("y lo dice con el folio concreto", folio_a in page.locator(".alert.error").inner_text())

    # -------------------------------------------------------------------------
    print("\n6 · Duplicar")
    page.goto(url_a)
    page.click('a.btn:has-text("Duplicar")')
    check("la pantalla de duplicar se abre", "/duplicar" in page.url, page.url)
    resumen = page.locator(".duplicate-summary").inner_text()
    check("el resumen dice de que factura se parte", folio_a in resumen)
    check("y avisa de que se reinicia el cliente", "reinicia" in resumen.lower())
    page.screenshot(path=f"{SHOTS}/02-duplicar.png")

    fill(page, "customer_name", "Segundo interesado")
    page.click('button:has-text("Crear factura duplicada")')
    check("la copia se crea y abre su editor", "/editar" in page.url, page.url)

    folio_copia = valor(page, "folio")
    check("la copia tiene folio propio", folio_copia != folio_a, f"{folio_a} -> {folio_copia}")
    check("la copia NACE COMO BORRADOR", valor(page, "status") == "draft")
    check("la copia no hereda la fecha de emision", valor(page, "issue_date") == "")
    check("la copia no hereda la autorizacion", valor(page, "authorization") == "")
    check("la copia conserva el vehiculo", valor(page, "vehicle_title") == "2021 Honda Civic Sport")
    check("la copia conserva el VIN", valor(page, "vehicle_vin") == VIN_A)
    check("la copia conserva el precio", valor(page, "pricing_vehicle_price").startswith("412500"))
    check("el cliente nuevo se ha aplicado", valor(page, "customer_name") == "Segundo interesado")
    check("se marca como copia", "copia de otra factura" in page.content())
    url_copia = page.url

    page.goto(url_a)
    check("la factura de origen NO ha cambiado de estado", valor(page, "status") == "pending")
    check("la factura de origen conserva su folio", valor(page, "folio") == folio_a)

    # -------------------------------------------------------------------------
    print("\n7 · Duplicar pidiendo 'pago pendiente' sin datos completos")
    id_a = url_a.split("/facturas/")[1].split("/")[0]
    page.goto(f"{BASE}/facturas/{id_a}/duplicar")
    page.select_option('[name="status"]', "pending")
    page.click('button:has-text("Crear factura duplicada")')
    check(
        "sin cliente ni fechas, la copia se queda en borrador",
        valor(page, "status") == "draft",
        valor(page, "status"),
    )

    # -------------------------------------------------------------------------
    print("\n8 · Agrupacion por VIN")
    page.goto(f"{BASE}/vehiculos")
    fila = page.locator("tbody tr", has_text=VIN_A)
    check("el vehiculo aparece agrupado", fila.count() == 1)
    check("y cuenta los tres interesados", "3 interesados" in fila.inner_text(), fila.inner_text().replace("\n", " ")[:80])
    page.screenshot(path=f"{SHOTS}/03-vehiculos.png")

    page.goto(f"{BASE}/vehiculos/{VIN_A}")
    filas = page.locator("tbody tr").count()
    check("el historial del vehiculo lista las tres", filas == 3, f"{filas} filas")
    check("el historial marca cuales son copias", page.locator("td", has_text="copia").count() >= 2)
    page.screenshot(path=f"{SHOTS}/04-historial-vin.png")

    page.goto(url_copia)
    check(
        "el editor enseña el historial del mismo VIN",
        page.locator("summary", has_text="Historial del vehículo").count() == 1,
    )

    # -------------------------------------------------------------------------
    print("\n9 · Aviso cuando el vehiculo ya tiene una factura avanzada")
    page.goto(url_a)
    page.select_option('[name="status"]', "generated")
    page.click('button.btn.blue[type="submit"]')
    check("la de origen pasa a PDF generado", valor(page, "status") == "generated")

    page.goto(url_copia)
    check(
        "las otras facturas del vehiculo avisan",
        page.locator(".warning-box").count() >= 1,
    )
    check(
        "el aviso nombra la factura comprometida",
        folio_a in page.locator(".warning-box").first.inner_text(),
    )

    # -------------------------------------------------------------------------
    print("\n10 · Busqueda")
    page.goto(f"{BASE}/facturas?q=Segundo interesado")
    check("buscar por cliente encuentra la copia", page.locator("tbody tr:not(.empty-row)").count() == 1)
    page.goto(f"{BASE}/facturas?q={VIN_A}")
    check("buscar por VIN devuelve las tres", page.locator("tbody tr:not(.empty-row)").count() == 3)
    page.goto(f"{BASE}/facturas?q={folio_a}")
    check("buscar por folio devuelve una", page.locator("tbody tr:not(.empty-row)").count() == 1)

    # -------------------------------------------------------------------------
    print("\n11 · Registro de actividad")
    page.goto(f"{BASE}/actividad")
    texto = page.locator("table").inner_text()
    for evento in ["Borrador guardado", "Factura editada", "Factura duplicada"]:
        check(f"queda registrado: {evento}", evento in texto)
    check("el registro nombra el folio", folio_copia in texto)
    page.screenshot(path=f"{SHOTS}/05-actividad.png")

    # -------------------------------------------------------------------------
    print("\n12 · Las pantallas nuevas en modo Noche")
    # La maqueta fijaba fondo claro en .duplicate-summary pero dejaba que el
    # texto heredara el color, asi que en Noche salia blanco sobre blanco. Esto
    # no lo ve ninguna prueba funcional: solo se ve mirando.
    page.goto(f"{BASE}/facturas/{id_a}/duplicar")
    page.evaluate("setTheme('night')")
    page.wait_for_timeout(250)
    fondo = page.evaluate(
        "getComputedStyle(document.querySelector('.duplicate-summary')).backgroundColor"
    )
    canales = [int(n) for n in fondo.replace("rgb(", "").replace(")", "").split(",")[:3]]
    check("en Noche, el resumen de duplicado tiene fondo oscuro", sum(canales) / 3 < 90, fondo)
    page.screenshot(path=f"{SHOTS}/06-duplicar-noche.png")
    page.evaluate("setTheme('light')")

    # -------------------------------------------------------------------------
    print("\n13 · Nada de esto se puede hacer sin sesion")
    page.goto(f"{BASE}/salir")
    for ruta in ["/facturas/nueva", "/vehiculos", f"/vehiculos/{VIN_A}"]:
        page.goto(BASE + ruta)
        check(f"sin sesion, {ruta} lleva al acceso", page.url.endswith("/acceso"), page.url)

    browser.close()

print(f"\n{'=' * 58}\n{len(ok)} comprobaciones correctas, {len(fallos)} fallos")
if fallos:
    for f in fallos:
        print(f"  FALLA: {f}")
    sys.exit(1)
print("Fase B verificada.")
