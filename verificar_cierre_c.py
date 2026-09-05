"""
Comprobacion del cierre del Hito C: la franja de resumen y la marca.

ATENCION: este guion SUSTITUYE el album de una factura y le cambia las
verificaciones. Solo debe ejecutarse contra una COPIA, nunca contra el sistema
en uso.

    python verificar_cierre_c.py http://127.0.0.1:8742 /ruta/a/data

Lo que se comprueba, por orden:

  1. la franja de resumen sale EXACTAMENTE en los casos en los que cabe entera,
     y en ninguno mas: las 140 combinaciones de fotografias y verificaciones,
     contra una lista escrita a mano;
  2. la hoja de verdad, en modo IMPRESION: alto de la franja, blanco que queda
     encima del pie comparado con el de la hoja llena, y que nada se sale;
  3. la franja no inventa ni un dato: los tres que ensena son los mismos que ya
     salen en otro sitio de la misma hoja;
  4. el CSS de la pagina 1 no se cuela en la franja. .summary-grid existe en
     factura.css sin acotar a ninguna hoja, y sin el prefijo p2- partiria la
     franja en tres columnas de anchos distintos;
  5. la marca: el dibujo y el nombre miden lo mismo IMPRESOS en las dos hojas,
     la pastilla azul con las iniciales ya no esta, y con logotipo propio de
     perfil de marca sale en las dos, cada una con su medida;
  6. control positivo: las comprobaciones de arriba tienen que ser capaces de
     ponerse en rojo.
"""
import io
import os
import re
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


def resumen():
    print(f"\n{len(ok)} comprobaciones correctas, {len(fallos)} fallos")
    for f in fallos:
        print(f"  - {f}")
    return 1 if fallos else 0


# --- material de prueba -------------------------------------------------------


