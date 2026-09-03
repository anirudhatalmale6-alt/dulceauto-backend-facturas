"""
Comprobacion del Bloque B (Call Center Operador V1.4).

Sigue los criterios de aceptacion del alcance cerrado, con sus palabras:

    - Usuario Operador no puede abrir rutas Admin.
    - Busqueda por folio correcto encuentra la reserva.
    - Folio inexistente devuelve mensaje claro.
    - Paso 1 bloquea continuacion si no se verifican al menos 2 datos.
    - FAQ inactiva no aparece.
    - Nota queda asociada al folio correcto.
    - Sugerencia FAQ queda pendiente, no publicada.
    - Operador no puede modificar factura, banco, estado ni Configuracion.
    - Claro / Suave / Noche funcionan.

Se ejecuta contra el servidor de verdad, por HTTP y con sesion, no llamando a
las funciones por dentro: lo que interesa comprobar es lo que ocurre cuando el
Operador pulsa, y sobre todo lo que ocurre cuando escribe una direccion a mano.

    python verificar_bloque_b.py http://127.0.0.1:PUERTO

La base tiene que ser una COPIA: el guion escribe notas.

Solo stdlib, para no anadir ninguna dependencia al proyecto.
"""
import http.cookiejar
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8731").rstrip("/")

ADMIN_USER, ADMIN_PASS = "admin", "DulceAuto2026"
MASTER = "Master2026"
OPERADOR_USER, OPERADOR_PASS = "operador", "Operador2026"

_ok = 0
_fallos: list[str] = []


def check(punto: str, condicion: bool, detalle: str = "") -> bool:
    global _ok
    if condicion:
        _ok += 1
        print(f"  OK    {punto}" + (f"  · {detalle}" if detalle else ""))
    else:
        _fallos.append(punto)
        print(f"  FALLO {punto}" + (f"  · {detalle}" if detalle else ""))
    return condicion


def plano(html: str) -> str:
    """El HTML con los espacios colapsados en uno solo.

    Buscar una frase literal dentro del HTML crudo NO funciona: la plantilla
    parte las lineas donde le conviene y "sin respuesta aprobada" aparece en el
    fuente como "sin\n          respuesta aprobada". Sin esto, la bateria da
    fallos con el codigo perfectamente correcto -- me paso en la primera
    pasada, con dos comprobaciones.
    """
    return " ".join(html.split())


class Respuesta:
    def __init__(self, url: str, text: str, status_code: int):
        self.url = url
        # .text se guarda ya aplanado: asi ninguna comprobacion futura puede
        # volver a caer en lo mismo por descuido.
        self.text = plano(text)
        self.crudo = text
        self.status_code = status_code


class Cliente:
    """Cliente HTTP con cookies propias, para poder tener dos sesiones vivas
    a la vez (una de Admin y otra de Operador) y comparar que ve cada una."""

    def __init__(self, base: str):
        self.base = base
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )

    def _abrir(self, url: str, datos: bytes | None) -> Respuesta:
        # "datos is not None": un POST sin campos llega como b"", que es falso,
        # y se enviaria como GET.
        metodo = "POST" if datos is not None else "GET"
        peticion = urllib.request.Request(url, data=datos, method=metodo)
        if datos is not None:
            peticion.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with self.opener.open(peticion, timeout=60) as r:
                return Respuesta(r.geturl(), r.read().decode("utf-8", "replace"), r.status)
        except urllib.error.HTTPError as e:
            return Respuesta(url, e.read().decode("utf-8", "replace"), e.code)

    def get(self, ruta: str) -> Respuesta:
        return self._abrir(self.base + ruta, None)

    def post(self, ruta: str, data: dict | None = None) -> Respuesta:
        cuerpo = urllib.parse.urlencode(data or {}, encoding="utf-8").encode()
        return self._abrir(self.base + ruta, cuerpo)


def sin_contador_de_notas(html: str) -> str:
    """El listado, quitando el aviso de cuantas notas tiene cada factura.

    Ese contador SI cambia cuando el Operador escribe una nota, y debe cambiar:
    es justo la senal de que hay algo anotado en esa reserva. Lo que esta
    bateria comprueba es otra cosa -- que la FACTURA no se ha tocado -- asi que
    se compara el listado sin esa parte. Comparar el HTML entero mezclaba las
    dos preguntas y daba un fallo con el codigo correcto.
    """
    return re.sub(r"\d+ notas?", "", html)


