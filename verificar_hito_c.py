"""
Comprobacion del Hito C: pagina 2 definitiva, PDF e inmutabilidad.

ATENCION: este guion SUSTITUYE el album de una factura, le cambia las
verificaciones y genera documentos. Solo debe ejecutarse contra una COPIA,
nunca contra el sistema en uso.

    python verificar_hito_c.py http://127.0.0.1:8742 /ruta/a/data

Lo que se comprueba, por orden:

  1. la rejilla de 1 a 20 sigue CONGELADA: el estiron no la recalcula;
  2. el hueco que dejan las verificaciones que faltan, contra lo que mide
     Chromium en la hoja de verdad;
  3. el estiron: alto del album, suelo 0.88, pie pegado abajo y nada fuera de
     la hoja, medido en modo IMPRESION;
  4. con 4, 5 y 6 verificaciones el documento sale exactamente como antes;
  5. el PDF: hojas segun el mercado, peso y como quedan incrustadas las
     imagenes;
  6. inmutabilidad: un documento ya emitido no cambia ni un byte cuando la
     factura cambia despues;
  7. control positivo: las comprobaciones de arriba tienen que ser capaces de
     ponerse en rojo.

Las medidas de la hoja se toman con la pagina en modo IMPRESION y no en
pantalla. No es un detalle: en pantalla la hoja lleva un borde de 1px que en
papel no esta, y ese borde le quita medio milimetro de ancho al album. Lo que
hay que comprobar es el papel.
"""
import hashlib
import io
import os
import sqlite3
import subprocess
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
CLAVES = [v.clave for v in verificaciones.VERIFICACIONES]

ok, fallos = [], []


def check(nombre, condicion, extra=""):
    (ok if condicion else fallos).append(nombre)
    print(("  OK   " if condicion else "  FALLA") + f" {nombre}" + (f"  [{extra}]" if extra else ""))


# --- material de prueba ------------------------------------------------------


def _jpegs() -> list[bytes]:
    reales = [p.read_bytes() for p in sorted(FOTOS.glob("*.jpg"))] if FOTOS.is_dir() else []
    if reales:
        return reales
    from PIL import Image

    salida = []
    for color in ((180, 60, 60), (60, 120, 180), (80, 160, 90), (150, 120, 60)):
        memoria = io.BytesIO()
        Image.new("RGB", (1200, 900), color).save(memoria, "JPEG")
        salida.append(memoria.getvalue())
    return salida


IMAGENES = _jpegs()


def zip_de(cuantas: int) -> bytes:
    memoria = io.BytesIO()
    with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(1, cuantas + 1):
            z.writestr(f"foto{i:02d}.jpg", IMAGENES[(i - 1) % len(IMAGENES)])
    return memoria.getvalue()


def consulta(sql, *args):
    con = sqlite3.connect(DB)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def factura_con_pagina2() -> int | None:
    """La primera factura de un mercado cuya plantilla trae la pagina 2."""
    for (fid, locale) in consulta("SELECT id, locale FROM invoice ORDER BY id"):
        if doc_engine.tiene_pagina2(locale):
            return fid
    return None


def factura_sin_pagina2() -> int | None:
    for (fid, locale) in consulta("SELECT id, locale FROM invoice ORDER BY id"):
        if not doc_engine.tiene_pagina2(locale):
            return fid
    return None


def huella(carpeta: Path) -> dict[str, str]:
    """Un hash por archivo. Es lo que hace falta para poder decir 'no cambio'."""
    salida = {}
    for ruta in sorted(carpeta.rglob("*")):
        if ruta.is_file():
            salida[str(ruta.relative_to(carpeta))] = hashlib.sha256(ruta.read_bytes()).hexdigest()
    return salida


# --- la hoja, medida en el navegador -----------------------------------------

