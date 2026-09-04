"""
Comprobacion del Hito B: album por ZIP, verificaciones y pagina 2.

ATENCION: este guion SUSTITUYE el album de una factura y le cambia las
verificaciones. Solo debe ejecutarse contra una COPIA, nunca contra el sistema
en uso.

    python verificar_hito_b.py http://127.0.0.1:8742 /ruta/a/data

Lo que se comprueba, por orden:

  1. el ZIP: limite de 20, contenido real, orden natural, imagenes rotas,
     archivos que no son imagenes, rutas que salen de la carpeta y ZIP bomba;
  2. que sustituir el album lo sustituye ENTERO y no mezcla dos cargas;
  3. las verificaciones: ninguna de serie, solo salen las marcadas, y
     desmarcar desmarca;
  4. la pagina 2 en el documento de verdad: contadores, medidas y la pastilla
     "Foto 09" que ya no esta;
  5. control positivo: las comprobaciones de arriba tienen que ser capaces de
     ponerse en rojo.
"""
import io
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from app import album, verificaciones  # noqa: E402
from app import documents as doc_engine  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8742"
DATA = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(
    os.environ.get("DATA_DIR") or (Path(__file__).parent / "data")
)
DB = DATA / "dulceauto.db"
FOTOS = Path(os.environ.get("ALBUM_FOTOS") or (Path(__file__).parent / "fotos-album"))

USER, PASSWORD = "admin", "DulceAuto2026"

ok, fallos = [], []


def check(nombre, condicion, extra=""):
    (ok if condicion else fallos).append(nombre)
    print(("  OK   " if condicion else "  FALLA") + f" {nombre}" + (f"  [{extra}]" if extra else ""))


# --- material de prueba ------------------------------------------------------


def _jpegs() -> list[bytes]:
    reales = [p.read_bytes() for p in sorted(FOTOS.glob("*.jpg"))] if FOTOS.is_dir() else []
    if reales:
        return reales
    # Sin fotografias reales se generan JPEG de verdad: tienen que pasar por
    # Pillow, asi que no vale un archivo inventado.
    from PIL import Image

    salida = []
    for color in ((180, 60, 60), (60, 120, 180), (80, 160, 90), (150, 120, 60)):
        memoria = io.BytesIO()
        Image.new("RGB", (400, 300), color).save(memoria, "JPEG")
        salida.append(memoria.getvalue())
    return salida


IMAGENES = _jpegs()


def zip_con(entradas: list[tuple[str, bytes]]) -> bytes:
    memoria = io.BytesIO()
    with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, datos in entradas:
            z.writestr(nombre, datos)
    return memoria.getvalue()


def zip_de_fotos(nombres: list[str]) -> bytes:
    return zip_con([(n, IMAGENES[i % len(IMAGENES)]) for i, n in enumerate(nombres)])


def fotos_en_bd(factura_id: int) -> list[tuple[int, str, str]]:
    """(posicion, nombre original, ruta) de las fotografias de una factura."""
    con = sqlite3.connect(DB)
    try:
        return con.execute(
            "SELECT position, original_name, file_path FROM invoice_photo "
            "WHERE invoice_id=? ORDER BY position",
            (factura_id,),
        ).fetchall()
    finally:
        con.close()


def verificaciones_en_bd(factura_id: int):
    con = sqlite3.connect(DB)
    try:
        fila = con.execute(
            "SELECT verifications FROM invoice WHERE id=?", (factura_id,)
        ).fetchone()
        return fila[0] if fila else None
    finally:
        con.close()


