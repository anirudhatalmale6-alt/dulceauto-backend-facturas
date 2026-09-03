"""
Comprobacion del Hito A (Milestone 5): Actividad protegida y limpieza segura.

Alcance cerrado con el cliente:

    - Actividad exige la misma Master Password que Configuracion, sin segunda
      contrasena y con la misma sesion.
    - La proteccion es de servidor: escribir /actividad a mano no la salta.
    - Las 6 ultimas acciones del Dashboard quedan igual de protegidas; el resto
      del Dashboard sigue funcionando con normalidad.
    - "Limpiar historial" exige la Master Password y una confirmacion.
    - Antes de borrar se guarda una copia completa del historial.
    - Se borra SOLO activity_log. Nada de facturas, notas, snapshots, fotos,
      folios ni Configuracion.
    - Despues del borrado queda una unica entrada: quien, cuando y cuantas
      habia.

Se ejecuta contra el servidor de verdad, por HTTP y con sesion, no llamando a
las funciones por dentro: lo que importa es lo que ocurre cuando alguien
escribe una direccion a mano o envia un formulario desde fuera.

    python verificar_hito_a.py http://127.0.0.1:PUERTO /ruta/al/data

La base tiene que ser una COPIA: el guion BORRA el historial de actividad.

Solo stdlib, para no anadir ninguna dependencia al proyecto.
"""
import csv
import http.cookiejar
import io
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8731").rstrip("/")
DATA = Path(sys.argv[2] if len(sys.argv) > 2 else "data")
DB = DATA / "dulceauto.db"
EXPORTES = DATA / "exportes"

ADMIN_USER, ADMIN_PASS = "admin", "DulceAuto2026"
MASTER = "Master2026"

# Detalles hostiles que se siembran en el historial ANTES de exportarlo. No son
# adorno: son las tres formas conocidas de que un CSV que alguien abre en Excel
# mienta o se rompa.
DETALLE_FORMULA = '=1+1'          # Excel lo ejecutaria al abrir el archivo
DETALLE_COMA = 'Ciudad, Estado'    # partiria la fila en dos columnas
DETALLE_COMILLA = 'Cliente "VIP"'  # desplazaria el resto del archivo
DETALLE_SALTO = 'Linea 1\nLinea 2'  # partiria la fila en dos filas
DETALLE_TEL = '+52 55 1234 5678'   # tambien empieza por caracter de formula

# Marca con la que se comprueba si el registro se esta enseñando o no. Va sin
# comillas ni signos a proposito: Jinja escapa las comillas al pintar el HTML
# (bien), asi que buscar 'Cliente "VIP"' en la pagina no encuentra nada NUNCA y
# la prueba pasaria sin comprobar nada. Es justo lo que descubrio el control
# positivo la primera vez que se ejecuto este guion.
MARCA = 'MARCA-HITO-A-9F3C'

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


# --- cliente HTTP con sesion --------------------------------------------------

cookies = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))