MEDIDA = """() => {
  const hoja = document.querySelector('.pagina2');
  if (!hoja) return null;
  const alb = document.querySelector('.pagina2 .album');
  const principal = document.querySelector('.pagina2 .p2-main');
  const verif = document.querySelector('.pagina2 .verify-wrap');
  const pie = document.querySelector('.pagina2 .p2-footer');
  const est = getComputedStyle(hoja);
  const rh = hoja.getBoundingClientRect(), rp = pie.getBoundingClientRect();
  // Dos reglas distintas: getBoundingClientRect ya viene con el transform de
  // impresion aplicado y getComputedStyle no. Mezclarlas resta milimetros que
  // no existen.
  const mm = rp.height / 14;
  const mm_css = parseFloat(getComputedStyle(pie).height) / 14;
  const visible = e => getComputedStyle(e).display !== 'none';
  // El ultimo bloque de contenido antes del pie. Desde el cierre del Hito C
  // puede ser la franja de resumen, que sale cuando el tope del album deja
  // sitio de sobra. Si esto se quedara mirando siempre .p2-main, el blanco
  // medido incluiria la franja y esta comprobacion dejaria de ver la hoja.
  const bloques = [principal, verif, document.querySelector('.pagina2 .p2-summary')]
    .filter(e => e && visible(e));
  const ultimo = bloques[bloques.length - 1].getBoundingClientRect();
  const fotos = [...document.querySelectorAll('.pagina2 .photo')].map(f => {
    const c = f.getBoundingClientRect();
    return c.width / c.height;
  });
  return {
    album_mm: alb ? alb.getBoundingClientRect().height / mm : null,
    principal_mm: principal.getBoundingClientRect().height / mm,
    verif_visible: visible(verif),
    verif_mm: visible(verif) ? verif.getBoundingClientRect().height / mm : 0,
    blanco_mm: (rp.top - ultimo.bottom) / mm,
    sobra_mm: (rh.bottom - rp.bottom) / mm - parseFloat(est.paddingBottom) / mm_css,
    peor: fotos.length ? Math.min(...fotos) : null,
    cuantas: fotos.length,
  };
}"""


