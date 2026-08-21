"""
Comprobacion de la Fase D en el panel.

El motor de PDF se comprueba aparte, sin servidor, en verificar_pdf.py. Aqui se
comprueba lo que solo se ve usando el panel: que el boton haga lo que dice, que
un borrador no se pueda imprimir, que el PDF se descargue de verdad y que
generarlo no mueva el estado de la operacion.

    python verificar_fase_d.py [http://127.0.0.1:8731] [/tmp/fase-d]
"""
import sqlite3
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8731").rstrip("/")
SHOTS = sys.argv[2] if len(sys.argv) > 2 else "/tmp/fase-d"
Path(SHOTS).mkdir(parents=True, exist_ok=True)

USER, PASSWORD = "admin", "DulceAuto2026"
VIN = "5FNRL38209B006842"
CLIENTE = "Cliente de la Fase D"
DB = Path(__file__).parent / "data" / "dulceauto.db"
SNAPSHOTS = Path(__file__).parent / "data" / "snapshots"

ok, fallos = [], []


def check(nombre, condicion, extra=""):
    (ok if condicion else fallos).append(nombre)
    print(("  OK   " if condicion else "  FALLA") + f" {nombre}" + (f"  [{extra}]" if extra else ""))


def fill(page, campo, valor):
    page.fill(f'[name="{campo}"]', valor)


def elegir(page, campo, valor):
    page.select_option(f'[name="{campo}"]', valor)


def limpiar_datos_de_pruebas_anteriores():
    """Borra la factura y los snapshots de la ejecucion anterior."""
    if not DB.exists():
        return
    import shutil

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
        for i in ids:
            shutil.rmtree(SNAPSHOTS / str(i), ignore_errors=True)
    con.close()
    print(f"  (limpieza: {len(ids)} facturas de pruebas anteriores borradas)")


