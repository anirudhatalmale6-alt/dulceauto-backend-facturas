#!/usr/bin/env bash
#
# Limpieza de la base de desarrollo, para empezar en produccion desde cero.
#
#   bash limpiar.sh                        muestra que se borraria. No toca nada.
#   bash limpiar.sh --en-serio             limpia de verdad
#   bash limpiar.sh --en-serio --folio 90001   limpia y deja el contador ahi
#
# Borra las facturas de prueba y todo lo que cuelga de ellas: fotografias,
# snapshots, PDF y el registro de actividad de las pruebas.
#
# NO borra la Configuracion -datos bancarios, representantes, textos- ni las
# contrasenas ni las tres plantillas aprobadas. Esas se conservan enteras.
#
# Sin --en-serio no toca nada: solo cuenta lo que hay y dice que quedaria.
set -euo pipefail

PROYECTO="${DULCEAUTO_PROYECTO:-/opt/dulceauto}"
BACKUPS="${DULCEAUTO_BACKUPS:-/opt/dulceauto-backups}"

MODO="mirar"
FOLIO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --en-serio) MODO="limpiar" ;;
    --folio) FOLIO="${2:?--folio necesita un numero, por ejemplo 90001}"; shift ;;
    *) echo "Opcion desconocida: $1"; exit 1 ;;
  esac
  shift
done

# El contador guarda el SIGUIENTE numero a usar, no el ultimo usado, y el
# numero de digitos sale de la longitud de lo que se escriba aqui. Por eso
# tiene que ser exactamente "90001" y no "090001": lo segundo daria RES-090001.
if [ -n "$FOLIO" ]; then
  case "$FOLIO" in
    ''|*[!0-9]*) echo "El folio tiene que ser solo digitos: --folio 90001"; exit 1 ;;
  esac
fi

log() { printf "\n\033[1;34m==> %s\033[0m\n" "$*"; }
cd "$PROYECTO"

contar() {
  docker compose exec -T backend python - <<'PY'
import sqlite3, sys
c = sqlite3.connect("data/dulceauto.db")
q = lambda s: c.execute(s).fetchone()[0]
# La consulta de los VIN va en su propia variable para no anidar comillas
# dentro de la f-string: anidarlas solo compila en Python 3.12 en adelante.
SQL_VIN = "select count(distinct vehicle_vin) from invoice where vehicle_vin is not null and vehicle_vin != ''"
print(f"  facturas            : {q('select count(*) from invoice')}")
print(f"  vehiculos (VIN)     : {q(SQL_VIN)}")
print(f"  fotografias         : {q('select count(*) from invoice_photo')}")
print(f"  snapshots           : {q('select count(*) from invoice_snapshot')}")
print(f"  registro actividad  : {q('select count(*) from activity_log')}")
print(f"  ajustes (se guardan): {q('select count(*) from setting')}")
print(f"  credenciales        : {q('select count(*) from credential')}")
fila = c.execute("select value from setting where key='folio.next' and market is null").fetchone()
pref = c.execute("select value from setting where key='folio.prefix' and market is null").fetchone()
print(f"  proximo folio       : {(pref[0] if pref else 'RES-')}{fila[0] if fila else '?'}")
PY
}

log "Estado actual"
contar

echo
echo "  Archivos en disco:"
echo "    snapshots : $(find data/snapshots -type f 2>/dev/null | wc -l) archivos"
echo "    subidas   : $(find data/uploads   -type f 2>/dev/null | wc -l) archivos"

if [ "$MODO" != "limpiar" ]; then
  cat <<FIN

  No se ha tocado nada.

  Para limpiar de verdad:
      bash limpiar.sh --en-serio --folio 90001

FIN
  exit 0
fi

log "1/5 · Copia de seguridad antes de limpiar"
# Si la limpieza sale mal, o si manana alguien echa de menos algo, esta copia
# es la unica forma de volver atras.
install -d "$BACKUPS"
ANTES="${BACKUPS}/antes-de-limpiar-$(date +%F-%H%M).tar.gz"
tar -czf "$ANTES" -C "$PROYECTO" data
echo "  guardado en ${ANTES}"

log "2/5 · Parando el backend"
# Se para a proposito: borrar filas y archivos con la aplicacion escribiendo
# encima es como se consiguen las bases a medio borrar.
docker compose down