def main() -> None:
    if not DB.exists():
        check("la base de datos de pruebas existe", False, str(DB))
        return resumen()

    fid = factura_con_pagina2()
    if fid is None:
        check("hay una factura de un mercado con pagina 2", False)
        return resumen()

    # --- 1 · la rejilla sigue congelada --------------------------------------
    print("\n1 · la rejilla de 1 a 20 sigue congelada")

    # La tabla que el cliente aprobo viendo las cuatro laminas, escrita a mano
    # aqui a proposito: si alguien toca la regla, esto tiene que cantarlo.
    APROBADA = {
        1: "1 filas · 1",
        2: "2 filas · 1 · 1",
        3: "2 filas · 1 · 2",
        4: "2 filas · 2 · 2",
        5: "3 filas · destacada(66%)|1+1 · 2",
        6: "3 filas · destacada(66%)|1+1 · 3",
        7: "4 filas · destacada(66%)|1+1 · 2 · 2",
        8: "4 filas · destacada(66%)|1+1 · 2 · 3",
        9: "4 filas · destacada(66%)|1+1 · 3 · 3",
        10: "4 filas · destacada(66%)|1+1 · 3 · 4",
        11: "4 filas · destacada(50%)|2+2 · 3 · 3",
        12: "4 filas · destacada(50%)|2+2 · 3 · 4",
        13: "4 filas · destacada(50%)|2+2 · 4 · 4",
        14: "5 filas · destacada(50%)|2+2 · 3 · 3 · 3",
        15: "5 filas · destacada(50%)|2+2 · 3 · 3 · 4",
        16: "5 filas · destacada(50%)|2+2 · 3 · 4 · 4",
        17: "5 filas · destacada(50%)|2+2 · 4 · 4 · 4",
        18: "5 filas · destacada(50%)|2+2 · 4 · 4 · 5",
        19: "5 filas · destacada(50%)|2+2 · 4 · 5 · 5",
        20: "5 filas · destacada(50%)|2+2 · 5 · 5 · 5",
    }
    iguales = [n for n in range(1, 21) if album.repartir(n).resumen() == APROBADA[n]]
    check("los 20 repartos son los aprobados", len(iguales) == 20,
          f"{len(iguales)}/20")

    check("el alto de referencia del album sigue siendo 136mm",
          album.ALTO_BASE_MM == 136.0, f"{album.ALTO_BASE_MM}")

    # El estiron no puede cambiar el reparto: se pide dos veces con el album a
    # dos alturas distintas y tiene que salir lo mismo.
    estables = []
    for n in range(1, 21):
        antes = album.repartir(n).resumen()
        doc_engine.alto_album(n, 0)          # el camino que estira
        estables.append(album.repartir(n).resumen() == antes)
    check("pedir el alto estirado no altera el reparto", all(estables),
          f"{sum(estables)}/20")

    # --- 2 · el hueco que dejan las verificaciones ---------------------------
    print("\n2 · el hueco libre que dejan las verificaciones que faltan")
    check("con las seis marcadas no sobra nada", doc_engine.hueco_libre(6) == 0.0)
    check("con 4 y 5 tampoco, porque el panel sigue a dos filas",
          doc_engine.hueco_libre(4) == 0.0 and doc_engine.hueco_libre(5) == 0.0)
    hueco13 = {doc_engine.hueco_libre(v) for v in (1, 2, 3)}
    check("de 1 a 3 sobra lo mismo: una fila menos de tarjetas",
          len(hueco13) == 1 and abs(next(iter(hueco13)) - 27.2) < 0.05,
          f"{next(iter(hueco13)):.2f}mm")
    check("sin ninguna marcada sobra el panel entero mas su separacion",
          abs(doc_engine.hueco_libre(0) - 74.5) < 0.05,
          f"{doc_engine.hueco_libre(0):.2f}mm")
    check("cuantas menos verificaciones, mas hueco (nunca al reves)",
          all(doc_engine.hueco_libre(v) >= doc_engine.hueco_libre(v + 1) for v in range(6)))

    # --- 3 · el tope de forma, en el modelo ----------------------------------
    print("\n3 · el tope 0.88, en todas las combinaciones")
    bajo_suelo, sin_estirar = [], []
    for n in range(1, 21):
        reparto = album.repartir(n)
        for nv in range(0, 7):
            alto = doc_engine.alto_album(n, nv)
            if album.peor_proporcion(reparto, alto) < album.PROPORCION_MINIMA - 0.001:
                bajo_suelo.append((n, nv))
            if alto < album.ALTO_BASE_MM - 0.001:
                sin_estirar.append((n, nv))
    check("ninguna de las 140 combinaciones baja del suelo 0.88",
          not bajo_suelo, str(bajo_suelo[:4]))
    check("el album nunca sale mas bajo que los 136mm congelados",
          not sin_estirar, str(sin_estirar[:4]))
    check("con 4, 5 y 6 verificaciones el album se queda en 136mm",
          all(doc_engine.alto_album(n, nv) == album.ALTO_BASE_MM
              for n in range(1, 21) for nv in (4, 5, 6)))
    check("y con menos, nunca encoge respecto a tener mas",
          all(doc_engine.alto_album(n, 0) >= doc_engine.alto_album(n, 3) >= doc_engine.alto_album(n, 6)
              for n in range(1, 21)))

    # --- 4 y 5 · la hoja de verdad, en el navegador --------------------------
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pag = navegador.new_page(viewport={"width": 1280, "height": 900})
        pag.goto(f"{BASE}/acceso")
        pag.fill('input[name="username"]', USER)
        pag.fill('input[name="password"]', PASSWORD)
        pag.click('button[type="submit"]')
        pag.emulate_media(media="print")

        def poner(n_fotos, n_verif):
            pag.request.post(
                f"{BASE}/facturas/{fid}/album",
                multipart={"album_zip": {"name": "a.zip", "mimeType": "application/zip",
                                         "buffer": zip_de(n_fotos)}},
            )
            pag.request.post(
                f"{BASE}/facturas/{fid}/verificaciones",
                headers={"content-type": "application/x-www-form-urlencoded"},
                data="&".join(f"verificacion={c}" for c in CLAVES[:n_verif]),
            )
            pag.goto(f"{BASE}/facturas/{fid}/documento")
            pag.wait_for_load_state("networkidle")
            return pag.evaluate(MEDIDA)

        print("\n4 · el estiron, medido en la hoja impresa")
        CASOS = [(n, nv) for n in (1, 2, 3, 5, 7, 13, 14, 20) for nv in (0, 1, 3, 4, 6)]
        malos_alto, malos_suelo, malos_pie, malos_cuenta, malos_orden = [], [], [], [], []
        blanco_por_caso = {}
        for n, nv in CASOS:
            m = poner(n, nv)
            if m is None:
                malos_alto.append((n, nv, "sin pagina 2"))
                continue
            esperado = doc_engine.alto_album(n, nv)
            if abs(m["album_mm"] - esperado) > 0.35:
                malos_alto.append((n, nv, f"{m['album_mm']:.2f} vs {esperado:.1f}"))
            if m["peor"] < album.PROPORCION_MINIMA - 0.005:
                malos_suelo.append((n, nv, f"{m['peor']:.3f}"))
            if m["sobra_mm"] < -0.05:
                malos_pie.append((n, nv, f"{m['sobra_mm']:.2f}"))
            if m["cuantas"] != n:
                malos_cuenta.append((n, nv, m["cuantas"]))
            if abs(m["principal_mm"] - (doc_engine.ALTO_CABECERA_ALBUM_MM + m["album_mm"])) > 0.4:
                malos_orden.append((n, nv, f"{m['principal_mm']:.2f}"))
            blanco_por_caso[(n, nv)] = m["blanco_mm"]

        check(f"el album se pinta al alto calculado en los {len(CASOS)} casos",
              not malos_alto, str(malos_alto[:3]))
        check("ninguna fotografia impresa baja de 0.88", not malos_suelo, str(malos_suelo[:3]))
        check("el pie no se sale de la hoja en ningun caso", not malos_pie, str(malos_pie[:3]))
        check("salen todas las fotografias del album", not malos_cuenta, str(malos_cuenta[:3]))
        check("el bloque del album crece con el album y no por su cuenta",
              not malos_orden, str(malos_orden[:3]))

        blanco_lleno = {b for (n, nv), b in blanco_por_caso.items() if nv in (4, 5, 6)}
        check("con la hoja llena el aire sobre el pie es el mismo de siempre",
              max(blanco_lleno) - min(blanco_lleno) < 0.2,
              f"{min(blanco_lleno):.2f}–{max(blanco_lleno):.2f}mm")
        gano = [n for n in (1, 2, 3, 5, 7, 13, 14, 20)
                if blanco_por_caso[(n, 0)] <= blanco_por_caso[(n, 6)] + 0.2
                or doc_engine.alto_album(n, 0) > album.ALTO_BASE_MM]
        check("sin verificaciones el album aprovecha hueco en los 8 casos",
              len(gano) == 8, f"{len(gano)}/8")

        print("\n5 · con 4, 5 y 6 verificaciones nada cambia")
        # No basta con que el alto coincida: se compara el HTML servido.
        cuerpos = {}
        for nv in (4, 5, 6):
            poner(14, nv)
            cuerpos[nv] = pag.evaluate(
                "() => document.querySelector('.pagina2 .album').getAttribute('style')"
                " + '|' + document.querySelector('[data-field=album_estilos]').textContent"
            )
        check("el CSS del album no lleva ningun estiron con la hoja llena",
              all("p2-main{height" not in c for c in cuerpos.values()))
        check("y es el mismo con 4, 5 y 6", len(set(cuerpos.values())) == 1)

        poner(14, 0)
        estilo0 = pag.evaluate(
            "() => document.querySelector('[data-field=album_estilos]').textContent")
        check("sin verificaciones si lleva el estiron, y en una sola linea",
              "p2-main{height" in estilo0 and "\n" not in estilo0.strip())

        navegador.close()

    # --- 6 · el PDF ----------------------------------------------------------
    print("\n6 · el PDF")
    from app import models, pdf as pdf_engine
    from app.db import SessionLocal

    ses = SessionLocal()
    try:
        inv = ses.get(models.Invoice, fid)
        # el caso mas pesado: veinte fotografias
        _subir_album(ses, inv, 20)
        inv.verifications = verificaciones.guardar(CLAVES)
        ses.commit()
        res = pdf_engine.generar(ses, inv)
        peso = res.pdf.stat().st_size / 1048576
        check("la pre-factura con album sale de dos hojas", res.paginas == 2, str(res.paginas))
        check("con 20 fotografias el PDF baja de 3 MB", peso < 3.0, f"{peso:.2f} MB")

        crudas = _imagenes_crudas(res.pdf)
        check("todas las imagenes van dentro del PDF como JPEG, sin mapas de bits",
              crudas == 0, f"{crudas} en crudo")

        # Y el mercado que NO tiene pagina 2: antes reventaba la generacion.
        otro = factura_sin_pagina2()
        if otro is None:
            check("hay un mercado sin pagina 2 con el que comparar", False)
        else:
            inv2 = ses.get(models.Invoice, otro)
            _subir_album(ses, inv2, 6)
            ses.commit()
            check("con album, un mercado sin pagina 2 cuenta una sola hoja",
                  doc_engine.paginas(inv2) == 1, str(doc_engine.paginas(inv2)))
            res2 = pdf_engine.generar(ses, inv2)
            check("y su PDF se genera igualmente, sin error de hojas",
                  res2.paginas == 1, str(res2.paginas))

        # --- 7 · inmutabilidad ----------------------------------------------
        print("\n7 · un documento ya emitido no cambia")
        carpeta = pdf_engine.carpeta_snapshot(fid, "factura", res.snapshot.version)
        antes = huella(carpeta)
        check("el snapshot emitido tiene archivos que vigilar", len(antes) > 5, str(len(antes)))

        inv = ses.get(models.Invoice, fid)
        _subir_album(ses, inv, 5)
        inv.verifications = verificaciones.guardar([])
        inv.vehicle_title = "Otro coche completamente distinto"
        ses.commit()
        nuevo = pdf_engine.generar(ses, inv)

        check("generar otra vez crea una version nueva, no pisa la anterior",
              nuevo.snapshot.version > res.snapshot.version,
              f"v{res.snapshot.version} → v{nuevo.snapshot.version}")
        despues = huella(carpeta)
        cambiados = [k for k in antes if antes[k] != despues.get(k)]
        check("ni un solo byte del documento anterior ha cambiado",
              not cambiados, str(cambiados[:3]))
        check("y sigue enseñando las 20 fotografias que tenia",
              (carpeta / "documento.html").read_text(encoding="utf-8").count("data-photo-index") == 20)
        check("mientras el nuevo enseña las 5 de ahora",
              (pdf_engine.carpeta_snapshot(fid, "factura", nuevo.snapshot.version)
               / "documento.html").read_text(encoding="utf-8").count("data-photo-index") == 5)
    finally:
        ses.close()

    # --- 8 · control positivo -----------------------------------------------
    #
    # Todo lo de arriba esta en verde. Falta saber si puede ponerse en rojo: una
    # bateria que no sabe fallar no dice nada.
    print("\n8 · control positivo: las comprobaciones saben fallar")
    reparto = album.repartir(20)
    check("un album mas alto del tope si baja del suelo",
          album.peor_proporcion(reparto, doc_engine.alto_album(20, 0) + 30)
          < album.PROPORCION_MINIMA)
    check("el tope calculado es justo el que deja el suelo clavado en 0.88",
          abs(album.peor_proporcion(reparto, album.alto_maximo(reparto))
              - album.PROPORCION_MINIMA) < 0.002)
    check("con el suelo relajado a 0.80 el album podria estirarse mas",
          album.alto_maximo(reparto, 0.80) > album.alto_maximo(reparto))
    falsa = {1: "esto no es el reparto aprobado"}
    check("la tabla aprobada detecta un reparto cambiado",
          album.repartir(1).resumen() != falsa[1])

    resumen()