def main() -> None:
    if not DB.exists():
        check("la base de datos de pruebas existe", False, str(DB))
        return

    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pag = navegador.new_page(viewport={"width": 1280, "height": 900})
        pag.goto(f"{BASE}/acceso")
        pag.fill('input[name="username"]', USER)
        pag.fill('input[name="password"]', PASSWORD)
        pag.click('button[type="submit"]')

        pag.goto(f"{BASE}/facturas")
        enlace = pag.locator('tbody tr:not(.empty-row) a[href*="/editar"]').first
        fid = int(enlace.get_attribute("href").split("/facturas/")[1].split("/")[0])
        print(f"\nFactura de pruebas: {fid}")

        def subir(datos: bytes, nombre="album.zip"):
            return pag.request.post(
                f"{BASE}/facturas/{fid}/album",
                multipart={
                    "album_zip": {"name": nombre, "mimeType": "application/zip", "buffer": datos}
                },
            )

        def marcar(claves):
            return pag.request.post(
                f"{BASE}/facturas/{fid}/verificaciones",
                headers={"content-type": "application/x-www-form-urlencoded"},
                data="&".join(f"verificacion={c}" for c in claves),
            )

        def editor() -> str:
            pag.goto(f"{BASE}/facturas/{fid}/editar")
            return pag.content()

        # --- 1 · el ZIP -----------------------------------------------------
        print("\n1 · Lo que entra y lo que no entra por el ZIP")

        # Orden natural. Los nombres estan puestos para que el orden alfabetico
        # y el natural NO coincidan: alfabeticamente 'foto10' va antes que
        # 'foto2', y si el orden fuera ese, la posicion 2 seria foto10.
        nombres = [f"foto{i}.jpg" for i in range(1, 13)]
        subir(zip_de_fotos(nombres))
        filas = fotos_en_bd(fid)
        check("entran las 12 del ZIP", len(filas) == 12, str(len(filas)))
        orden = [n for _p, n, _r in filas]
        check(
            "el orden es natural: foto2 va antes que foto10",
            orden == nombres,
            f"posición 2 = {orden[1] if len(orden) > 1 else '?'}",
        )
        check(
            "las posiciones van de 1 a N sin saltos",
            [p for p, _n, _r in filas] == list(range(1, len(filas) + 1)),
        )

        # Tope de 20.
        respuesta = subir(zip_de_fotos([f"f{i:02d}.jpg" for i in range(1, 26)]))
        filas = fotos_en_bd(fid)
        check(
            f"un ZIP de 25 deja el álbum en {album.MAX_FOTOS}",
            len(filas) == album.MAX_FOTOS,
            str(len(filas)),
        )
        # El aviso se busca en el cuerpo que devuelve el POST, no navegando
        # despues al editor: el POST ya sigue el 303 hasta el editor, y al
        # hacerlo se lleva el aviso por delante. Mirarlo despues da vacio
        # siempre, tenga razon el codigo o no.
        check("y avisa de las que ha dejado fuera", "de más" in respuesta.text())

        # Sustitucion completa. Se pasa de 20 a 5: si mezclara, quedarian 20.
        subir(zip_de_fotos([f"nueva{i}.jpg" for i in range(1, 6)]))
        filas = fotos_en_bd(fid)
        check("sustituir el álbum lo sustituye ENTERO", len(filas) == 5, str(len(filas)))
        check(
            "y no queda ninguna de la carga anterior",
            all(n.startswith("nueva") for _p, n, _r in filas),
            str([n for _p, n, _r in filas]),
        )

        # Los archivos viejos no se quedan tirados en el disco.
        rutas_vivas = {r for _p, _n, r in filas}
        carpeta = DATA / "uploads" / "facturas" / str(fid)
        if carpeta.is_dir():
            en_disco = {
                str(f.relative_to(DATA)) for f in carpeta.iterdir() if f.is_file()
            }
            huerfanos = en_disco - rutas_vivas
            check(
                "no quedan archivos huérfanos de la carga anterior",
                not huerfanos,
                f"{len(huerfanos)} sobrantes",
            )

        # Imagen rota: no entra NINGUNA, para no dejar el album a medias.
        antes = fotos_en_bd(fid)
        respuesta = subir(zip_con([("a1.jpg", IMAGENES[0]), ("a2.jpg", b"esto no es un JPEG")]))
        despues = fotos_en_bd(fid)
        check(
            "una imagen rota deja el álbum como estaba",
            [n for _p, n, _r in despues] == [n for _p, n, _r in antes],
            str([n for _p, n, _r in despues][:3]),
        )
        check(
            "y lo dice, nombrando el archivo que falla",
            "no se ha cambiado nada del álbum" in respuesta.text().lower()
            and "a2.jpg" in respuesta.text(),
        )

        # Un archivo que no es imagen se ignora; el resto entra.
        subir(zip_con([("b1.jpg", IMAGENES[0]), ("leeme.txt", b"hola"), ("b2.jpg", IMAGENES[1])]))
        filas = fotos_en_bd(fid)
        check(
            "un .txt dentro del ZIP se ignora y las imágenes entran",
            [n for _p, n, _r in filas] == ["b1.jpg", "b2.jpg"],
            str([n for _p, n, _r in filas]),
        )

        # Ruta que sale de la carpeta.
        antes = fotos_en_bd(fid)
        subir(zip_con([("../../fuera.jpg", IMAGENES[0])]))
        check(
            "una entrada con ../ se rechaza y no cambia nada",
            fotos_en_bd(fid) == antes,
        )
        subir(zip_con([("/etc/passwd.jpg", IMAGENES[0])]))
        check("una ruta absoluta se rechaza y no cambia nada", fotos_en_bd(fid) == antes)

        # ZIP bomba: comprime a poco y descomprime a mucho.
        relleno = b"\0" * (album.MAX_FOTOS and 100 * 1024 * 1024)
        bomba = zip_con([("bomba.jpg", relleno)])
        check(
            "la bomba comprime a poco (si no, la prueba no probaría nada)",
            len(bomba) < 1 * 1024 * 1024,
            f"{len(bomba) / 1024:.0f} KB comprimidos, {len(relleno) / 1024 / 1024:.0f} MB dentro",
        )
        subir(bomba)
        check("un ZIP bomba se rechaza y no cambia nada", fotos_en_bd(fid) == antes)

        # Un ZIP que no es un ZIP.
        respuesta = pag.request.post(
            f"{BASE}/facturas/{fid}/album",
            multipart={
                "album_zip": {"name": "x.zip", "mimeType": "application/zip", "buffer": b"PK-no"}
            },
        )
        check("un archivo que no es un ZIP se rechaza", respuesta.status < 400)
        check("y no cambia nada", fotos_en_bd(fid) == antes)

        # --- 2 · las verificaciones -----------------------------------------
        print("\n2 · Las verificaciones")

        marcar([])
        check(
            "sin marcar nada, no se guarda ninguna",
            not verificaciones.leer(verificaciones_en_bd(fid)),
            repr(verificaciones_en_bd(fid)),
        )

        marcar(["robo", "placas", "historial"])
        guardadas = verificaciones.leer(verificaciones_en_bd(fid))
        check("se guardan las tres marcadas", guardadas == ["robo", "placas", "historial"], str(guardadas))

        marcar(["robo"])
        guardadas = verificaciones.leer(verificaciones_en_bd(fid))
        check(
            "desmarcar quita las que ya no vienen",
            guardadas == ["robo"],
            str(guardadas),
        )

        marcar(["robo", "inventada", "placas"])
        guardadas = verificaciones.leer(verificaciones_en_bd(fid))
        check(
            "una clave que no existe se descarta",
            guardadas == ["robo", "placas"],
            str(guardadas),
        )

        # El orden es el del diseno, no el orden en que llegan.
        marcar(["historial", "robo"])
        guardadas = verificaciones.leer(verificaciones_en_bd(fid))
        check(
            "el orden impreso es el del diseño, no el de marcado",
            guardadas == ["robo", "historial"],
            str(guardadas),
        )

        # --- 3 · la página 2 en el documento --------------------------------
        print("\n3 · La página 2, en el documento que sirve la aplicación")

        casos = ((5, 6), (14, 4), (20, 2), (1, 1))
        for cuantas, cuantas_v in casos:
            subir(zip_de_fotos([f"foto{i}.jpg" for i in range(1, cuantas + 1)]))
            marcar([v.clave for v in verificaciones.VERIFICACIONES[:cuantas_v]])
            pag.goto(f"{BASE}/facturas/{fid}/documento")
            pag.wait_for_load_state("networkidle")
            medida = pag.evaluate(
                """() => {
                  const p2 = document.querySelector('.pagina2');
                  if (!p2) return null;
                  const alb = p2.querySelector('.album');
                  const vw = p2.querySelector('.verify-wrap');
                  const ft = p2.querySelector('.p2-footer');
                  const caja = e => { const b = e.getBoundingClientRect();
                    return {w: b.width, h: b.height, top: b.top, bottom: b.bottom}; };
                  return {
                    pagina: caja(p2),
                    album: alb ? caja(alb) : null,
                    fotos: p2.querySelectorAll('.photo').length,
                    tarjetas: p2.querySelectorAll('.verify-card').length,
                    badge: (p2.querySelector('[data-field="album_cuenta"]')||{}).textContent,
                    proof: (p2.querySelector('[data-field="verificaciones_cuenta"]')||{}).textContent,
                    pie: (p2.querySelector('[data-field="pagina_pie"]')||{}).textContent,
                    verify: vw ? caja(vw) : null,
                    verifyOculto: vw ? getComputedStyle(vw).display === 'none' : null,
                    footer: ft ? caja(ft) : null,
                    html: p2.innerHTML,
                  };
                }"""
            )
            if medida is None:
                check(f"{cuantas} fotos: la página 2 existe", False)
                continue

            px_mm = 96 / 25.4
            check(
                f"{cuantas:2d} fotos: hay {cuantas} recuadros en el álbum",
                medida["fotos"] == cuantas,
                str(medida["fotos"]),
            )
            check(
                f"{cuantas:2d} fotos: el contador dice lo que hay",
                medida["badge"].strip().startswith(str(cuantas)),
                repr(medida["badge"]),
            )
            check(
                f"{cuantas:2d} fotos: el álbum sigue midiendo 136mm en la hoja",
                abs(medida["album"]["h"] - 136 * px_mm) < 1.0,
                f'{medida["album"]["h"] / px_mm:.1f}mm',
            )
            check(
                f"{cuantas:2d} fotos: la hoja mide 210x297mm",
                abs(medida["pagina"]["w"] - 210 * px_mm) < 1.0
                and abs(medida["pagina"]["h"] - 297 * px_mm) < 1.0,
                f'{medida["pagina"]["w"] / px_mm:.0f}x{medida["pagina"]["h"] / px_mm:.0f}mm',
            )
            check(
                f"{cuantas:2d} fotos: se imprimen {cuantas_v} tarjetas y no seis",
                medida["tarjetas"] == cuantas_v,
                str(medida["tarjetas"]),
            )
            check(
                f"{cuantas:2d} fotos: el contador de verificaciones cuadra",
                medida["proof"].strip().startswith(str(cuantas_v)),
                repr(medida["proof"]),
            )
            check(
                f"{cuantas:2d} fotos: el pie dice «Página 2 de 2»",
                "2 de 2" in medida["pie"],
                repr(medida["pie"]),
            )
            check(
                f"{cuantas:2d} fotos: el pie queda pegado al borde de abajo",
                abs(medida["footer"]["bottom"] - (medida["pagina"]["bottom"] - 5.5 * px_mm)) < 2,
                f'{(medida["pagina"]["bottom"] - medida["footer"]["bottom"]) / px_mm:.1f}mm de margen',
            )
            check(
                f"{cuantas:2d} fotos: el panel mide lo calculado",
                abs(medida["verify"]["h"] - doc_engine.alto_verificaciones(cuantas_v) * px_mm) < 1.5,
                f'{medida["verify"]["h"] / px_mm:.1f}mm',
            )
            check(
                f"{cuantas:2d} fotos: ya no está la pastilla «Foto 09»",
                "Foto 0" not in medida["html"],
            )

        # Con seis marcadas el panel tiene que medir los 71mm que el diseno
        # llevaba escritos a mano. Es la prueba de que la formula no se ha
        # inventado el numero: reproduce el original en su caso original.
        check(
            "con las seis, el panel mide los 71mm del diseño original",
            abs(doc_engine.alto_verificaciones(6) - 71.0) < 0.05,
            f"{doc_engine.alto_verificaciones(6):.2f}mm",
        )

        # Sin verificaciones, el panel no se imprime.
        marcar([])
        pag.goto(f"{BASE}/facturas/{fid}/documento")
        oculto = pag.evaluate(
            "() => { const v=document.querySelector('.pagina2 .verify-wrap');"
            " return v ? getComputedStyle(v).display === 'none' : null; }"
        )
        check("sin ninguna marcada, el panel verde no sale", oculto is True, str(oculto))

        # Sin fotografias, no hay pagina 2.
        pag.request.post(f"{BASE}/facturas/{fid}/album/vaciar")
        check("vaciar el álbum lo deja vacío", fotos_en_bd(fid) == [])
        pag.goto(f"{BASE}/facturas/{fid}/documento")
        estado = pag.evaluate(
            "() => { const s=document.querySelector('.page-shell-2');"
            " return s ? getComputedStyle(s).display : 'no existe'; }"
        )
        check("sin fotografías, la página 2 no se imprime", estado == "none", str(estado))

        # --- 4 · el dato del vendedor no puede meter marcado ----------------
        print("\n4 · Un título de vehículo con comillas no rompe el documento")
        con = sqlite3.connect(DB)
        titulo_antes = con.execute("SELECT vehicle_title FROM invoice WHERE id=?", (fid,)).fetchone()[0]
        trampa = 'Audi "A3" <script>alert(1)</script>'
        con.execute("UPDATE invoice SET vehicle_title=? WHERE id=?", (trampa, fid))
        con.commit()
        con.close()
        subir(zip_de_fotos(["foto1.jpg", "foto2.jpg"]))
        # Se miran los BYTES que sirve el servidor, no el innerHTML del DOM.
        # Al serializar, el navegador NO vuelve a escapar el < dentro de un
        # valor de atributo -no le hace falta-, asi que innerHTML ensena un
        # "<script>" que en el documento servido no existe. Mirandolo ahi, esta
        # comprobacion daba un fallo que no era tal.
        crudo = pag.request.get(f"{BASE}/facturas/{fid}/documento").text()
        trozo = crudo[crudo.find('class="pagina2"'):]
        check("en lo que se sirve no hay ningún <script en la página 2", "<script" not in trozo)
        check(
            "el título llega escapado al alt",
            "&lt;script&gt;" in trozo and "&quot;A3&quot;" in trozo,
            trozo[trozo.find("alt=") : trozo.find("alt=") + 90] if "alt=" in trozo else "",
        )
        check(
            "y el navegador no crea ningún elemento script en la página 2",
            pag.evaluate("() => document.querySelectorAll('.pagina2 script').length") == 0,
        )
        con = sqlite3.connect(DB)
        con.execute("UPDATE invoice SET vehicle_title=? WHERE id=?", (titulo_antes, fid))
        con.commit()
        con.close()

        # --- 5 · control positivo -------------------------------------------
        print("\n5 · Control positivo: estas comprobaciones saben ponerse en rojo")

        # Si el orden fuera alfabetico y no natural, la posicion 2 seria
        # 'foto10'. Se comprueba que la clave de orden REALMENTE los separa.
        from app.album_zip import clave_natural

        alfabetico = sorted(["foto2.jpg", "foto10.jpg"])
        natural = sorted(["foto2.jpg", "foto10.jpg"], key=clave_natural)
        check(
            "el orden alfabético y el natural dan resultados DISTINTOS",
            alfabetico != natural,
            f"alfabético {alfabetico[0]} · natural {natural[0]}",
        )

        # Si la comprobacion del contenido no existiera, un .jpg con texto
        # dentro pasaria. Se comprueba que Pillow lo rechaza de verdad.
        from app import uploads

        try:
            uploads.comprobar_imagen(b"GIF89a" + b"\0" * 100)
            paso = True
        except uploads.SubidaInvalida:
            paso = False
        check("un GIF (formato no admitido) se rechaza", not paso)

        try:
            uploads.comprobar_imagen(IMAGENES[0])
            paso = True
        except uploads.SubidaInvalida:
            paso = False
        check("y un JPEG de verdad se acepta (si no, rechazaría todo)", paso)

        navegador.close()

    print("\n" + "=" * 58)
    print(f"{len(ok)} comprobaciones correctas, {len(fallos)} fallos")
    if fallos:
        for f in fallos:
            print(f"  FALLA: {f}")
        sys.exit(1)
    print("Hito B verificado.")


if __name__ == "__main__":
    main()