class SinRedireccion(urllib.request.HTTPRedirectHandler):
    """Para ver el 303 y su Location tal cual, sin seguirlo."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


opener_sin_seguir = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(cookies), SinRedireccion
)


def get(ruta: str) -> tuple[int, str]:
    try:
        with opener.open(BASE + ruta, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def post(ruta: str, datos: dict, seguir: bool = True) -> tuple[int, str, str]:
    """Devuelve (codigo, cuerpo, destino). Con seguir=False no sigue el 303, que
    es lo unico que permite comprobar A DONDE manda el servidor."""
    cuerpo = urllib.parse.urlencode(datos).encode()
    o = opener if seguir else opener_sin_seguir
    try:
        with o.open(BASE + ruta, data=cuerpo, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace"), r.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers.get("Location", "")


# --- lectura directa de la base (solo para comprobar, nunca para actuar) ------


def conteos() -> dict:
    """Cuantas filas hay en cada tabla. Sirve para probar que la limpieza no se
    lleva por delante nada mas que el historial."""
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        tablas = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name <> 'alembic_version'"
            )
        ]
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tablas}
    finally:
        con.close()


def sembrar_detalles_hostiles() -> None:
    con = sqlite3.connect(DB.as_posix())
    try:
        for detalle in (
            DETALLE_FORMULA,
            DETALLE_COMA,
            DETALLE_COMILLA,
            DETALLE_SALTO,
            DETALLE_TEL,
        ):
            con.execute(
                "INSERT INTO activity_log (actor, action, detail, created_at) "
                "VALUES ('Admin', 'invoice_updated', ?, datetime('now'))",
                (detalle,),
            )
        # La marca va la ultima y un segundo por delante, para que sea con
        # seguridad la primera de la lista y aparezca tambien en las SEIS del
        # Dashboard, no solo en las 200 de la pantalla de Actividad.
        con.execute(
            "INSERT INTO activity_log (actor, action, detail, created_at) "
            "VALUES ('Admin', 'invoice_updated', ?, datetime('now', '+1 second'))",
            (MARCA,),
        )
        con.commit()
    finally:
        con.close()


def historial() -> tuple[int, bool]:
    """Cuantas entradas hay y si la marca sigue estando. Las dos cosas, porque
    el numero SUBE solo: cada desbloqueo y cada bloqueo se registran. Comparar
    solo el total daria por borrado lo que unicamente habia crecido."""
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        n = con.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
        viva = con.execute(
            "SELECT COUNT(*) FROM activity_log WHERE detail = ?", (MARCA,)
        ).fetchone()[0] > 0
        return n, viva
    finally:
        con.close()


def snapshots_en_disco() -> set[str]:
    raiz = DATA / "snapshots"
    return {p.name for p in raiz.rglob("*")} if raiz.exists() else set()


def subidas_en_disco() -> set[str]:
    raiz = DATA / "uploads"
    return {p.name for p in raiz.rglob("*")} if raiz.exists() else set()


# --- 0. acceso ----------------------------------------------------------------

print("\n0 · Acceso al panel")
post("/acceso", {"username": ADMIN_USER, "password": ADMIN_PASS})
codigo, cuerpo = get("/")
check("Sesion de Admin abierta", codigo == 200 and "Dashboard" in cuerpo)

# Marca inconfundible dentro del historial. Todo lo que sigue se apoya en ella:
# si esta cadena aparece, el registro se esta enseñando; si no aparece, no.
sembrar_detalles_hostiles()


# --- 1. Actividad protegida en el servidor -----------------------------------

print("\n1 · Actividad exige la Master Password")

codigo, cuerpo = get("/actividad")
bloqueada = "Actividad bloqueada" in cuerpo
check("Con el candado cerrado, /actividad enseña la pantalla de bloqueo",
      codigo == 200 and bloqueada)
check("Escribir /actividad a mano no salta la proteccion",
      MARCA not in cuerpo,
      "el registro no viaja dentro del HTML")
check("No se ofrece una segunda contrasena distinta",
      "/configuracion/desbloquear" in cuerpo,
      "usa el mismo formulario y la misma Master Password")

# Control positivo: la comprobacion de arriba solo vale si esta misma busqueda
# SI encuentra la marca cuando el candado esta abierto. Sin esto, un cambio que
# rompiera el registro pasaria por "protegido".
post("/configuracion/desbloquear", {"master_password": MASTER, "destino": "/actividad"})
codigo, cuerpo_abierto = get("/actividad")
check("CONTROL POSITIVO · con el candado abierto la marca SI aparece",
      MARCA in cuerpo_abierto,
      "la prueba de arriba sabe distinguir")

# Y la sesion es la misma que la de Configuracion, como se acordo.
codigo, cuerpo_conf = get("/configuracion")
check("Desbloquear desde Actividad abre tambien Configuracion",
      "Configuración maestra" in cuerpo_conf,
      "misma contrasena y misma sesion")


# --- 2. Dashboard: solo el bloque de Actividad queda tapado -------------------

print("\n2 · Las 6 ultimas acciones del Dashboard")

codigo, dash_abierto = get("/")
check("CONTROL POSITIVO · con el candado abierto el Dashboard enseña la marca",
      MARCA in dash_abierto)

post("/configuracion/bloquear", {"destino": "/actividad"})
codigo, dash_cerrado = get("/")
check("Con el candado cerrado el Dashboard no enseña el registro",
      MARCA not in dash_cerrado,
      "no viaja escondido en el HTML")
check("El resto del Dashboard sigue funcionando",
      codigo == 200
      and "Actividad reciente" in dash_cerrado
      and "Contenido protegido" in dash_cerrado,
      "totales y facturas recientes intactos")
for pieza in ("Facturas recientes", "Vehículos", "Mercados"):
    if pieza in dash_abierto:
        check(f"Dashboard · «{pieza}» se sigue pintando bloqueado",
              pieza in dash_cerrado)


# --- 3. Limpiar historial: las dos barreras ----------------------------------

print("\n3 · Limpiar historial · barreras")

n_antes, _ = historial()

# 3a. Con el candado CERRADO, un envio directo del formulario no borra nada.
codigo, _, destino = post("/actividad/limpiar", {"confirmacion": "LIMPIAR"}, seguir=False)
ahora, viva = historial()
check("Sin Master Password no borra nada",
      ahora >= n_antes and viva,
      f"{ahora} entradas, habia {n_antes}")
check("Sin Master Password devuelve a /actividad",
      destino.endswith("/actividad"), destino)

# 3b. Con el candado abierto pero sin confirmar, tampoco.
post("/configuracion/desbloquear", {"master_password": MASTER, "destino": "/actividad"})
n_antes, _ = historial()
codigo, cuerpo, _ = post("/actividad/limpiar", {"confirmacion": ""})
ahora, viva = historial()
check("Con la confirmacion vacia no borra nada",
      ahora >= n_antes and viva,
      f"{ahora} entradas, habia {n_antes}")

n_antes, _ = historial()
codigo, cuerpo, _ = post("/actividad/limpiar", {"confirmacion": "limpiar el historial entero"})
ahora, viva = historial()
check("Con una confirmacion equivocada no borra nada",
      ahora >= n_antes and viva,
      f"{ahora} entradas, habia {n_antes}")
check("Y lo dice, en vez de callarse",
      "Escribe LIMPIAR" in cuerpo)


# --- 4. El destino del desbloqueo no acepta cualquier direccion ---------------

print("\n4 · El formulario no puede reenviar a donde quiera")

post("/configuracion/bloquear", {})
codigo, _, destino = post(
    "/configuracion/desbloquear",
    {"master_password": MASTER, "destino": "https://ejemplo-malicioso.test/roba"},
    seguir=False,
)
check("Un destino ajeno se ignora y cae en Configuracion",
      destino == "/configuracion",
      destino or "sin Location")


# --- 5. La limpieza de verdad ------------------------------------------------

print("\n5 · Limpieza · copia primero, borrado despues")

antes = conteos()
n_antes = antes.get("activity_log", 0)
snaps_antes = snapshots_en_disco()
subidas_antes = subidas_en_disco()
previos = {p.name for p in EXPORTES.glob("*.csv")} if EXPORTES.exists() else set()

codigo, cuerpo, _ = post("/actividad/limpiar", {"confirmacion": "LIMPIAR"})
despues = conteos()

nuevos = ({p.name for p in EXPORTES.glob("*.csv")} - previos) if EXPORTES.exists() else set()
check("Se ha escrito una copia nueva en el servidor", len(nuevos) == 1, ", ".join(nuevos))

check("El historial queda con UNA sola entrada",
      despues.get("activity_log", 0) == 1,
      f"{despues.get('activity_log', 0)} entradas")

codigo, cuerpo = get("/actividad")
check("Esa entrada dice quien, cuando y cuantas habia",
      "Historial de actividad limpiado" in cuerpo and f"{n_antes} entradas" in cuerpo,
      f"esperaba «{n_antes} entradas eliminadas»")

# Lo que NO se ha tocado. Se comprueba tabla por tabla, no de memoria.
intactas = [
    t for t in antes
    if t != "activity_log" and antes[t] != despues.get(t)
]
check("No se ha tocado ninguna otra tabla",
      not intactas,
      "cambiaron: " + ", ".join(intactas) if intactas else
      f"{len(antes) - 1} tablas iguales")
check("No se ha borrado ningun documento del historico",
      snapshots_en_disco() == snaps_antes,
      f"{len(snaps_antes)} archivos")
check("No se ha borrado ninguna fotografia ni logo",
      subidas_en_disco() == subidas_antes,
      f"{len(subidas_antes)} archivos")


# --- 6. La copia es legible y no miente --------------------------------------

print("\n6 · La copia que queda en el servidor")

if nuevos:
    ruta = EXPORTES / next(iter(nuevos))
    crudo = ruta.read_bytes()
    check("Lleva BOM, para que Excel no destroce los acentos",
          crudo.startswith(b"\xef\xbb\xbf"),
          "sin BOM, «Configuración» se lee «ConfiguraciÃ³n»")

    texto = crudo.decode("utf-8-sig")
    filas = list(csv.reader(io.StringIO(texto)))
    cabecera, cuerpo_csv = filas[0], filas[1:]

    check("Tiene una fila por entrada, ni una mas ni una menos",
          len(cuerpo_csv) == n_antes,
          f"{len(cuerpo_csv)} filas para {n_antes} entradas")
    check("Ninguna fila se ha partido ni desplazado",
          all(len(f) == len(cabecera) for f in cuerpo_csv),
          f"{len(cabecera)} columnas")

    detalles = [f[cabecera.index("detalle")] for f in cuerpo_csv]
    check("Un detalle con coma sigue en UNA sola celda",
          DETALLE_COMA in detalles)
    check("Un detalle con comillas se conserva entero",
          DETALLE_COMILLA in detalles)
    check("Un detalle con salto de linea no parte la fila",
          DETALLE_SALTO in detalles)
    check("Una formula queda desactivada",
          "'" + DETALLE_FORMULA in detalles,
          "Excel lo abre como texto, no lo ejecuta")
    check("Un telefono con + queda desactivado y legible",
          "'" + DETALLE_TEL in detalles)
    check("La copia trae la accion en castellano, no solo la clave",
          "accion_legible" in cabecera)
    check("Lo que se borro de la pantalla esta dentro de la copia",
          MARCA in detalles,
          "la copia es la unica forma de recuperarlo")


# --- 7. Nada de esto ha necesitado una migracion ------------------------------

print("\n7 · Sin cambios de esquema")
con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
columnas = [r[1] for r in con.execute("PRAGMA table_info(activity_log)")]
con.close()
check("activity_log conserva sus columnas de siempre",
      columnas == ["id", "actor", "action", "entity_type", "entity_id",
                   "folio", "detail", "ip", "created_at"],
      ", ".join(columnas))


print("\n" + "=" * 62)
print(f"  {_ok} comprobaciones OK, {len(_fallos)} fallos")
for f in _fallos:
    print(f"   - {f}")
print("=" * 62)
sys.exit(1 if _fallos else 0)