def zip_de(cuantas: int) -> bytes:
    fuentes = sorted(p for p in FOTOS.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    memoria = io.BytesIO()
    with zipfile.ZipFile(memoria, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(1, cuantas + 1):
            z.writestr(f"foto{i:02d}.jpg", fuentes[(i - 1) % len(fuentes)].read_bytes())
    return memoria.getvalue()


def factura_con_pagina2() -> int | None:
    """La primera factura de un mercado cuya plantilla tiene pagina 2."""
    import sqlite3

    con = sqlite3.connect(DB)
    try:
        for fid, locale in con.execute("SELECT id, locale FROM invoice ORDER BY id"):
            if doc_engine.tiene_pagina2(locale):
                return fid
    finally:
        con.close()
    return None


# --- lo que se mide en la hoja ------------------------------------------------

MEDIDA = """() => {
  const hoja = document.querySelector('.pagina2');
  if (!hoja) return null;
  const pie = hoja.querySelector('.p2-footer');
  const franja = hoja.querySelector('.p2-summary');
  // Dos reglas: getBoundingClientRect ya trae aplicado el transform de
  // impresion y getComputedStyle no. El pie mide 14mm de diseno y sirve de
  // patron para pasar de pixeles pintados a milimetros.
  const rp = pie.getBoundingClientRect();
  const mm = rp.height / 14;
  const mm_css = parseFloat(getComputedStyle(pie).height) / 14;
  const visible = e => e && getComputedStyle(e).display !== 'none';
  const bloques = [...hoja.children].filter(e => visible(e) && e !== pie);
  const ultimo = bloques[bloques.length - 1].getBoundingClientRect();
  const texto = s => { const e = hoja.querySelector(s); return e ? e.textContent.trim() : null; };
  const tarjetas = [...hoja.querySelectorAll('.p2-summary-card')];
  return {
    franja_visible: visible(franja),
    franja_mm: visible(franja) ? franja.getBoundingClientRect().height / mm : 0,
    blanco_mm: (rp.top - ultimo.bottom) / mm,
    // Lo que sobra por debajo del pie: tiene que ser justo el relleno de la
    // hoja. Negativo significa que el pie se ha salido del papel.
    sobra_mm: (hoja.getBoundingClientRect().bottom - rp.bottom) / mm
              - parseFloat(getComputedStyle(hoja).paddingBottom) / mm_css,
    valores: tarjetas.map(t => t.querySelector('strong').textContent.trim()),
    anchos: tarjetas.map(t => t.getBoundingClientRect().width / mm),
    album_pastilla: texto('.album-badge'),
    folio_cabecera: texto('.p2-meta strong'),
    estado_ficha: texto('.p2-status'),
    // La marca, en las dos hojas. Los rectangulos ya llevan aplicada la escala
    // de impresion de cada una, asi que un pixel de aqui es un pixel de papel
    // en las dos y se pueden comparar directamente.
    marca: ['.invoice .brand', '.pagina2 .p2-brand'].map(s => {
      const raiz = document.querySelector(s);
      const caja = e => { const r = e.getBoundingClientRect();
                          return {w: r.width * 25.4 / 96, h: r.height * 25.4 / 96}; };
      const dibujo = raiz.querySelector('svg'), nombre = raiz.querySelector('span');
      return {dibujo: dibujo ? caja(dibujo) : null, nombre: nombre ? caja(nombre) : null};
    }),
    quedan_iniciales: !!hoja.querySelector('.p2-brand-mark'),
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

    # --- 1 · cuando sale la franja, en el modelo -----------------------------
    print("\n1 · la franja sale solo cuando cabe entera")

    # Escrita a mano a proposito, igual que la tabla de la rejilla: si alguien
    # cambia el alto de la franja o la regla del estiron, esto tiene que
    # cantarlo en vez de recalcularse y seguir dando verde.
    #
    # Todas son de "cero verificaciones", y no es casualidad: con una sola
    # marcada el panel ya ocupa una fila entera de tarjetas y el album se queda
    # con todo lo que sobra.
    ESPERADAS = {(n, 0) for n in (1, 3, 4, 5, 6, 10, 11, 12, 13, 18, 19, 20)}

    salen, apretadas, sobra_min = set(), [], None
    for n in range(1, album.MAX_FOTOS + 1):
        reparto = album.repartir(n)
        for nv in range(0, len(verificaciones.CLAVES) + 1):
            sobra = doc_engine.hueco_sobrante(reparto, nv)
            if doc_engine.hay_resumen(reparto, nv):
                salen.add((n, nv))
                sobra_min = sobra if sobra_min is None else min(sobra_min, sobra)
                if sobra + 0.001 < doc_engine.ALTO_RESUMEN_MM + doc_engine.SEPARACION_RESUMEN_MM:
                    apretadas.append((n, nv, round(sobra, 2)))

    check("sale exactamente en los 12 casos previstos y en ninguno mas",
          salen == ESPERADAS,
          f"de mas {sorted(salen - ESPERADAS)} · de menos {sorted(ESPERADAS - salen)}")
    check("y en ninguno de ellos va apretada", not apretadas, str(apretadas[:3]))
    check("con 4, 5 o 6 verificaciones no sale nunca",
          not any(nv >= 4 for _n, nv in salen))
    check("el caso mas justo aun deja sitio de sobra",
          sobra_min is not None
          and sobra_min >= doc_engine.ALTO_RESUMEN_MM + doc_engine.SEPARACION_RESUMEN_MM,
          f"sobran {sobra_min:.2f}mm y hacen falta "
          f"{doc_engine.ALTO_RESUMEN_MM + doc_engine.SEPARACION_RESUMEN_MM:.2f}mm")

    # Que no haya casos dudosos: o sobra sitio de sobra, o casi nada. Un caso
    # intermedio significaria que la decision de ensenarla o no depende de
    # decimales, y entonces habria que repensar el alto y no el umbral.
    dudosos = []
    for n in range(1, album.MAX_FOTOS + 1):
        reparto = album.repartir(n)
        for nv in range(0, len(verificaciones.CLAVES) + 1):
            sobra = doc_engine.hueco_sobrante(reparto, nv)
            if 2.0 < sobra < doc_engine.ALTO_RESUMEN_MM + doc_engine.SEPARACION_RESUMEN_MM:
                dudosos.append((n, nv, round(sobra, 2)))
    check("no hay ningun caso a medio camino", not dudosos, str(dudosos[:5]))

    # --- 2 a 5 · la hoja de verdad, en el navegador --------------------------
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

        print("\n2 · la franja, medida en la hoja impresa")
        CASOS = [(n, nv) for n in (1, 2, 5, 13, 14, 20) for nv in (0, 1, 4, 6)]
        malos_visible, malos_alto, malos_pie, blanco = [], [], [], {}
        for n, nv in CASOS:
            m = poner(n, nv)
            debe = doc_engine.hay_resumen(album.repartir(n), nv)
            if m["franja_visible"] != debe:
                malos_visible.append((n, nv, m["franja_visible"], debe))
            if debe and abs(m["franja_mm"] - doc_engine.ALTO_RESUMEN_MM) > 0.3:
                malos_alto.append((n, nv, round(m["franja_mm"], 2)))
            if m["sobra_mm"] < -0.05:
                malos_pie.append((n, nv, round(m["sobra_mm"], 2)))
            blanco[(n, nv)] = m["blanco_mm"]

        check(f"sale y se esconde donde toca en los {len(CASOS)} casos",
              not malos_visible, str(malos_visible[:3]))
        check(f"y mide sus {doc_engine.ALTO_RESUMEN_MM:g}mm cuando sale",
              not malos_alto, str(malos_alto[:3]))
        check("el pie sigue sin salirse de la hoja", not malos_pie, str(malos_pie[:3]))

        # La hoja llena -la que ya esta desplegada y aprobada- deja su propio
        # aire encima del pie. La medida de verdad no es "cuanto blanco queda"
        # sino "cuanto MAS que en la hoja llena", que es lo que el cliente ve
        # como hoja a medio terminar.
        lleno = [b for (_n, nv), b in blanco.items() if nv == 6]
        de_mas = {(n, nv): b - max(lleno) for (n, nv), b in blanco.items() if nv == 0}
        check("con la hoja llena el aire encima del pie es el de siempre",
              max(lleno) - min(lleno) < 0.2, f"{min(lleno):.2f}–{max(lleno):.2f}mm")
        check("sin verificaciones ya no queda mas blanco que con la hoja llena",
              max(de_mas.values()) < 2.0,
              " · ".join(f"{n}f {v:+.1f}mm" for (n, _nv), v in sorted(de_mas.items())))

        print("\n3 · la franja no inventa ningun dato")
        m = poner(20, 0)
        check("la cuenta de fotografias es la misma que la pastilla del album",
              m["valores"][0] == m["album_pastilla"],
              f"{m['valores'][0]!r} vs {m['album_pastilla']!r}")
        check("el folio es el mismo que el de la cabecera de la hoja",
              m["valores"][1] == m["folio_cabecera"],
              f"{m['valores'][1]!r} vs {m['folio_cabecera']!r}")
        check("el estado es el mismo que el de la ficha de la reserva",
              m["valores"][2] == m["estado_ficha"],
              f"{m['valores'][2]!r} vs {m['estado_ficha']!r}")
        m1 = poner(1, 0)
        check("con una sola fotografia lo dice en singular",
              m1["valores"][0] == "1 fotografía", m1["valores"][0])

        print("\n4 · el CSS de la pagina 1 no se cuela en la franja")
        # factura.css define .summary-grid SIN acotar a ninguna hoja, con tres
        # columnas de 1.02fr, 0.92fr y 1.08fr. Si la franja no llevara el
        # prefijo p2-, esa regla la partiria asi y nadie se enteraria.
        anchos = m["anchos"]
        check("las tres tarjetas de la franja miden lo mismo",
              max(anchos) - min(anchos) < 0.3,
              " · ".join(f"{a:.2f}mm" for a in anchos))
        hoja_css = pag.request.get(f"{BASE}/plantillas/assets/css/pagina2.css").text()
        check("y sus reglas van todas acotadas a la pagina 2",
              all(linea.startswith(".pagina2 ") or linea.startswith(".page-shell-2")
                  for linea in re.findall(r"^\.\S[^{]*(?=\{)", hoja_css, re.M)),
              str([linea for linea in re.findall(r"^\.\S[^{]*(?=\{)", hoja_css, re.M)
                   if not (linea.startswith(".pagina2 ") or linea.startswith(".page-shell-2"))][:3]))

        print("\n5 · la marca es la misma en las dos hojas")
        dibujo1, dibujo2 = m["marca"][0]["dibujo"], m["marca"][1]["dibujo"]
        nombre1, nombre2 = m["marca"][0]["nombre"], m["marca"][1]["nombre"]
        check("el dibujo mide lo mismo impreso en las dos",
              abs(dibujo1["w"] - dibujo2["w"]) < 0.25 and abs(dibujo1["h"] - dibujo2["h"]) < 0.25,
              f"{dibujo1['w']:.2f}x{dibujo1['h']:.2f}mm vs {dibujo2['w']:.2f}x{dibujo2['h']:.2f}mm")
        check("y el nombre tambien",
              abs(nombre1["w"] - nombre2["w"]) < 0.5 and abs(nombre1["h"] - nombre2["h"]) < 0.3,
              f"{nombre1['w']:.2f}x{nombre1['h']:.2f}mm vs {nombre2['w']:.2f}x{nombre2['h']:.2f}mm")
        check("la pastilla azul con las iniciales ya no esta",
              not m["quedan_iniciales"])

        # --- 6 · control positivo -------------------------------------------
        print("\n6 · control positivo: las comprobaciones saben ponerse en rojo")
        roto = pag.evaluate(
            "() => { const f = document.querySelector('.pagina2 .p2-summary');"
            " f.style.height = '20mm';"
            " const t = f.querySelector('.p2-summary-card strong');"
            " t.textContent = 'INVENTADO';"
            " const b = document.querySelector('.pagina2 .p2-brand svg');"
            " b.style.width = '12mm'; b.style.height = '11mm'; return true; }"
        )
        m_roto = pag.evaluate(MEDIDA)
        check("si la franja cambia de alto, se nota", roto and
              abs(m_roto["franja_mm"] - doc_engine.ALTO_RESUMEN_MM) > 0.3,
              f"{m_roto['franja_mm']:.2f}mm")
        check("si un dato de la franja deja de coincidir, se nota",
              m_roto["valores"][0] != m_roto["album_pastilla"])
        check("si la marca cambia de tamano, se nota",
              abs(m_roto["marca"][0]["dibujo"]["w"] - m_roto["marca"][1]["dibujo"]["w"]) >= 0.25)

        navegador.close()

    # --- 7 · con logotipo propio, sale en las dos hojas ----------------------
    print("\n7 · con logotipo propio de perfil de marca")
    from app import models  # noqa: F401
    from app.db import SessionLocal

    sesion = SessionLocal()
    try:
        factura = sesion.get(models.Invoice, fid)
        con_logo = doc_engine.render(
            factura, logo="/marcas/1/logo.png", marca="DulceAuto"
        ).html
    finally:
        sesion.close()

    estilos = re.findall(r'<img class="brand-logo"[^>]*style="([^"]*)"', con_logo)
    check("el logotipo propio sale en las dos hojas", len(estilos) == 2, str(estilos))
    check("cada hoja lo dimensiona en su unidad",
          len(estilos) == 2 and "px" in estilos[0] and "mm" in estilos[1],
          str(estilos))
    sesion = SessionLocal()
    try:
        sin_logo = doc_engine.render(sesion.get(models.Invoice, fid)).html
    finally:
        sesion.close()
    check("sin logotipo propio se queda la marca dibujada, en las dos hojas",
          '<img class="brand-logo"' not in sin_logo
          and sin_logo.count('<span class="brand-name">DulceAuto</span>') == 1
          and sin_logo.count('<span class="p2-brand-name">DulceAuto</span>') == 1)

    return resumen()


if __name__ == "__main__":
    sys.exit(main())
