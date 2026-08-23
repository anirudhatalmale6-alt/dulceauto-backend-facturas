"""
Comprobacion del Bloque A (Tuneup Backend/PDF V1.1).

Sigue literalmente la lista de 13 puntos que pidio revisar el cliente, en su
orden y con sus palabras. Cada punto imprime OK o FALLO con el detalle, y al
final sale el recuento.

Se ejecuta contra el panel de verdad, por HTTP y con sesion iniciada, no
llamando a las funciones por dentro: lo que se quiere comprobar es lo que le va
a pasar al usuario cuando pulse los botones.

    python verificar_bloque_a.py http://127.0.0.1:PUERTO

La base tiene que ser una COPIA. El guion crea, archiva y elimina facturas.
"""
import http.cookiejar
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
USUARIO = "admin"
PASSWORD = "DulceAuto2026"
MASTER = "Master2026"

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


class Respuesta:
    """Lo minimo que hace falta de una respuesta: url final, texto y codigo."""

    def __init__(self, url: str, text: str, status_code: int):
        self.url = url
        self.text = text
        self.status_code = status_code


class Panel:
    """Cliente HTTP con cookies. Solo stdlib, para no anadir dependencias."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )

    def _abrir(self, url: str, datos: bytes | None) -> Respuesta:
        # "datos is not None" y no "if datos": un POST sin campos llega aqui
        # como b"", que es falso, y se enviaria como GET.
        metodo = "POST" if datos is not None else "GET"
        peticion = urllib.request.Request(url, data=datos, method=metodo)
        if datos is not None:
            peticion.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with self.opener.open(peticion, timeout=120) as r:
                return Respuesta(r.geturl(), r.read().decode("utf-8", "replace"), r.status)
        except urllib.error.HTTPError as e:
            return Respuesta(url, e.read().decode("utf-8", "replace"), e.code)

    def get(self, ruta: str) -> Respuesta:
        return self._abrir(self.base + ruta, None)

    def post(self, ruta: str, data: dict | None = None) -> Respuesta:
        cuerpo = urllib.parse.urlencode(data or {}, encoding="utf-8").encode()
        return self._abrir(self.base + ruta, cuerpo)


def entrar() -> Panel:
    c = Panel(BASE)
    r = c.post("/acceso", data={"username": USUARIO, "password": PASSWORD})
    assert "/acceso" not in r.url, "no se ha podido entrar en el panel"
    return c


def desbloquear(c: Panel) -> None:
    c.post("/configuracion/desbloquear", data={"master_password": MASTER})


def crear_factura(c: Panel, **extra) -> tuple[str, int]:
    """Crea una factura minima y devuelve (folio, id)."""
    datos = {
        "locale": "es-MX",
        "status": "draft",
        "customer_name": "Prueba Bloque A",
        "vehicle_title": "2020 Prueba",
        "vehicle_vin": "1HGBH41JXMN109186",
        "pricing_vehicle_price": "100000",
        "pricing_reservation_amount": "1000",
        "issue_date": "2026-08-23",
    }
    datos.update(extra)
    r = c.post("/facturas/nueva", data=datos)
    m = re.search(r"/facturas/(\d+)/editar", str(r.url))
    if not m:
        return "", 0
    iid = int(m.group(1))
    folio = re.search(r"data-folio[^>]*value=\"([^\"]+)\"", r.text)
    return (folio.group(1) if folio else ""), iid


def main() -> int:
    print(f"Bloque A · comprobacion sobre {BASE}\n")
    c = entrar()

    # ---- 1 a 4 · perfiles de marca ----------------------------------------
    print("PERFILES DE MARCA")
    r = c.get("/marcas")
    check("marcas · la pantalla existe", r.status_code == 200 and "Marcas" in r.text)

    r = c.post(
        "/marcas/guardar",
        data={"name": "Concesionario B", "doc_title": "Concesionario B — Reserva"},
    )
    check("marcas · añadir", "Concesionario B" in r.text)

    ids = re.findall(r"/marcas/(\d+)/activar", r.text)
    nuevo = max(int(i) for i in ids) if ids else 0

    r = c.post("/marcas/guardar", data={"id": str(nuevo), "name": "Concesionario B2",
                                        "doc_title": "Concesionario B2 — Reserva"})
    check("marcas · editar", "Concesionario B2" in r.text)

    r = c.post(f"/marcas/{nuevo}/activar")
    check("marcas · desactivar", "Desactivada" in r.text)
    r = c.post(f"/marcas/{nuevo}/activar")
    check("marcas · activar", "Concesionario B2" in r.text and "Activa" in r.text)

    # seleccionar marca por factura
    folio, iid = crear_factura(c, brand_profile_id=str(nuevo), status="pending")
    check("marca · se selecciona por factura", bool(iid), f"factura {folio}")

    r = c.get(f"/facturas/{iid}/documento")
    check(
        "marca · nombre y título correctos en el documento",
        "Concesionario B2 — Reserva" in r.text,
        "el <title> sale del perfil",
    )

    # snapshot historico que no cambia al modificar despues el perfil
    r = c.post(f"/facturas/{iid}/pdf")
    doc_antes = c.get(f"/facturas/{iid}/documento").text
    titulo_antes = re.search(r"<title[^>]*>([^<]*)</title>", doc_antes)
    c.post("/marcas/guardar", data={"id": str(nuevo), "name": "Renombrada despues",
                                    "doc_title": "Titulo cambiado despues"})
    # el snapshot en disco tiene que seguir diciendo lo de antes
    from app.config import settings as cfg  # noqa: E402

    snaps = sorted((cfg.snapshots_dir / str(iid)).glob("v*/documento.html"))
    congelado = snaps[-1].read_text(encoding="utf-8") if snaps else ""
    check(
        "marca · el snapshot histórico NO cambia al modificar el perfil",
        "Concesionario B2 — Reserva" in congelado and "Titulo cambiado despues" not in congelado,
        f"{snaps[-1].parent.name if snaps else 'sin snapshot'}",
    )
    del titulo_antes

    # ---- 5 y 6 · PDF ------------------------------------------------------
    print("\nPDF")
    # Se cuentan las paginas con la misma funcion que usa el generador, y no con
    # pdfinfo: asi la comprobacion no depende de tener poppler instalado y se
    # puede ejecutar tambien dentro del contenedor del servidor.
    from app.pdf import contar_paginas  # noqa: E402

    paginas = []
    for loc, nombre in (("es-MX", "MX"), ("en", "EN"), ("es-AR", "AR")):
        f2, id2 = crear_factura(c, locale=loc, brand_profile_id=str(nuevo), status="pending")
        c.post(f"/facturas/{id2}/pdf")
        pdfs = sorted((cfg.snapshots_dir / str(id2)).glob("v*/*.pdf"))
        paginas.append((nombre, contar_paginas(pdfs[-1]) if pdfs else 0))

    check(
        "PDF · borde exterior mejorado en MX / EN / AR",
        Path("templates_html/assets/css/factura.css").read_text(encoding="utf-8").count(
            "border: 1px solid #d5dbe5"
        )
        >= 2,
        "el marco se dibuja sobre .page-shell al imprimir",
    )
    check(
        "PDF · los tres siguen siendo A4 de una sola página",
        all(n == 1 for _, n in paginas),
        ", ".join(f"{k}={v}pág" for k, v in paginas),
    )

    # ---- 7 a 9 · cancelar, archivar, eliminar, folio reservado ------------
    print("\nCANCELADAS Y FOLIOS")
    desbloquear(c)

    # cancelada SIN historico -> se puede eliminar
    f_sin, id_sin = crear_factura(c)
    c.post(f"/facturas/{id_sin}/editar", data={
        "locale": "es-MX", "status": "cancelled", "customer_name": "Prueba Bloque A",
        "vehicle_title": "2020 Prueba", "vehicle_vin": "1HGBH41JXMN109186",
        "pricing_vehicle_price": "100000", "pricing_reservation_amount": "1000",
        "issue_date": "2026-08-23",
    })
    r = c.post(f"/facturas/{id_sin}/eliminar")
    check("Cancelada sin histórico → eliminación permitida", "eliminada" in r.text, f_sin)

    r = c.get(f"/facturas/{id_sin}/editar")
    check("  · y desaparece de verdad", "ya no existe" in r.text or r.status_code == 404)

    # el folio sigue reservado: no se puede volver a poner a mano
    r = c.post("/facturas/nueva", data={
        "locale": "es-MX", "status": "draft", "customer_name": "Reintento",
        "vehicle_title": "2020 Prueba", "vehicle_vin": "1HGBH41JXMN109186",
        "pricing_vehicle_price": "100000", "pricing_reservation_amount": "1000",
        "issue_date": "2026-08-23", "folio_mode": "manual", "folio_manual": f_sin,
    })
    check(
        "Folio utilizado → reservado permanentemente",
        "ya se ha usado" in r.text,
        f"{f_sin} sigue bloqueado tras eliminar su factura",
    )

    # cancelada CON historico -> archivar, no eliminar
    f_con, id_con = crear_factura(c, status="pending")
    c.post(f"/facturas/{id_con}/pdf")
    c.post(f"/facturas/{id_con}/editar", data={
        "locale": "es-MX", "status": "cancelled", "customer_name": "Prueba Bloque A",
        "vehicle_title": "2020 Prueba", "vehicle_vin": "1HGBH41JXMN109186",
        "pricing_vehicle_price": "100000", "pricing_reservation_amount": "1000",
        "issue_date": "2026-08-23",
    })
    r = c.post(f"/facturas/{id_con}/eliminar")
    check(
        "Cancelada con histórico → no se elimina",
        "se archiva" in r.text,
        "el panel lo explica en lugar de destruirla",
    )
    r = c.post(f"/facturas/{id_con}/archivar")
    check("Cancelada con histórico → archivada", "archivada" in r.text, f_con)
    r = c.get("/facturas")
    check("  · y sale del listado normal", f_con not in r.text)
    r = c.get("/facturas?archivadas=1")
    check("  · pero sigue existiendo entera", f_con in r.text)

    # ---- 10 a 13 · folio automatico / manual ------------------------------
    print("\nFOLIO AUTOMÁTICO / MANUAL")
    f_auto, _ = crear_factura(c)
    check("Automático", bool(f_auto), f_auto)

    # El folio manual se calcula a partir del contador de AHORA, no fijo.
    # Con un numero fijo la primera pasada lo ocuparia y la segunda fallaria con
    # el codigo perfectamente bien: el guion tiene que poder repetirse.
    prefijo = re.match(r"([A-Z\-]+)", f_auto).group(1)
    ancho = len(f_auto) - len(prefijo)
    # El salto tiene que caber en los digitos configurados: un folio con mas
    # digitos de la cuenta NO mueve el contador, y es a proposito (ver
    # avanzar_contador_tras_manual). Eso se comprueba aparte, mas abajo.
    numero_manual = int(f_auto[len(prefijo):]) + 100
    manual = f"{prefijo}{numero_manual:0{ancho}d}"
    f_man, id_man = crear_factura(c, folio_mode="manual", folio_manual=manual)
    check("Manual", f_man == manual, f_man)

    r = c.get("/facturas/nueva")
    siguiente = re.search(r"placeholder=\"([A-Z\-]+\d+)\"", r.text)
    esperado = f"{prefijo}{numero_manual + 1:0{ancho}d}"
    check(
        "Contador correcto después de un folio manual superior",
        siguiente is not None and siguiente.group(1) == esperado,
        f"siguiente automático = {siguiente.group(1) if siguiente else '?'}",
    )

    # proteccion Master Password
    c.post("/configuracion/bloquear")
    r = c.post("/facturas/nueva", data={
        "locale": "es-MX", "status": "draft", "customer_name": "Sin master",
        "vehicle_title": "2020 Prueba", "vehicle_vin": "1HGBH41JXMN109186",
        "pricing_vehicle_price": "100000", "pricing_reservation_amount": "1000",
        "issue_date": "2026-08-23", "folio_mode": "manual",
        "folio_manual": f"{prefijo}{numero_manual + 50:0{ancho}d}",
    })
    check(
        "Protección Master Password",
        "Master Password" in r.text and "función administrativa" in r.text,
        "sin desbloquear, el folio manual se rechaza",
    )
    desbloquear(c)

    # colision / concurrencia intacta
    r = c.post("/facturas/nueva", data={
        "locale": "es-MX", "status": "draft", "customer_name": "Duplicado",
        "vehicle_title": "2020 Prueba", "vehicle_vin": "1HGBH41JXMN109186",
        "pricing_vehicle_price": "100000", "pricing_reservation_amount": "1000",
        "issue_date": "2026-08-23", "folio_mode": "manual", "folio_manual": manual,
    })
    check(
        "Protección de colisión/concurrencia intacta",
        "ya se ha usado" in r.text,
        "el folio manual repetido se rechaza en voz alta, no coge otro número",
    )

    # Un folio manual con MAS digitos de los configurados no mueve el contador.
    # Es una decision deliberada: sin poder compararlo con el contador habria
    # que inventarse una regla, y no hace falta, porque el registro de folios ya
    # impide que ese numero se reutilice.
    antes = re.search(r"placeholder=\"([A-Z\-]+\d+)\"", c.get("/facturas/nueva").text).group(1)
    # Tambien derivado del contador, para que la segunda pasada no choque con
    # el folio que dejo creado la primera.
    largo = f"{prefijo}{numero_manual + 9 * 10 ** ancho}"
    f_largo, _ = crear_factura(c, folio_mode="manual", folio_manual=largo)
    despues = re.search(r"placeholder=\"([A-Z\-]+\d+)\"", c.get("/facturas/nueva").text).group(1)
    check(
        "  · un folio manual más largo se acepta pero NO mueve el contador",
        f_largo == largo and antes == despues,
        f"{largo} creado, contador sigue en {despues}",
    )

    # y el automatico sigue sin repetir nunca
    vistos = set()
    for _ in range(5):
        f, _i = crear_factura(c)
        vistos.add(f)
    check("  · el automático nunca repite", len(vistos) == 5, ", ".join(sorted(vistos)))

    print(f"\n{'='*62}")
    print(f"{_ok} comprobaciones OK, {len(_fallos)} fallos")
    for f in _fallos:
        print(f"  - {f}")
    return 1 if _fallos else 0


if __name__ == "__main__":
    sys.exit(main())
