"""
Comprobacion de los tres puntos de cierre del Call Center.

    1. Nombre visible del Operador, configurable e INDEPENDIENTE del usuario de
       acceso.
    2. Logotipo propio del Call Center: subir, reemplazar y quitar, sin tocar el
       logotipo de la factura ni el de ninguna marca.
    3. Los campos del Operador (input / select / textarea) legibles en Claro,
       Suave y Noche.

    python verificar_cierre_callcenter.py http://127.0.0.1:PUERTO

La base tiene que ser una COPIA: el guion cambia ajustes, sube archivos y
escribe una nota. Lo deja todo como estaba al terminar, y ademas empieza
reponiendo el estado por si una pasada anterior se corto por la mitad, de modo
que se puede ejecutar dos veces seguidas.

El punto 3 se comprueba aqui sobre la hoja de estilos y sobre el HTML servido.
Lo que ve el ojo -que los tres temas se distinguen y que el desplegable abierto
es legible- va aparte, con navegador y capturas: `repaso_cierre.py`.

Solo stdlib.
"""
import http.cookiejar
import io
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8731").rstrip("/")

ADMIN_USER, ADMIN_PASS = "admin", "DulceAuto2026"
MASTER = "Master2026"
OPERADOR_USER, OPERADOR_PASS = "operador", "Operador2026"

NOMBRE_PRUEBA = "María López"
NOMBRE_SEGUNDO = "Juan Ramírez"

# El folio NO se escribe a mano: se toma del propio panel al arrancar. Un folio
# fijo en el guion solo existe en la base donde se escribio, y en cualquier otra
# copia el Operador devuelve "no encontrada": la pantalla de notas no llega a
# pintarse y las comprobaciones sobre ella pasarian sin haber mirado nada.
FOLIO = ""

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
    return " ".join(html.split())


class Respuesta:
    def __init__(self, url: str, cuerpo: bytes, status_code: int, headers=None):
        self.url = url
        self.raw = cuerpo
        self.text = plano(cuerpo.decode("utf-8", "replace"))
        self.status_code = status_code
        self.headers = headers or {}


class Cliente:
    def __init__(self, base: str):
        self.base = base
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )

    def _abrir(self, url: str, datos: bytes | None, tipo: str | None = None) -> Respuesta:
        metodo = "POST" if datos is not None else "GET"
        peticion = urllib.request.Request(url, data=datos, method=metodo)
        if datos is not None:
            peticion.add_header(
                "Content-Type", tipo or "application/x-www-form-urlencoded"
            )
        try:
            with self.opener.open(peticion, timeout=60) as r:
                return Respuesta(r.geturl(), r.read(), r.status, dict(r.headers))
        except urllib.error.HTTPError as e:
            return Respuesta(url, e.read(), e.code, dict(e.headers))

    def get(self, ruta: str) -> Respuesta:
        return self._abrir(self.base + ruta, None)

    def post(self, ruta: str, data: dict | None = None) -> Respuesta:
        return self._abrir(self.base + ruta, urllib.parse.urlencode(data or {}).encode())

    def subir(self, ruta: str, campo: str, nombre: str, datos: bytes) -> Respuesta:
        """POST multipart, que es como viaja un archivo desde el formulario."""
        borde = "----" + uuid.uuid4().hex
        cuerpo = io.BytesIO()
        cuerpo.write(f"--{borde}\r\n".encode())
        cuerpo.write(
            f'Content-Disposition: form-data; name="{campo}"; filename="{nombre}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n".encode()
        )
        cuerpo.write(datos)
        cuerpo.write(f"\r\n--{borde}--\r\n".encode())
        return self._abrir(
            self.base + ruta, cuerpo.getvalue(), f"multipart/form-data; boundary={borde}"
        )