log "3/5 · Borrando datos de prueba"
docker compose run --rm --no-deps -T backend python - "${FOLIO}" <<'PY'
import sqlite3, sys

folio = sys.argv[1] if len(sys.argv) > 1 else ""
c = sqlite3.connect("data/dulceauto.db")

# SQLite NO aplica ON DELETE CASCADE si no se le activa expresamente, asi que
# los hijos se borran a mano y en orden. Confiar en el CASCADE aqui dejaria
# fotografias y snapshots huerfanos apuntando a facturas que ya no existen.
c.execute("PRAGMA foreign_keys=ON")
for tabla in ("invoice_photo", "invoice_snapshot", "activity_log", "invoice"):
    n = c.execute(f"delete from {tabla}").rowcount
    print(f"  {tabla:<18}: {n} filas borradas")

# Los contadores internos de sqlite. La tabla sqlite_sequence solo existe si
# alguna tabla usa AUTOINCREMENT, y aqui ninguna lo usa: si se borra a ciegas,
# esto revienta con "no such table" a mitad de la limpieza, con el backend ya
# parado y las filas ya borradas. Se comprueba antes.
hay_secuencia = c.execute(
    "select 1 from sqlite_master where type='table' and name='sqlite_sequence'"
).fetchone()
if hay_secuencia:
    c.execute("delete from sqlite_sequence where name in "
              "('invoice','invoice_photo','invoice_snapshot','activity_log')")

if folio:
    fila = c.execute("select value from setting where key='folio.next' and market is null").fetchone()
    if fila:
        c.execute("update setting set value=? where key='folio.next' and market is null", (folio,))
    else:
        c.execute("insert into setting (key, market, value, is_sensitive) values ('folio.next', NULL, ?, 0)", (folio,))
    print(f"  contador de folios : proximo = {folio}")

c.commit()
PY

log "4/5 · Borrando archivos de prueba"
# Los snapshots y las fotografias viven en disco, no en la base. Borrar solo
# las filas dejaria los megas ahi y el disco llenandose sin motivo.
rm -rf data/snapshots/* data/uploads/* 2>/dev/null || true
install -d data/snapshots data/uploads
echo "  snapshots y subidas vaciados"

log "5/5 · Arrancando"
docker compose up -d
sleep 10
curl -fsS http://127.0.0.1:8000/salud && echo

log "Como queda"
contar
echo
echo "  Archivos en disco:"
echo "    snapshots : $(find data/snapshots -type f 2>/dev/null | wc -l) archivos"
echo "    subidas   : $(find data/uploads   -type f 2>/dev/null | wc -l) archivos"
echo
echo "  Revision de Alembic:"
docker compose exec -T backend alembic current 2>&1 | grep -v '^INFO' | sed 's/^/    /'

cat <<FIN

  Base limpia. Se conservan la Configuracion, las contrasenas y las tres
  plantillas aprobadas.

  Lo que habia antes quedo guardado en:
      ${ANTES}

FIN

# Comprobacion final, despues de arrancar. No basta con contar las filas justo
# despues de borrarlas: lo que importa es como queda la base con la aplicacion
# ya en marcha, porque el arranque puede volver a escribir en ella.
#
# Aqui paso de verdad: la siembra inicial recreaba tres facturas de muestra
# cada vez que la tabla quedaba vacia, y el guion daba la limpieza por buena
# mientras las contaba en pantalla. Si vuelve a pasar, esto tiene que fallar.
QUEDAN=$(docker compose exec -T backend python -c \
  "import sqlite3; print(sqlite3.connect('data/dulceauto.db').execute('select count(*) from invoice').fetchone()[0])" \
  | tr -d '\r')

if [ "$QUEDAN" != "0" ]; then
  echo
  echo "FALLA: tras arrancar quedan ${QUEDAN} facturas en la base."
  echo "       La limpieza no ha servido: algo las esta recreando al arrancar."
  echo "       Revisar SEED_DEMO_INVOICES en el archivo .env (tiene que estar"
  echo "       en false o no estar) y volver a ejecutar."
  echo "       La copia de antes de limpiar sigue en: ${ANTES}"
  exit 1
fi

echo "  Comprobado tras arrancar: 0 facturas. La limpieza ha quedado firme."
echo
