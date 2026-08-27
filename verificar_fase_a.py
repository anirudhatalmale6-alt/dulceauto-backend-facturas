"""
Comprobacion de la Fase A de punta a punta.

No comprueba que las paginas "carguen": comprueba que hagan lo que tienen que
hacer. En concreto, que la puerta de Configuracion no se pueda saltar, que es lo
unico de esta fase que seria grave si fallara.

    python verificar_fase_a.py [http://127.0.0.1:8731]
"""
import re
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8731"
SHOTS = sys.argv[2] if len(sys.argv) > 2 else "/tmp/fase-a"

USER, PASSWORD = "admin", "DulceAuto2026"
MASTER = "Master2026"

ok, fallos = [], []


def check(nombre, condicion, extra=""):
    (ok if condicion else fallos).append(nombre)
    print(("  OK   " if condicion else "  FALLA") + f" {nombre}" + (f"  [{extra}]" if extra else ""))


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})

    print("\n1 · Acceso")
    page.goto(f"{BASE}/facturas")
    check("sin sesion, cualquier vista lleva al acceso", page.url.endswith("/acceso"), page.url)

    page.fill('input[name="username"]', USER)
    page.fill('input[name="password"]', "contrasena-incorrecta")
    page.click('button[type="submit"]')
    check("contrasena incorrecta no entra", page.url.endswith("/acceso"))
    check("el error se muestra", page.locator(".alert.error").count() == 1)

    page.fill('input[name="username"]', USER)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    check("credenciales correctas entran", page.url.rstrip("/") == BASE.rstrip("/"), page.url)
    page.screenshot(path=f"{SHOTS}/01-dashboard.png")

    print("\n2 · Las seis vistas")
    for ruta, marca, archivo in [
        ("/", "Dashboard", "01-dashboard"),
        ("/facturas", "Facturas", "02-facturas"),
        ("/facturas/nueva", "Crear", "03-editor"),
        ("/plantillas", "Plantillas", "04-plantillas"),
        ("/actividad", "Actividad", "05-actividad"),
    ]:
        page.goto(BASE + ruta)
        check(f"{ruta} responde", marca.lower() in page.locator("h1").inner_text().lower())
        page.screenshot(path=f"{SHOTS}/{archivo}.png")

    print("\n3 · Busqueda")
    # Los terminos de busqueda se sacan de la primera factura que haya en el
    # listado, no van escritos aqui. Antes se buscaba "Audi" y RES-87240, que
    # eran datos del entorno de desarrollo: contra cualquier otra base la prueba
    # fallaba sin que hubiera nada roto. Asi la bateria se puede ejecutar contra
    # los datos que sean y el resultado sigue siendo limpio.
    page.goto(f"{BASE}/facturas")
    filas_todas = page.locator("tbody tr:not(.empty-row)")
    if filas_todas.count() == 0:
        check("busqueda: hay alguna factura con la que probar", False, "listado vacio")
    else:
        # Solo la primera linea, igual que con la celda del vehiculo: debajo
        # del folio puede ir el aviso de "N notas" del Call Center, y con el
        # texto entero pegado la busqueda no encuentra nada. La celda es de dos
        # lineas desde que existe el modulo de Operador.
        celda_folio = filas_todas.first.locator("td").nth(0).inner_text().strip()
        folio = celda_folio.splitlines()[0].strip() if celda_folio else ""
        # Se recorren las filas hasta dar con un vehiculo del que salga una
        # palabra utilizable: la primera factura puede no tener vehiculo escrito.
        termino = ""
        for i in range(min(filas_todas.count(), 10)):
            celda = filas_todas.nth(i).locator("td").nth(2).inner_text().strip()
            # Solo la primera linea: debajo va el VIN, que es alfanumerico y da
            # trozos de letras sin sentido como termino de busqueda.
            titulo = celda.splitlines()[0] if celda else ""
            palabras = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{4,}", titulo)
            if palabras:
                termino = max(palabras, key=len)
                break

        if termino:
            page.goto(f"{BASE}/facturas?q={termino}")
            # Se comprueba que filtre, no que salga una sola fila: duplicar una
            # factura del mismo vehiculo es legitimo y entonces salen las dos.
            # Lo que no puede aparecer es una que no tenga que ver.
            filas = page.locator("tbody tr:not(.empty-row)")
            textos = [filas.nth(i).inner_text() for i in range(filas.count())]
            check(
                f"buscar por vehiculo ('{termino}') devuelve resultados",
                len(textos) >= 1,
                f"{len(textos)} filas",
            )
            check(
                "y no cuela ninguna factura que no coincida",
                all(termino.lower() in t.lower() for t in textos),
                f"{len(textos)} filas",
            )
        else:
            check(
                "buscar por vehiculo",
                False,
                "ninguna de las primeras facturas tiene vehiculo escrito",
            )

        page.goto(f"{BASE}/facturas?q={folio}")
        check(
            f"buscar por folio funciona ('{folio}')",
            page.locator("tbody tr:not(.empty-row)").count() == 1,
        )

    page.goto(f"{BASE}/facturas?q=zzzzz")
    check("busqueda sin resultados no rompe", page.locator(".empty-row").count() == 1)

    print("\n4 · Master Password")
    page.goto(f"{BASE}/configuracion")
    check("Configuracion arranca bloqueada aun con sesion", page.locator(".locked-panel").count() == 1)
    check("los datos bancarios no se filtran en el HTML bloqueado", "012180001234567899" not in page.content())
    check("la Master Password no viaja al navegador", MASTER not in page.content())

    page.fill('input[name="master_password"]', "master-incorrecta")
    page.click('button[type="submit"]')
    check("Master incorrecta no abre", page.locator(".locked-panel").count() == 1)
    check("avisa del error", page.locator(".alert.error").count() == 1)

    page.fill('input[name="master_password"]', MASTER)
    page.click('button[type="submit"]')
    check("Master correcta abre", page.locator(".settings-hero").count() == 1)
    check("ahora si se ven los datos bancarios", "012180001234567899" in page.content())
    page.screenshot(path=f"{SHOTS}/06-configuracion.png")

    print("\n5 · El bloqueo vuelve a cerrarse")
    page.click('form[action="/configuracion/bloquear"] button')
    check("bloquear cierra la seccion", page.locator(".locked-panel").count() == 1)

    page.fill('input[name="master_password"]', MASTER)
    page.click('button[type="submit"]')
    page.goto(f"{BASE}/salir")
    page.goto(f"{BASE}/acceso")
    page.fill('input[name="username"]', USER)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.goto(f"{BASE}/configuracion")
    check("cerrar sesion tambien cierra Configuracion", page.locator(".locked-panel").count() == 1)

    print("\n6 · Los tres modos visuales")
    page.goto(BASE + "/")
    for tema, clase, archivo in [
        ("light", "", "07-tema-claro"),
        ("soft", "theme-soft", "08-tema-suave"),
        ("night", "theme-night", "09-tema-noche"),
    ]:
        page.evaluate(f"setTheme('{tema}')")
        page.wait_for_timeout(250)
        clases = page.locator("body").get_attribute("class") or ""
        check(f"tema {tema} se aplica", clase in clases if clase else "theme-" not in clases)
        page.screenshot(path=f"{SHOTS}/{archivo}.png")

    # El tema tiene que sobrevivir a una recarga: si no, no sirve de nada.
    page.reload()
    check("el tema persiste al recargar", "theme-night" in (page.locator("body").get_attribute("class") or ""))

    print("\n7 · Registro de actividad")
    page.goto(f"{BASE}/actividad")
    texto = page.locator("table").inner_text()
    for evento in ["Inicio de sesión", "Intento de acceso fallido", "Master Password incorrecta",
                   "Configuración desbloqueada", "Configuración bloqueada", "Cierre de sesión"]:
        check(f"queda registrado: {evento}", evento in texto)
    page.screenshot(path=f"{SHOTS}/05-actividad.png")

    browser.close()

print(f"\n{'=' * 58}\n{len(ok)} comprobaciones correctas, {len(fallos)} fallos")
if fallos:
    for f in fallos:
        print(f"  FALLA: {f}")
    sys.exit(1)
print("Fase A verificada.")