def entrar_admin() -> Cliente:
    c = Cliente(BASE)
    r = c.post("/acceso", data={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert "/acceso" not in r.url, "no se ha podido entrar como Admin"
    r = c.post("/configuracion/desbloquear", data={"master_password": MASTER})
    assert "Configuración maestra" in r.text, "no se ha podido abrir Configuracion"
    return c


def entrar_operador() -> Cliente:
    c = Cliente(BASE)
    r = c.post("/operador/acceso", data={"username": OPERADOR_USER, "password": OPERADOR_PASS})
    assert "/operador/acceso" not in r.url, "no se ha podido entrar como Operador"
    return c


def poner_nombre(admin: Cliente, valor: str) -> Respuesta:
    return admin.post(
        "/configuracion/guardar", data={"ajuste:callcenter.operator_name": valor}
    )


def nombre_guardado(admin: Cliente) -> str:
    """Lo que hay REALMENTE en el ajuste, leido de su casilla en Configuracion."""
    html = admin.get("/configuracion").text
    m = re.search(r'name="ajuste:callcenter\.operator_name" value="([^"]*)"', html)
    return m.group(1) if m else "<no está la casilla>"


def cabecera(html: str) -> str:
    """Solo la barra superior del Call Center.

    Buscar el nombre en la pagina entera no serviria: tambien sale en el campo
    del formulario de notas, y una comprobacion sobre la pagina completa daria
    por buena la cabecera aunque no se hubiera tocado.
    """
    m = re.search(r"<header class=\"topbar\">.*?</header>", html, re.S)
    return m.group(0) if m else ""


def formulario_de_notas(html: str) -> str:
    m = re.search(r'<form method="post" action="/operador/notas">.*?</form>', html, re.S)
    return m.group(0) if m else ""


def buscar_folio(op: Cliente, admin: Cliente) -> str:
    """Un folio real de ESTA base, que ademas llegue al paso 4 del guion.

    Se prueba de verdad en lugar de suponerlo: el paso 4 exige dos datos
    verificados, y una factura sin nombre de cliente no da mas que uno.
    """
    for folio in re.findall(r"RES-\d+", admin.get("/facturas").text):
        r = op.get(f"/operador?folio={folio}&paso=4&v=name,folio&c=1")
        if "notesPanel" in r.text and "Tipo de nota" in r.text:
            return folio
    raise SystemExit("no se ha encontrado ninguna factura que llegue al paso 4")


def guia_paso4(op: Cliente, folio: str = "") -> Respuesta:
    """El paso 4 del guion, que es donde se pinta la guia y el panel de notas.

    Pedir ?paso=4 a secas no basta: el servidor no deja saltar sin dos datos
    verificados y la reserva confirmada, y devolveria el paso 1. Ahi cualquier
    comprobacion sobre el panel de notas saldria correcta sin haber mirado nada.
    """
    return op.get(f"/operador?folio={folio or FOLIO}&paso=4&v=name,folio&c=1")


# Un PNG de 2x2 de verdad, generado byte a byte: el modulo de subidas ABRE la
# imagen para validarla, asi que unos bytes cualesquiera con extension .png no
# pasarian, y con razon.
def png(color: tuple[int, int, int]) -> bytes:
    import struct
    import zlib

    ancho = alto = 2
    fila = bytes([0]) + bytes(color) * ancho
    crudo = fila * alto

    def trozo(tipo: bytes, datos: bytes) -> bytes:
        return (
            struct.pack(">I", len(datos))
            + tipo
            + datos
            + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + trozo(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, 2, 0, 0, 0))
        + trozo(b"IDAT", zlib.compress(crudo))
        + trozo(b"IEND", b"")
    )


ROJO, VERDE = png((220, 20, 20)), png((20, 200, 20))


def main() -> int:
    global FOLIO
    admin = entrar_admin()
    op = entrar_operador()
    FOLIO = buscar_folio(op, admin)
    print(f"  (se trabaja sobre el folio {FOLIO})")

    # --- estado de partida, y se repone por si una pasada anterior se corto ---
    poner_nombre(admin, "")
    admin.post("/configuracion/callcenter/logo/quitar")

    logo_factura_antes = admin.get("/configuracion/logo.img")
    marcas_antes = admin.get("/marcas").text

    print("\n=== 1. Nombre visible del Operador ===")

    # Control de presencia: ANTES de tocar nada, la cabecera dice el nombre de
    # la cuenta. Sin esto, el "ahora dice Maria Lopez" de abajo no probaria que
    # ha cambiado algo.
    cab = cabecera(op.get("/operador").text)
    check("de partida, la cabecera dice el nombre de la cuenta",
          "Operador: <b>operador</b>" in cab, cab[-90:])

    r = poner_nombre(admin, NOMBRE_PRUEBA)
    check("Configuración acepta el nombre visible", "1 ajuste actualizado" in r.text)
    check("y queda guardado", nombre_guardado(admin) == NOMBRE_PRUEBA,
          nombre_guardado(admin))

    cab = cabecera(op.get("/operador").text)
    check("la cabecera del Operador pasa a decirlo",
          f"Operador: <b>{NOMBRE_PRUEBA}</b>" in cab)
    check("y ya no dice el nombre de la cuenta",
          "Operador: <b>operador</b>" not in cab)

    # Lo que pidio el cliente: que el nombre visible NO sea el usuario de acceso.
    check("el usuario de acceso NO ha cambiado en Configuración",
          f'name="username" value="{OPERADOR_USER}"' in admin.get("/configuracion").text)
    otro = Cliente(BASE)
    r = otro.post("/operador/acceso",
                  data={"username": OPERADOR_USER, "password": OPERADOR_PASS})
    check("se sigue entrando con el usuario de siempre", "/operador/acceso" not in r.url)
    otro2 = Cliente(BASE)
    r = otro2.post("/operador/acceso",
                   data={"username": NOMBRE_PRUEBA, "password": OPERADOR_PASS})
    check("y NO se entra usando el nombre visible como usuario",
          "/operador/acceso" in r.url)

    r = guia_paso4(op)
    formulario = formulario_de_notas(r.text)
    # Control de presencia: si el formulario no se hubiera pintado, la
    # comprobacion de abajo fallaria por el motivo equivocado -o peor, una del
    # tipo "ya no aparece" pasaria sin haber mirado nada.
    check("el panel de notas del paso 4 se pinta", bool(formulario),
          f"{len(formulario)} caracteres")
    check("el formulario de notas enseña el mismo nombre",
          f'value="{NOMBRE_PRUEBA}" readonly' in formulario)

    # --- la nota se firma con el nombre que hubiera al escribirla -------------
    texto = f"Comprobación del nombre visible {uuid.uuid4().hex[:8]}"
    op.post("/operador/notas", data={"folio": FOLIO, "tipo": "cliente", "nota": texto,
                                     "paso": 6})
    notas = admin.get("/notas").text
    trozo = notas.split(texto, 1)[1][:400] if texto in notas else ""
    check("la nota nueva queda firmada con el nombre visible",
          bool(trozo) and NOMBRE_PRUEBA in trozo, trozo[:120])

    # Y cambiarlo despues NO reescribe la que ya estaba: es la misma regla que
    # con los datos bancarios de una factura ya emitida.
    poner_nombre(admin, NOMBRE_SEGUNDO)
    notas = admin.get("/notas").text
    trozo = notas.split(texto, 1)[1][:400] if texto in notas else ""
    check("cambiar el nombre NO reescribe la nota ya guardada",
          bool(trozo) and NOMBRE_PRUEBA in trozo and NOMBRE_SEGUNDO not in trozo)
    check("pero la cabecera sí pasa al nombre nuevo",
          f"Operador: <b>{NOMBRE_SEGUNDO}</b>" in cabecera(op.get("/operador").text))

    # --- el Admin que entra a mirar el modulo sigue siendo el Admin -----------
    cab = cabecera(admin.get("/operador").text)
    check("el Admin viendo el módulo se ve a sí mismo, no al Operador",
          "Operador: <b>admin</b>" in cab and NOMBRE_SEGUNDO not in cab, cab[-90:])

    # --- limites y vuelta atras ----------------------------------------------
    r = poner_nombre(admin, "L" * 61)
    check("un nombre de más de 60 caracteres se rechaza",
          "no puede pasar de 60" in r.text)
    check("y el que había NO se ha tocado", nombre_guardado(admin) == NOMBRE_SEGUNDO,
          nombre_guardado(admin))

    r = poner_nombre(admin, "")
    check("dejarlo en blanco se acepta", "1 ajuste actualizado" in r.text)
    check("y la cabecera vuelve al nombre de la cuenta",
          "Operador: <b>operador</b>" in cabecera(op.get("/operador").text))

    print("\n=== 2. Logotipo del Call Center ===")

    # Presencia otra vez: sin logotipo se ve el distintivo "DA" de siempre.
    cab = cabecera(op.get("/operador").text)
    check("sin logotipo propio, la cabecera lleva la marca DulceAuto",
          '<div class="brandMark">DA</div>' in cab and "brandLogo" not in cab)
    check("y la imagen del Call Center no existe todavía",
          op.get("/operador/logo.img").status_code == 404)

    r = admin.subir("/configuracion/callcenter/logo", "logo", "marca.png", ROJO)
    check("se sube el logotipo del Call Center", "Logotipo del Call Center actualizado" in r.text)

    cab = cabecera(op.get("/operador").text)
    check("la cabecera pasa a pintar el logotipo",
          'class="brandLogo" src="/operador/logo.img"' in cab)
    check("y deja de pintar el distintivo DA", '<div class="brandMark">DA</div>' not in cab)

    servido = op.get("/operador/logo.img")
    check("el Operador puede descargar la imagen", servido.status_code == 200)
    check("y es exactamente el archivo que se subió", servido.raw == ROJO,
          f"{len(servido.raw)} bytes")

    r = admin.subir("/configuracion/callcenter/logo", "logo", "marca2.png", VERDE)
    check("se reemplaza por otro", "Logotipo del Call Center actualizado" in r.text)
    check("y lo que se sirve es el nuevo, no el viejo",
          op.get("/operador/logo.img").raw == VERDE)

    r = admin.subir("/configuracion/callcenter/logo", "logo", "trampa.png", b"esto no es una imagen")
    check("un archivo que no es una imagen se rechaza",
          "no es una imagen" in r.text)
    check("y el logotipo bueno sigue puesto", op.get("/operador/logo.img").raw == VERDE)

    # --- lo que NO debe tocar -------------------------------------------------
    logo_factura_ahora = admin.get("/configuracion/logo.img")
    check("el logotipo de la FACTURA no se ha tocado",
          logo_factura_ahora.raw == logo_factura_antes.raw
          and logo_factura_ahora.status_code == logo_factura_antes.status_code,
          f"{logo_factura_ahora.status_code}, {len(logo_factura_ahora.raw)} bytes")
    check("los logotipos de las MARCAS no se han tocado",
          admin.get("/marcas").text == marcas_antes)

    # --- la puerta ------------------------------------------------------------
    sin_sesion = Cliente(BASE)
    r = sin_sesion.get("/operador/logo.img")
    check("sin sesión no se descarga el logotipo",
          r.raw != VERDE and "/operador/acceso" in r.url, r.url)
    r = op.post("/configuracion/callcenter/logo/quitar")
    check("el Operador NO puede quitar el logotipo",
          op.get("/operador/logo.img").raw == VERDE, r.url)

    r = admin.post("/configuracion/callcenter/logo/quitar")
    check("Administración lo quita", "vuelve a la marca DulceAuto" in r.text)
    check("la cabecera recupera el distintivo DA",
          '<div class="brandMark">DA</div>' in cabecera(op.get("/operador").text))
    check("y la imagen vuelve a no existir",
          op.get("/operador/logo.img").status_code == 404)

    print("\n=== 3. Campos legibles en los tres temas ===")

    css = Cliente(BASE).get("/static/css/operador.css").text
    check("el módulo de Operador declara el esquema oscuro en Noche",
          "body.theme-night{color-scheme:dark}" in plano(css))
    check("y fija a mano el color de las opciones del desplegable",
          "body.theme-night option" in css and "#102033" in css)
    check("la tarjeta de acceso se queda en esquema claro",
          ".loginCard{color-scheme:light}" in plano(css))
    check("el buscador de la barra superior también",
          ".search input{color-scheme:light}" in plano(css))

    css_panel = Cliente(BASE).get("/static/css/backend.css").text
    check("el panel de Administración lleva el mismo arreglo",
          "body.theme-night { color-scheme: dark; }" in css_panel)
    check("y la factura de la vista previa se queda clara",
          "color-scheme: light" in css_panel and ".invoice-mini" in css_panel)

    # El desplegable que reporto el cliente sigue estando dentro de .field, que
    # es la regla que le da fondo y color de tema. Si alguien lo sacara de ahi,
    # el arreglo de arriba dejaria de alcanzarle sin que nada mas se rompiera.
    html = guia_paso4(op).text
    m = re.search(r'<div class="field"> <label>Tipo de nota</label> <select name="tipo">', html)
    check("el desplegable «Tipo de nota» sigue dentro de un .field", bool(m))

    for tema, clase in (("light", ""), ("soft", "theme-soft"), ("night", "theme-night")):
        c = Cliente(BASE)
        c.post("/operador/acceso", data={"username": OPERADOR_USER, "password": OPERADOR_PASS})
        c.cookies.set_cookie(
            http.cookiejar.Cookie(
                0, "da_theme", tema, None, False, "127.0.0.1", False, False,
                "/", True, False, None, True, None, None, {},
            )
        )
        cuerpo = c.get("/operador").text
        esperado = f'<body class="{clase}">'
        check(f"el tema {tema} llega pintado desde el servidor", esperado in cuerpo)

    print("\n" + "=" * 62)
    print(f"{_ok} comprobaciones correctas, {len(_fallos)} fallos")
    for f in _fallos:
        print(f"  - {f}")
    return 1 if _fallos else 0


if __name__ == "__main__":
    sys.exit(main())