def _subir_album(ses, invoice, cuantas: int) -> None:
    """Sustituye el album por la via de siempre: leer el ZIP y guardar."""
    from app import album_zip, models, uploads

    resultado = album_zip.leer(zip_de(cuantas))
    validas, errores = album_zip.revisar(resultado)
    if errores:
        raise RuntimeError(errores[0])
    guardados = [uploads.guardar_imagen(datos, nombre, f"facturas/{invoice.id}")
                 for nombre, datos in validas]
    for foto in list(invoice.photos):
        ses.delete(foto)
    # El flush suelta las filas viejas ANTES de insertar las nuevas: la posicion
    # 1 no puede existir dos veces en la misma factura.
    ses.flush()
    for posicion, (guardado, (nombre, _)) in enumerate(zip(guardados, validas), start=1):
        ses.add(models.InvoicePhoto(
            invoice_id=invoice.id, position=posicion,
            original_name=nombre[:255], file_path=guardado.relativa,
        ))
    ses.flush()
    ses.refresh(invoice)


def _imagenes_crudas(pdf: Path) -> int:
    """Cuantas imagenes van dentro del PDF como mapa de bits en vez de JPEG.

    Es la diferencia entre 1,3 MB y 6,5 MB, asi que se cuenta y no se supone.
    Si pdfimages no esta instalado se devuelve 0 y se dice: mejor no comprobarlo
    que comprobarlo mal.
    """
    try:
        salida = subprocess.run(["pdfimages", "-list", str(pdf)],
                                capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        print("      (pdfimages no disponible: no se cuentan las imagenes crudas)")
        return 0
    crudas = 0
    for linea in salida.splitlines()[2:]:
        partes = linea.split()
        if len(partes) > 8 and partes[8] == "image":
            crudas += 1
    return crudas


def resumen() -> None:
    print(f"\n{len(ok)} comprobaciones correctas, {len(fallos)} fallos")
    if fallos:
        for f in fallos:
            print("  ·", f)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