print("\n0 · Punto de partida limpio")
limpiar_datos_de_pruebas_anteriores()

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 980})

    page.goto(f"{BASE}/acceso")
    fill(page, "username", USER)
    fill(page, "password", PASSWORD)
    page.click('button[type="submit"]')

    # -------------------------------------------------------------------------
    print("\n1 · Un borrador no se imprime")
    page.goto(f"{BASE}/facturas/nueva")
    fill(page, "customer_name", CLIENTE)
    fill(page, "customer_email", "cliente.fased@ejemplo.mx")
    fill(page, "customer_city", "Guadalajara")
    fill(page, "vehicle_title", "2021 Honda Odyssey Touring")
    fill(page, "vehicle_vin", VIN)
    fill(page, "vehicle_year", "2021")
    fill(page, "pricing_vehicle_price", "515000")
    fill(page, "pricing_reservation_amount", "6000")
    fill(page, "issue_date", "2026-08-21")
    fill(page, "delivery_date", "2026-09-10")
    page.click('button[name="save_as"][value="draft"]')
    factura_id = page.url.split("/facturas/")[1].split("/")[0]
    check("el borrador se ha creado", "/editar" in page.url, page.url)

    page.goto(f"{BASE}/facturas/{factura_id}/vista-previa")
    page.click("button.btn.gold")
    page.wait_for_load_state()
    avisos = page.locator(".alert").all_inner_texts()
    check("no deja imprimir un borrador", any("borrador no se imprime" in a for a in avisos),
          " | ".join(a[:60] for a in avisos))
    check("y no ha quedado ningun PDF", page.locator("text=Historial de PDF").count() == 0)

    # -------------------------------------------------------------------------
    print("\n2 · Con la factura en pago pendiente si se imprime")
    page.goto(f"{BASE}/facturas/{factura_id}/editar")
    elegir(page, "status", "pending")
    page.click('button.btn.blue[type="submit"]')
    check("la factura sale de borrador", page.locator(".alert.error").count() == 0)

    page.goto(f"{BASE}/facturas/{factura_id}/vista-previa")
    page.click("button.btn.gold")
    page.wait_for_load_state()
    avisos = page.locator(".alert").all_inner_texts()
    check("el PDF se genera", any("PDF generado" in a for a in avisos),
          " | ".join(a[:60] for a in avisos))
    check("y avisa de que queda una copia congelada",
          any("copia congelada" in a for a in avisos))
    check("aparece el historial de PDF", page.locator("text=Historial de PDF").count() == 1)
    filas = page.locator("table tbody tr")
    check("con una version", filas.count() == 1, f"{filas.count()} filas")
    check("marcada como v1", "v1" in filas.first.inner_text())
    page.screenshot(path=f"{SHOTS}/d1-pdf-generado.png")

    # -------------------------------------------------------------------------
    print("\n3 · Generar el PDF no mueve la operacion")
    page.goto(f"{BASE}/facturas/{factura_id}/editar")
    check("el estado sigue siendo pago pendiente",
          page.input_value('[name="status"]') == "pending", page.input_value('[name="status"]'))
    page.goto(f"{BASE}/facturas/{factura_id}/documento")
    check("y el documento no ha avanzado la barra",
          "active" in (page.locator('[data-step="2"]').get_attribute("class") or ""))

    # -------------------------------------------------------------------------
    print("\n4 · El PDF se descarga y es un PDF")
    respuesta = page.request.get(f"{BASE}/facturas/{factura_id}/pdf")
    check("responde 200", respuesta.status == 200, str(respuesta.status))
    check("con tipo application/pdf", "application/pdf" in (respuesta.headers.get("content-type") or ""),
          respuesta.headers.get("content-type"))
    cuerpo = respuesta.body()
    check("y el archivo empieza como un PDF", cuerpo[:5] == b"%PDF-", str(cuerpo[:5]))
    check("con contenido de verdad", len(cuerpo) > 50_000, f"{len(cuerpo)} bytes")
    # Una sola pagina: es la regla del documento aprobado.
    check("una sola pagina", cuerpo.count(b"/Type /Page") - cuerpo.count(b"/Type /Pages") == 1,
          f"{cuerpo.count(b'/Type /Page') - cuerpo.count(b'/Type /Pages')}")

    # -------------------------------------------------------------------------
    print("\n5 · Volver a generarlo crea otra version, sin borrar la anterior")
    page.goto(f"{BASE}/facturas/{factura_id}/editar")
    fill(page, "customer_city", "Zapopan")
    page.click('button.btn.blue[type="submit"]')
    page.goto(f"{BASE}/facturas/{factura_id}/vista-previa")
    page.click("button.btn.gold")
    page.wait_for_load_state()
    filas = page.locator("table tbody tr")
    check("ahora hay dos versiones", filas.count() == 2, f"{filas.count()} filas")
    check("la primera de la lista es la v2", "v2" in filas.first.inner_text())

    v1 = page.request.get(f"{BASE}/facturas/{factura_id}/pdf?version=1")
    v2 = page.request.get(f"{BASE}/facturas/{factura_id}/pdf?version=2")
    check("la version 1 se sigue pudiendo descargar", v1.status == 200 and v1.body()[:5] == b"%PDF-")
    check("y la 2 tambien", v2.status == 200 and v2.body()[:5] == b"%PDF-")
    check("son archivos distintos", v1.body() != v2.body())

    # -------------------------------------------------------------------------
    print("\n6 · La copia congelada de la v1 conserva los datos de entonces")
    carpeta = SNAPSHOTS / factura_id / "v1"
    html = (carpeta / "documento.html").read_text(encoding="utf-8")
    check("la carpeta de la v1 existe", carpeta.exists(), str(carpeta))
    check("y guarda la ciudad que tenia al imprimirla",
          "Guadalajara" in html and "Zapopan" not in html)
    check("con sus propios archivos", (carpeta / "assets/css/factura.css").exists())
    check("incluidas las tipografias",
          len(list((carpeta / "assets/fonts").glob("*.woff2"))) >= 4)

    # -------------------------------------------------------------------------
    print("\n7 · Queda registrado en Actividad")
    page.goto(f"{BASE}/actividad")
    texto = page.locator("table").inner_text()
    check("se anota la generacion del PDF", "PDF generado" in texto)
    check("y con el folio de la factura", "RES-" in texto)

    # -------------------------------------------------------------------------
    print("\n7bis · Configuracion se edita y se valida")
    page.goto(f"{BASE}/configuracion")
    check("arranca bloqueada", page.locator(".locked-panel").count() == 1)
    page.fill('[name="master_password"]', "Master2026")
    page.click('button[type="submit"]')
    check("con la Master Password se abre", page.locator(".settings-hero").count() == 1)

    # Una CLABE con el digito de control mal no puede guardarse: se copiaria a
    # cada factura nueva y el cliente transferiria a una cuenta que no existe.
    page.fill('[name="ajuste:banking.account_number"] >> nth=0', "012180001234567890")
    page.click('button:has-text("Guardar México")')
    page.wait_for_load_state()
    avisos = page.locator(".alert").all_inner_texts()
    check("una CLABE con el digito mal se rechaza",
          any("dígito de control" in a for a in avisos), " | ".join(a[:60] for a in avisos))

    page.goto(f"{BASE}/configuracion")
    valor = page.input_value('[name="ajuste:banking.account_number"] >> nth=0')
    check("y no se ha guardado", valor != "012180001234567890", valor)

    # La URL del QR tiene que poder cambiarse sin tocar codigo: es lo que pidio
    # el cliente para cuando tenga el dominio definitivo.
    page.fill('[name="ajuste:qr.base_url"]', "https://ejemplo-de-prueba.mx/verificar/")
    page.click('button:has-text("Guardar marca")')
    page.wait_for_load_state()
    page.goto(f"{BASE}/configuracion")
    check("la URL del QR se guarda",
          page.input_value('[name="ajuste:qr.base_url"]') == "https://ejemplo-de-prueba.mx/verificar/",
          page.input_value('[name="ajuste:qr.base_url"]'))

    # Una factura ya emitida no puede cambiar porque se toque Configuracion.
    page.goto(f"{BASE}/facturas/{factura_id}/documento")
    check("la factura ya creada conserva su URL de verificacion",
          "ejemplo-de-prueba" not in page.content())

    page.goto(f"{BASE}/configuracion")
    page.fill('[name="ajuste:qr.base_url"]', "https://dulceauto.mx/verificar/")
    page.click('button:has-text("Guardar marca")')
    page.wait_for_load_state()

    # -------------------------------------------------------------------------
    print("\n8 · Nada de esto sin sesion")
    page.context.clear_cookies()
    r = page.request.post(f"{BASE}/facturas/{factura_id}/pdf", max_redirects=0)
    check("sin sesion no se puede generar", r.status in (302, 303),
          f"{r.status} {r.headers.get('location')}")
    check("y lleva al acceso", "/acceso" in (r.headers.get("location") or ""))
    r = page.request.get(f"{BASE}/facturas/{factura_id}/pdf", max_redirects=0)
    check("sin sesion tampoco se descarga", r.status in (302, 303), str(r.status))

    browser.close()

print(f"\n{'=' * 58}\n{len(ok)} comprobaciones correctas, {len(fallos)} fallos")
for f in fallos:
    print(f"  FALLA: {f}")
if not fallos:
    print("Fase D verificada.")
sys.exit(1 if fallos else 0)
