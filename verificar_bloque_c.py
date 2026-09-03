"""
Comprobacion del Bloque C (Administracion de la guia y de las notas).

Lo que tiene que quedar demostrado:

    - El Admin crea, edita, publica, retira y reordena entradas de la guia.
    - Una pregunta SIN respuesta escrita no se puede publicar, ni por el
      formulario ni por el boton.
    - Lo que el Admin publica o retira cambia lo que ve el Operador.
    - Las sugerencias del Operador se convierten en entradas de la guia, y al
      hacerlo quedan marcadas como atendidas. No se publican solas.
    - Las notas se ven desde Administracion y desde la propia factura, y NO se
      pueden editar ni borrar desde ninguna pantalla.
    - El Operador sigue sin poder abrir nada de esto.

    python verificar_bloque_c.py http://127.0.0.1:PUERTO

La base tiene que ser una COPIA: el guion crea entradas de guia y notas.
Se limpia lo suyo al arrancar, para poder ejecutarla dos veces seguidas.

Solo stdlib.
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

# Marca con la que se reconoce todo lo que crea esta bateria, para poder
# retirarlo al empezar la siguiente pasada.
MARCA = "ZZPRUEBA"

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
    """HTML con los espacios colapsados: buscar una frase literal en el fuente
    no funciona, porque la plantilla parte las lineas donde le conviene."""
    return " ".join(html.split())


class Respuesta:
    def __init__(self, url: str, text: str, status_code: int):
        self.url = url
        self.text = plano(text)
        self.status_code = status_code


class Cliente:
    def __init__(self, base: str):
        self.base = base
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )

    def _abrir(self, url: str, datos: bytes | None) -> Respuesta:
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
        return self._abrir(self.base + ruta, urllib.parse.urlencode(data or {}).encode())


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


def filas_de_tabla(html: str) -> list[str]:
    """Las filas del <tbody>, y solo esas.

    Partir la pagina entera por "<tr" parece equivalente y no lo es: el trozo de
    la ULTIMA fila se extiende hasta el final del documento y arrastra todo lo
    que venga despues de la tabla. Aqui eso hacia que una entrada sin respuesta
    diera "Publicada" como estado, porque mas abajo hay un formulario con la
    etiqueta "Publicada: el Operador la ve". El codigo era correcto; la bateria
    no. Se recorta primero el cuerpo de la tabla.
    """
    if "<tbody>" not in html:
        return []
    cuerpo = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    return [t for t in cuerpo.split("<tr")[1:]]


def respuestas_de_la_guia(html: str) -> str:
    """Solo las tarjetas de respuesta que el Operador puede leer al cliente.

    No vale mirar la pagina entera, ni siquiera el bloque de la guia: el aviso
    de preguntas pendientes va dentro de ese bloque, y el panel de notas se
    pinta en todos los pasos a proposito. Lo que se quiere comprobar es que un
    texto NO esta ofrecido como respuesta aprobada.
    """
    return " ".join(re.findall(r'<div class="faqItem open">.*?</div> </div> </div>', html))


def bloque_pendientes(html: str) -> str:
    """El aviso de 'preguntas recogidas todavia sin respuesta aprobada'.

    Se mira aparte porque ahi es donde se colaba una entrada RETIRADA: la
    respuesta ya no se ofrecia, pero la pregunta seguia listada bajo un texto
    que afirma que no tiene respuesta aprobada. Devuelve cadena vacia si el
    aviso no esta, que es lo normal cuando no hay ninguna pendiente.
    """
    m = re.search(r'<div class="banner warn".*?</div>\s*</div>', html, re.S)
    return m.group(0) if m else ""


def ids_de_guia(html: str, contiene: str = "") -> list[int]:
    """Los ids de las entradas de la guia que se ven en la pagina."""
    filas = re.findall(r'/guia\?editar=(\d+)', html)
    vistos, orden = set(), []
    for i in filas:
        if i not in vistos:
            vistos.add(i)
            orden.append(int(i))
    if not contiene:
        return orden
    # Se recorta el HTML por filas para poder filtrar por texto.
    resultado = []
    for trozo in filas_de_tabla(html):
        if contiene in trozo:
            m = re.search(r'/guia\?editar=(\d+)', trozo)
            if m:
                resultado.append(int(m.group(1)))
    return resultado


def limpiar(admin: Cliente) -> int:
    """Borra lo que dejo una ejecucion anterior.

    Sin esto la segunda pasada falla con el codigo perfectamente correcto,
    porque los recuentos y el orden ya no son los mismos. Es la leccion que ya
    costo una vez en la bateria de la Fase B.
    """
    borradas = 0
    r = admin.get("/guia")
    for faq_id in ids_de_guia(r.text, MARCA):
        admin.post(f"/guia/{faq_id}/eliminar")
        borradas += 1
    return borradas


def main() -> int:
    print(f"\nBloque C · Administracion de guia y notas · {BASE}")

    admin = entrar_admin()
    retiradas = limpiar(admin)
    if retiradas:
        print(f"  (se han borrado {retiradas} entradas de una ejecucion anterior)")

    operador = entrar_operador()
    r = admin.get("/facturas")
    folios = re.findall(r">(RES-\d+)<", r.text)
    assert folios, "no hay ninguna factura en esta base"
    folio = folios[0]
    print(f"  (base de prueba: folio {folio})\n")

    # --- 1 · las pantallas existen y son de Admin ---------------------------
    print("1 · Las dos pantallas nuevas")

    r = admin.get("/guia")
    check("/guia responde", r.status_code == 200 and "Guía del Call Center" in r.text)
    r = admin.get("/notas")
    check("/notas responde", r.status_code == 200 and "Notas del Call Center" in r.text)

    r = admin.get("/")
    check(
        "las dos aparecen en el menu",
        'href="/guia"' in r.text and 'href="/notas"' in r.text,
    )

    # Lo importante: siguen siendo de Admin.
    for ruta in ["/guia", "/notas", "/notas?pendientes=1"]:
        r = operador.get(ruta)
        check(f"el Operador no puede abrir {ruta}", r.url.rstrip("/").endswith("/operador"), r.url)
    for ruta, datos in [
        ("/guia/guardar", {"category": "X", "question": "Y", "answer": "Z", "active": "1"}),
        ("/guia/1/publicar", {}),
        ("/guia/1/mover", {"arriba": "1"}),
        ("/guia/1/eliminar", {}),
        ("/notas/1/atendida", {"atendida": "1"}),
    ]:
        r = operador.post(ruta, data=datos)
        check(f"el Operador no puede POST {ruta}", r.url.rstrip("/").endswith("/operador"))

    # --- 2 · crear y publicar -----------------------------------------------
    print("\n2 · Anadir entradas a la guia")

    pregunta_ok = f"{MARCA} ¿Se puede pagar en dos partes?"
    respuesta_ok = "Sí, se puede fraccionar el pago de la reserva. Consulta el detalle en tu pre-factura."
    r = admin.post("/guia/guardar", data={
        "category": f"{MARCA} Pagos", "question": pregunta_ok,
        "answer": respuesta_ok, "active": "1",
    })
    check("se crea publicada", "Entrada añadida" in r.text)
    check("aparece en la lista de la guia", pregunta_ok in r.text)

    r = operador.get(f"/operador?folio={folio}&v=name,folio&c=1&paso=4")
    check("el Operador la ve inmediatamente, sin desplegar nada", respuesta_ok in r.text)

    # Borrar de verdad: una entrada de la guia no documenta nada, al contrario
    # que una nota o una factura.
    r = admin.post("/guia/guardar", data={
        "category": f"{MARCA} Temporal", "question": f"{MARCA} entrada para borrar",
        "answer": "texto cualquiera", "active": "1",
    })
    id_borrar = None
    for trozo in filas_de_tabla(r.text):
        if f"{MARCA} entrada para borrar" in trozo:
            m = re.search(r'/guia\?editar=(\d+)', trozo)
            if m:
                id_borrar = int(m.group(1))
    check("la entrada temporal existe", id_borrar is not None, str(id_borrar))
    if id_borrar:
        r = operador.get(f"/operador?folio={folio}&v=name,folio&c=1&paso=4")
        check("el Operador la ve antes de borrarla", "texto cualquiera" in r.text)
        r = admin.post(f"/guia/{id_borrar}/eliminar")
        check("se borra", "Entrada eliminada" in r.text)
        check("y desaparece de la lista", f"{MARCA} entrada para borrar" not in r.text)
        r = operador.get(f"/operador?folio={folio}&v=name,folio&c=1&paso=4")
        check("el Operador deja de verla", "texto cualquiera" not in r.text)
        r = admin.post(f"/guia/{id_borrar}/eliminar")
        check("borrarla dos veces no rompe", "ya no existe" in r.text)

    # --- 3 · la regla: sin respuesta no se publica --------------------------
    print("\n3 · Una pregunta sin respuesta NO se publica")

    pregunta_sin = f"{MARCA} ¿Hay descuento por pago al contado?"
    r = admin.post("/guia/guardar", data={
        "category": f"{MARCA} Pagos", "question": pregunta_sin, "answer": "", "active": "1",
    })
    check("crear activa sin respuesta se rechaza con motivo",
          "No se puede activar una pregunta sin respuesta" in r.text)
    check("y no se ha creado", pregunta_sin not in r.text)

    r = admin.post("/guia/guardar", data={
        "category": f"{MARCA} Pagos", "question": pregunta_sin, "answer": "", "active": "",
    })
    check("guardarla desactivada si se puede", "Entrada añadida" in r.text)

    ids = ids_de_guia(r.text, MARCA)
    r = admin.get("/guia")
    id_sin = None
    for trozo in filas_de_tabla(r.text):
        if pregunta_sin in trozo:
            m = re.search(r'/guia\?editar=(\d+)', trozo)
            if m:
                id_sin = int(m.group(1))
    check("la entrada sin respuesta existe y esta identificada", id_sin is not None, str(id_sin))

    if id_sin:
        r = admin.post(f"/guia/{id_sin}/publicar")
        check("el boton Publicar tambien la rechaza",
              "no tiene respuesta aprobada" in r.text)

    r = operador.get(f"/operador?folio={folio}&v=name,folio&c=1&paso=4")
    check(
        "el Operador NO la ve como respuesta",
        pregunta_sin not in respuestas_de_la_guia(r.text),
    )
    check("pero si la ve listada como pendiente", pregunta_sin in r.text)

    # --- 4 · editar, retirar y volver a publicar ----------------------------
    print("\n4 · Editar, retirar y volver a publicar")

    id_ok = None
    r = admin.get("/guia")
    for trozo in filas_de_tabla(r.text):
        if pregunta_ok in trozo:
            m = re.search(r'/guia\?editar=(\d+)', trozo)
            if m:
                id_ok = int(m.group(1))
    check("la entrada publicada esta identificada", id_ok is not None, str(id_ok))

    if id_ok:
        r = admin.get(f"/guia?editar={id_ok}")
        check("el formulario se abre con sus datos cargados", respuesta_ok in r.text)

        nueva = "Sí, se puede fraccionar. Lo confirmamos contigo antes de emitir el documento."
        r = admin.post("/guia/guardar", data={
            "faq_id": id_ok, "category": f"{MARCA} Pagos",
            "question": pregunta_ok, "answer": nueva, "active": "1",
        })
        check("se guarda la edicion", "Entrada actualizada" in r.text)

        r = operador.get(f"/operador?folio={folio}&v=name,folio&c=1&paso=4")
        check("el Operador ve el texto nuevo", nueva in r.text)
        check("y ya no ve el viejo", respuesta_ok not in r.text)

        r = admin.post(f"/guia/{id_ok}/publicar")
        check("se retira de la guia", "deja de verla" in r.text)
        r = operador.get(f"/operador?folio={folio}&v=name,folio&c=1&paso=4")
        check("el Operador deja de ver la respuesta", nueva not in r.text)
        # Mirar solo la RESPUESTA dejaba pasar el fallo entero: la respuesta se
        # iba, pero la PREGUNTA reaparecia en el panel de "recogidas sin
        # respuesta aprobada", que ademas es falso para una entrada retirada.
        # Retirar tiene que retirar las dos cosas.
        check("y tampoco ve la pregunta por ningun lado",
              pregunta_ok not in plano(r.text))
        check("no aparece en el panel de pendientes sin respuesta",
              pregunta_ok not in plano(bloque_pendientes(r.text)))

        r = admin.post(f"/guia/{id_ok}/publicar")
        check("se vuelve a publicar", "ya la ve" in r.text)
        r = operador.get(f"/operador?folio={folio}&v=name,folio&c=1&paso=4")
        check("el Operador vuelve a verla", nueva in r.text)
        check("y vuelve a ver la pregunta", pregunta_ok in plano(r.text))

    # --- 5 · orden -----------------------------------------------------------
    print("\n5 · El orden de la guia")

    r = admin.get("/guia")
    antes = ids_de_guia(r.text)
    check("hay al menos dos entradas para poder ordenar", len(antes) >= 2, f"{len(antes)}")

    if len(antes) >= 2:
        segundo = antes[1]
        r = admin.post(f"/guia/{segundo}/mover", data={"arriba": "1"})
        despues = ids_de_guia(r.text)
        check("subir una entrada la mueve una posicion",
              despues[0] == segundo, f"{antes[:3]} -> {despues[:3]}")

        r = admin.post(f"/guia/{segundo}/mover", data={"arriba": "0"})
        final = ids_de_guia(r.text)
        check("bajarla la devuelve a su sitio", final[:3] == antes[:3], f"{final[:3]}")

        primero = final[0]
        r = admin.post(f"/guia/{primero}/mover", data={"arriba": "1"})
        check("subir la primera no rompe nada", r.status_code == 200)
        check("y el orden no cambia", ids_de_guia(r.text)[:3] == final[:3])

        # El Operador tiene que ver ese mismo orden.
        r = operador.get(f"/operador?folio={folio}&v=name,folio&c=1&paso=4")
        preguntas_op = re.findall(r'class="faqQ"><span>([^<]+)</span>', r.text)
        r2 = admin.get("/guia")
        publicadas = []
        for trozo in filas_de_tabla(r2.text):
            if "Publicada" in trozo:
                m = re.search(r"<b>([^<]+)</b>", trozo)
                if m:
                    publicadas.append(m.group(1))
        check("el Operador ve la guia en el mismo orden que el Admin",
              preguntas_op == publicadas,
              f"{len(preguntas_op)} vs {len(publicadas)}")

    # --- 6 · sugerencias del Operador ---------------------------------------
    print("\n6 · De sugerencia del Operador a entrada de la guia")

    sugerencia = f"{MARCA} El cliente pregunta si puede cambiar el color del coche"
    operador.post("/operador/notas", data={
        "folio": folio, "tipo": "faq", "nota": sugerencia, "paso": 6,
    })
    r = admin.get("/guia")
    check("la sugerencia aparece en la guia como pendiente de revisar", sugerencia in r.text)
    r = admin.get("/notas?pendientes=1")
    check("y en el filtro de sugerencias pendientes", sugerencia in r.text)

    m = re.search(r'/notas/(\d+)/atendida', r.text)
    id_nota = int(m.group(1)) if m else None
    check("la sugerencia esta identificada", id_nota is not None, str(id_nota))

    if id_nota:
        preg_final = f"{MARCA} ¿Se puede cambiar el color del vehículo?"
        resp_final = "El vehículo se entrega tal y como aparece en tu pre-factura."
        r = admin.post("/guia/guardar", data={
            "desde_nota": id_nota, "category": f"{MARCA} Vehículo",
            "question": preg_final, "answer": resp_final, "active": "1",
        })
        check("se convierte en entrada de la guia", "Sugerencia convertida" in r.text)
        check("la sugerencia deja de estar pendiente", sugerencia not in r.text)

        r = operador.get(f"/operador?folio={folio}&v=name,folio&c=1&paso=4")
        check("el Operador ya ve la respuesta nueva", resp_final in r.text)
        # En la guia, no en la pagina entera: el panel de notas se pinta en
        # todos los pasos y ahi la sugerencia SI tiene que verse.
        check(
            "y NO ve el texto original de la sugerencia como respuesta",
            sugerencia not in respuestas_de_la_guia(r.text),
        )

        r = admin.get("/notas")
        check("la nota original sigue guardada, sin cambiar", sugerencia in r.text)
        check("marcada como atendida", "Sugerencia atendida" in r.text)

        r = admin.post(f"/notas/{id_nota}/atendida", data={"atendida": "0"})
        check("se puede devolver a pendientes", "devuelta a pendientes" in r.text)
        r = admin.post(f"/notas/{id_nota}/atendida", data={"atendida": "1"})
        check("y volver a marcarla", "marcada como atendida" in r.text)

    # --- 7 · notas desde Administracion -------------------------------------
    print("\n7 · Ver las notas desde Administracion")

    nota_cliente = f"{MARCA} nota de observacion del cliente"
    operador.post("/operador/notas", data={
        "folio": folio, "tipo": "cliente", "nota": nota_cliente, "paso": 6,
    })

    r = admin.get("/notas")
    check("la nota se ve en la vista de todas", nota_cliente in r.text)
    check("con su folio", folio in r.text)

    r = admin.get("/notas?tipo=cliente")
    check("el filtro por tipo funciona", nota_cliente in r.text)
    r = admin.get("/notas?tipo=seguimiento")
    check("y excluye lo que no es de ese tipo", nota_cliente not in r.text)
    r = admin.get("/notas?tipo=inventado")
    check("un tipo inventado no rompe la pantalla", r.status_code == 200)

    # Desde la propia factura.
    r = admin.get(f"/facturas?q={folio}")
    m = re.search(r'/facturas/(\d+)/editar', r.text)
    id_factura = int(m.group(1)) if m else None
    check("la factura esta identificada", id_factura is not None, str(id_factura))
    check("el listado avisa de que esa factura tiene notas", "nota" in r.text.lower())

    if id_factura:
        r = admin.get(f"/facturas/{id_factura}/editar")
        check("las notas se ven desde la propia factura", nota_cliente in r.text)
        check("con el titulo de la seccion", "Notas del Call Center" in r.text)

    # --- 8 · las notas no se pueden alterar ---------------------------------
    print("\n8 · Las notas no se editan ni se borran")

    for ruta in [f"/notas/{id_nota or 1}/editar", f"/notas/{id_nota or 1}/eliminar",
                 f"/notas/{id_nota or 1}/borrar"]:
        r = admin.post(ruta, data={"nota": "reescrita"})
        check(f"POST {ruta} no existe", r.status_code in (404, 405), str(r.status_code))

    r = admin.get("/notas")
    check("la nota sigue diciendo lo mismo", nota_cliente in r.text)

    # --- 9 · registro de actividad -------------------------------------------
    print("\n9 · Queda registrado")

    # Desde el Hito A, Actividad va detras de la Master Password: hay que
    # abrirla aunque la sesion de Admin ya este dentro del panel.
    admin.post(
        "/configuracion/desbloquear",
        data={"master_password": MASTER, "destino": "/actividad"},
    )
    r = admin.get("/actividad")
    for etiqueta in ["Entrada añadida a la guía", "Entrada de la guía modificada",
                     "Entrada de la guía eliminada", "Nota revisada"]:
        check(f"queda en Actividad: {etiqueta}", etiqueta in r.text)

    print("\n" + "=" * 62)
    print(f"{_ok} comprobaciones correctas, {len(_fallos)} fallos")
    if _fallos:
        for f in _fallos:
            print(f"  FALLO: {f}")
        return 1
    print("Bloque C verificado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