def entrar_admin() -> Cliente:
    c = Cliente(BASE)
    r = c.post("/acceso", data={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert "/acceso" not in r.url, "no se ha podido entrar como Admin"
    return c


def entrar_operador() -> Cliente:
    c = Cliente(BASE)
    r = c.post("/operador/acceso", data={"username": OPERADOR_USER, "password": OPERADOR_PASS})
    assert "/operador/acceso" not in r.url, "no se ha podido entrar como Operador"
    return c


def primer_folio(admin: Cliente) -> str:
    """Un folio real de la base con la que se esta probando.

    No se escribe ninguno a fuego: la bateria de Fase A ya ensenio que un dato
    fijo del entorno de desarrollo convierte la suite en inutil contra
    cualquier otra base.
    """
    r = admin.get("/facturas")
    folios = re.findall(r">(RES-\d+)<", r.text)
    assert folios, "no hay ninguna factura en esta base con la que probar"
    return folios[0]


# --- rutas de Administracion que el Operador NO debe poder abrir -------------
#
# La lista es deliberadamente larga y toca las cuatro familias: ver, crear,
# modificar y configurar. El bloqueo es central, asi que si una sola de estas
# pasara, el punto unico no estaria haciendo su trabajo.
RUTAS_ADMIN_GET = [
    "/",
    "/facturas",
    "/facturas/nueva",
    "/plantillas",
    "/actividad",
    "/configuracion",
    "/marcas",
]

RUTAS_ADMIN_POST = [
    ("/facturas/nueva", {"customer_name": "Intruso"}),
    ("/configuracion/desbloquear", {"master_password": "Master2026"}),
    ("/configuracion/guardar", {"folio.prefix": "HACK-"}),
]


def main() -> int:
    print(f"\nBloque B · Call Center Operador · {BASE}")

    admin = entrar_admin()
    folio = primer_folio(admin)
    print(f"  (base de prueba: se usa el folio {folio})\n")

    # --- 1 · acceso independiente -------------------------------------------
    print("1 · Acceso Operador independiente del de Admin")

    anonimo = Cliente(BASE)
    r = anonimo.get("/operador")
    check(
        "sin sesion, /operador lleva a su propio acceso",
        r.url.endswith("/operador/acceso"),
        r.url,
    )

    malo = Cliente(BASE)
    r = malo.post("/operador/acceso", data={"username": OPERADOR_USER, "password": "loquesea"})
    check("contrasena de Operador incorrecta no entra", r.url.endswith("/operador/acceso"))

    cruzado = Cliente(BASE)
    r = cruzado.post("/operador/acceso", data={"username": ADMIN_USER, "password": ADMIN_PASS})
    check(
        "las credenciales de Admin NO abren el panel de Operador",
        r.url.endswith("/operador/acceso"),
        "son cuentas separadas",
    )

    operador = entrar_operador()
    r = operador.get("/operador")
    check("credenciales de Operador correctas entran", r.status_code == 200 and "/operador/acceso" not in r.url)

    # El Operador no conoce ni necesita la contrasena de Admin: probarla en el
    # acceso del panel tampoco puede funcionar.
    otro = Cliente(BASE)
    r = otro.post("/acceso", data={"username": OPERADOR_USER, "password": OPERADOR_PASS})
    check(
        "las credenciales de Operador NO abren el panel de Admin",
        r.url.endswith("/acceso"),
    )

    # --- 2 · el bloqueo central ---------------------------------------------
    print("\n2 · El Operador no puede abrir rutas de Administracion")

    for ruta in RUTAS_ADMIN_GET:
        r = operador.get(ruta)
        # Lo que se exige no es solo que no explote: es que NO devuelva la
        # pagina de Admin. Se comprueba mirando donde acaba y que no aparezca
        # el menu administrativo.
        bloqueada = r.url.rstrip("/").endswith("/operador") and "Crear / Editar" not in r.text
        check(f"GET {ruta} escrito a mano queda bloqueado", bloqueada, r.url)

    for ruta, datos in RUTAS_ADMIN_POST:
        r = operador.post(ruta, data=datos)
        bloqueada = r.url.rstrip("/").endswith("/operador")
        check(f"POST {ruta} manipulado queda bloqueado", bloqueada, r.url)

    # Y lo contrario: que el bloqueo no se haya llevado por delante al Admin.
    r = admin.get("/facturas")
    check(
        "el Admin sigue entrando en Administracion con normalidad",
        r.status_code == 200 and "/acceso" not in r.url,
    )

    # El bloqueo queda anotado en Actividad, que es lo que permite al
    # propietario enterarse de que alguien lo intento.
    #
    # Desde el Hito A, Actividad exige la Master Password aunque la sesion de
    # Admin ya este abierta. Se comprueban las dos cosas: que el candado cerrado
    # no deja leer el registro, y que abriendolo el registro sigue estando ahi.
    r = admin.get("/actividad")
    check(
        "sin la Master Password, ni el Admin lee el registro",
        "Actividad bloqueada" in r.text and "Acceso a Administración bloqueado" not in r.text,
    )
    admin.post(
        "/configuracion/desbloquear",
        data={"master_password": MASTER, "destino": "/actividad"},
    )
    r = admin.get("/actividad")
    check(
        "los intentos bloqueados quedan en el registro de Actividad",
        "Acceso a Administración bloqueado" in r.text,
    )

    # --- 3 · busqueda por folio ---------------------------------------------
    print("\n3 · Busqueda por folio")

    r = operador.get(f"/operador?folio={folio}")
    check(f"folio correcto encuentra la reserva ({folio})", "Reserva localizada" in r.text)

    numero = re.sub(r"^\D+", "", folio)
    r = operador.get(f"/operador?folio={numero}")
    check("solo el numero tambien la encuentra", "Reserva localizada" in r.text, numero)

    r = operador.get(f"/operador?folio={folio.lower()}")
    check("en minusculas tambien la encuentra", "Reserva localizada" in r.text)

    r = operador.get("/operador?folio=RES-00000000")
    check(
        "folio inexistente da un mensaje claro y no un error",
        r.status_code == 200 and "No se encontró" in r.text,
    )

    r = operador.get("/operador")
    check(
        "sin buscar nada no acusa de un error que nadie ha cometido",
        "No se encontró" not in r.text and "Busca una reserva" in r.text,
    )

    # Datos de pago: tienen que venir de la factura, no de Configuracion.
    r = operador.get(f"/operador?folio={folio}&paso=3&v=name,folio&c=1&necesidad=payment")
    check(
        "los datos de pago se muestran como congelados en la factura",
        "congelados en la factura" in r.text,
    )

    # --- 4 · las dos puertas del guion --------------------------------------
    print("\n4 · Los 6 pasos, con sus reglas")

    r = operador.get(f"/operador?folio={folio}&paso=4")
    check(
        "saltar al paso 4 escribiendo la URL devuelve al paso 1",
        "Confirma al menos dos datos" in r.text and "1 · Identificar al cliente" in r.text,
    )

    r = operador.get(f"/operador?folio={folio}&paso=2&v=name")
    check(
        "un solo dato verificado no basta para pasar del paso 1",
        "Confirma al menos dos datos" in r.text,
    )

    r = operador.get(f"/operador?folio={folio}&paso=2&v=name,folio")
    check(
        "con dos datos verificados si se llega al paso 2",
        "2 · Confirmar la factura" in r.text,
    )

    r = operador.get(f"/operador?folio={folio}&paso=3&v=name,folio")
    check(
        "sin confirmar la factura no se pasa del paso 2",
        "Confirma que los datos principales coinciden" in r.text,
    )

    r = operador.get(f"/operador?folio={folio}&paso=3&v=name,folio&c=1")
    check("confirmando si se llega al paso 3", "3 · Identificar la necesidad" in r.text)

    completo = f"folio={folio}&v=name,folio&c=1"
    for numero_paso, marca in [
        (4, "4 · Resolver dudas"),
        (5, "5 · Confirmar decisión"),
        (6, "6 · Registrar nota"),
    ]:
        r = operador.get(f"/operador?{completo}&paso={numero_paso}")
        check(f"paso {numero_paso} se muestra", marca in r.text)

    r = operador.get(f"/operador?folio={folio}&paso=99&v=name,folio&c=1")
    check("un paso fuera de rango no rompe", r.status_code == 200)

    # Los 6 motivos del paso 3 estan todos.
    r = operador.get(f"/operador?{completo}&paso=3")
    motivos = ["Resolver una duda", "Entrega", "Pago", "Actualizar información",
               "Problema / incidencia", "Otra necesidad"]
    check(
        "los 6 motivos del paso 3 estan",
        all(m in r.text for m in motivos),
        f"{sum(1 for m in motivos if m in r.text)}/6",
    )

    # --- 5 · la guia de respuestas ------------------------------------------
    print("\n5 · Guia de respuestas (FAQ)")

    r = operador.get(f"/operador?{completo}&paso=4")
    check(
        "el Operador ve respuestas aprobadas",
        "Respuesta recomendada" in r.text,
    )
    check(
        "una pregunta sin respuesta aprobada NO se ofrece como respuesta",
        "sin respuesta aprobada" in r.text
        and "Esta respuesta debe definirse" not in r.text,
    )

    r = operador.get(f"/operador?{completo}&paso=4&q=garantia")
    check(
        "el buscador de la guia filtra e ignora los acentos",
        "garantía" in r.text.lower() or "garantia" in r.text.lower(),
    )

    r = operador.get(f"/operador?{completo}&paso=4&q=zzzzzzzz")
    check(
        "una busqueda sin resultados no rompe y avisa",
        r.status_code == 200 and "No hay respuestas aprobadas" in r.text,
    )

    # --- 6 · notas -----------------------------------------------------------
    print("\n6 · Notas del Operador")

    marca = f"prueba-automatica-{len(_fallos)}-{_ok}"
    r = operador.post(
        "/operador/notas",
        data={"folio": folio, "tipo": "cliente", "nota": f"Nota de {marca}", "paso": 6},
    )
    check("la nota se guarda", "Nota guardada" in r.text or marca in r.text)

    r = operador.get(f"/operador?folio={folio}&v=name,folio&c=1&paso=6")
    check("la nota queda asociada al folio correcto", marca in r.text, folio)

    r = operador.post(
        "/operador/notas",
        data={"folio": folio, "tipo": "cliente", "nota": "   ", "paso": 6},
    )
    check("una nota vacia se rechaza", "Escribe el contenido" in r.text)

    r = operador.post(
        "/operador/notas",
        data={"folio": folio, "tipo": "inventado", "nota": "tipo manipulado", "paso": 6},
    )
    check("un tipo de nota manipulado se rechaza", "no válido" in r.text.lower())

    r = operador.post(
        "/operador/notas",
        data={"folio": "RES-00000000", "tipo": "cliente", "nota": "sin reserva", "paso": 6},
    )
    check("una nota sobre un folio inexistente no se guarda", "No se encontró" in r.text)

    # Sugerencia FAQ: se registra, pero no se publica.
    sugerencia = f"sugerencia-{marca}"
    operador.post(
        "/operador/notas",
        data={"folio": folio, "tipo": "faq", "nota": sugerencia, "paso": 6},
    )
    r = operador.get(f"/operador?folio={folio}&v=name,folio&c=1&paso=4")
    # Se mira dentro de la guia, no en toda la pagina: el panel de notas se
    # pinta en todos los pasos a proposito, y ahi la sugerencia SI tiene que
    # verse. Lo que no puede pasar es que aparezca como respuesta publicada.
    guia = r.text.split("faqLayout", 1)[-1].split("</aside>", 1)[0]
    check("la sugerencia FAQ NO aparece publicada en la guia", sugerencia not in guia)
    r = operador.get(f"/operador?folio={folio}&v=name,folio&c=1&paso=6")
    check("la sugerencia FAQ si queda registrada como nota", sugerencia in r.text)

    # --- 7 · el Operador no modifica nada -----------------------------------
    print("\n7 · El Operador no modifica la factura")

    antes = admin.get(f"/facturas?q={folio}").text
    operador.post("/operador/notas", data={"folio": folio, "tipo": "seguimiento",
                                           "nota": "otra nota", "paso": 6})
    # Intentos directos contra las rutas que cambian cosas.
    for ruta, datos in [
        (f"/facturas/1/editar", {"customer_name": "Modificado por el operador"}),
        (f"/facturas/1/archivar", {}),
        (f"/facturas/1/eliminar", {"master_password": "Master2026"}),
    ]:
        r = operador.post(ruta, data=datos)
        check(f"POST {ruta} desde Operador queda bloqueado",
              r.url.rstrip("/").endswith("/operador"), r.url)

    despues = admin.get(f"/facturas?q={folio}").text
    check(
        "la factura sigue exactamente igual tras toda la sesion de Operador",
        sin_contador_de_notas(antes) == sin_contador_de_notas(despues),
    )

    # --- 7b · el Admin puede cambiar la contrasena del Operador --------------
    print("\n7b · Cambiar la contrasena del Operador desde Configuracion")

    admin.post("/configuracion/desbloquear", data={"master_password": MASTER})
    r = admin.get("/configuracion")
    check("la tarjeta de la cuenta Operador esta en Configuracion", "Cuenta Operador" in r.text)

    NUEVA = "OperadorTemporal2026"
    r = admin.post("/configuracion/contrasenas", data={
        "which": "operator", "username": OPERADOR_USER,
        "new_password": NUEVA, "confirm_password": NUEVA,
    })
    check("el Admin cambia la contrasena del Operador", "cuenta Operador actualizada" in r.text)

    c = Cliente(BASE)
    r = c.post("/operador/acceso", data={"username": OPERADOR_USER, "password": NUEVA})
    check("la contrasena nueva entra", not r.url.endswith("/operador/acceso"))

    c = Cliente(BASE)
    r = c.post("/operador/acceso", data={"username": OPERADOR_USER, "password": OPERADOR_PASS})
    check("la contrasena antigua ya NO entra", r.url.endswith("/operador/acceso"))

    # Se deja como estaba: la bateria tiene que poder ejecutarse dos veces
    # seguidas y la segunda empezar en las mismas condiciones que la primera.
    admin.post("/configuracion/contrasenas", data={
        "which": "operator", "username": OPERADOR_USER,
        "new_password": OPERADOR_PASS, "confirm_password": OPERADOR_PASS,
    })
    c = Cliente(BASE)
    r = c.post("/operador/acceso", data={"username": OPERADOR_USER, "password": OPERADOR_PASS})
    check("restaurada, vuelve a entrar", not r.url.endswith("/operador/acceso"))

    # Y el Operador no puede cambiarsela a si mismo ni tocar las otras dos.
    r = operador.post("/configuracion/contrasenas", data={
        "which": "admin", "new_password": "loquesea1", "confirm_password": "loquesea1",
    })
    check("el Operador NO puede cambiar contrasenas", r.url.rstrip("/").endswith("/operador"))
    c = Cliente(BASE)
    r = c.post("/acceso", data={"username": ADMIN_USER, "password": ADMIN_PASS})
    check("y la del Admin sigue siendo la misma", not r.url.endswith("/acceso"))

    # --- 8 · temas -----------------------------------------------------------
    print("\n8 · Los tres modos visuales")

    for tema, clase in [("light", ""), ("soft", "theme-soft"), ("night", "theme-night")]:
        c = Cliente(BASE)
        c.cookies.set_cookie(
            http.cookiejar.Cookie(
                0, "da_theme", tema, None, False, "127.0.0.1", False, False,
                "/", True, False, None, False, None, None, {},
            )
        )
        c.post("/operador/acceso", data={"username": OPERADOR_USER, "password": OPERADOR_PASS})
        r = c.get("/operador")
        cuerpo = re.search(r"<body class=\"([^\"]*)\"", r.text)
        actual = cuerpo.group(1).strip() if cuerpo else "?"
        check(f"tema {tema} se aplica en el panel de Operador", actual == clase, f"«{actual}»")

    # --- 9 · salir -----------------------------------------------------------
    print("\n9 · Cierre de sesion")

    r = operador.get("/operador/salir")
    check("salir devuelve al acceso de Operador", r.url.endswith("/operador/acceso"))
    r = operador.get("/operador")
    check("tras salir, /operador ya no abre", r.url.endswith("/operador/acceso"))

    r = admin.get("/actividad")
    for etiqueta in ["Acceso de Operador", "Consulta de reserva (Operador)", "Nota de Operador"]:
        check(f"queda registrado en Actividad: {etiqueta}", etiqueta in r.text)

    # --- resultado -----------------------------------------------------------
    print("\n" + "=" * 62)
    print(f"{_ok} comprobaciones correctas, {len(_fallos)} fallos")
    if _fallos:
        for f in _fallos:
            print(f"  FALLO: {f}")
        return 1
    print("Bloque B verificado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
