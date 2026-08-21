#!/usr/bin/env bash
#
# Restauracion de una copia de seguridad.
#
#   bash restaurar.sh                                   comprueba la ultima copia
#   bash restaurar.sh /opt/dulceauto-backups/datos-....tar.gz
#   bash restaurar.sh <archivo> --en-serio              restaura de verdad
#
# Una copia que no se ha restaurado nunca no es una copia de seguridad: es un
# archivo que parece una copia. Este script sirve para las dos cosas.
#
# Sin --en-serio NO toca nada: descomprime en una carpeta temporal, comprueba
# que la base de datos se abre, que no esta corrupta y cuantas facturas,
# fotografias y snapshots trae. Es la comprobacion que hay que hacer de vez en
# cuando, y es inofensiva.
#
# Con --en-serio sustituye los datos en marcha. Antes de hacerlo guarda una
# copia de lo que habia, para que la propia restauracion se pueda deshacer.
set -euo pipefail

BACKUPS="${DULCEAUTO_BACKUPS:-/opt/dulceauto-backups}"
PROYECTO="${DULCEAUTO_PROYECTO:-/opt/dulceauto/backend}"

ARCHIVO="${1:-}"
MODO="${2:-}"
if [ -z "$ARCHIVO" ] || [ "$ARCHIVO" = "--en-serio" ]; then
  MODO="${ARCHIVO:-}"
  ARCHIVO=$(ls -1t "${BACKUPS}"/datos-*.tar.gz 2>/dev/null | head -1 || true)
fi
[ -n "$ARCHIVO" ] || { echo "No hay ninguna copia en ${BACKUPS}."; exit 1; }
[ -f "$ARCHIVO" ] || { echo "No existe: ${ARCHIVO}"; exit 1; }

log() { printf "\n\033[1;34m==> %s\033[0m\n" "$*"; }

log "Copia: $(basename "$ARCHIVO")  ($(du -h "$ARCHIVO" | cut -f1), $(date -r "$ARCHIVO" '+%d/%m/%Y %H:%M'))"

TEMPORAL=$(mktemp -d)
trap 'rm -rf "$TEMPORAL"' EXIT
tar -xzf "$ARCHIVO" -C "$TEMPORAL"

BASE="${TEMPORAL}/data/dulceauto.db"
[ -f "$BASE" ] || { echo "FALLA: la copia no lleva la base de datos."; exit 1; }

log "Comprobacion del contenido"
# integrity_check es lo que dice si el archivo esta sano de verdad. Que abra no
# basta: una base corrupta tambien abre.
SALUD=$(sqlite3 "$BASE" "PRAGMA integrity_check;")
echo "  integridad          : ${SALUD}"
[ "$SALUD" = "ok" ] || { echo "FALLA: la base de datos esta danada."; exit 1; }

FACTURAS=$(sqlite3 "$BASE" "SELECT COUNT(*) FROM invoice;")
SNAPSHOTS=$(sqlite3 "$BASE" "SELECT COUNT(*) FROM invoice_snapshot;")
AJUSTES=$(sqlite3 "$BASE" "SELECT COUNT(*) FROM setting;")
FOTOS=$(find "${TEMPORAL}/data/uploads" -type f 2>/dev/null | wc -l)
PDFS=$(find "${TEMPORAL}/data/snapshots" -name '*.pdf' 2>/dev/null | wc -l)

echo "  facturas            : ${FACTURAS}"
echo "  snapshots anotados  : ${SNAPSHOTS}"
echo "  PDF en disco        : ${PDFS}"
echo "  archivos subidos    : ${FOTOS}"
echo "  ajustes             : ${AJUSTES}"

# Un snapshot anotado en la base cuyo PDF no este en disco significa que la
# copia esta incompleta, y es justo lo que no se ve mirando el tamano del
# archivo.
if [ "$SNAPSHOTS" -gt 0 ] && [ "$PDFS" -eq 0 ]; then
  echo "  AVISO: hay snapshots anotados pero ningun PDF en la copia."
fi

if [ "$MODO" != "--en-serio" ]; then
  cat <<FIN

  La copia se abre y esta completa. No se ha tocado nada del servidor.

  Para restaurarla de verdad:
      bash restaurar.sh "${ARCHIVO}" --en-serio

FIN
  exit 0
fi

log "RESTAURACION REAL"
read -r -p "Se van a sustituir los datos en marcha. Escriba SI para continuar: " RESPUESTA
[ "$RESPUESTA" = "SI" ] || { echo "Cancelado."; exit 1; }

cd "$PROYECTO"
log "1/4 · Parando el backend"
docker compose down

log "2/4 · Guardando lo que hay ahora"
ANTES="${BACKUPS}/antes-de-restaurar-$(date +%F-%H%M).tar.gz"
tar -czf "$ANTES" -C "$PROYECTO" data
echo "  guardado en ${ANTES}"

log "3/4 · Restaurando"
rm -rf "${PROYECTO}/data"
cp -a "${TEMPORAL}/data" "${PROYECTO}/data"

log "4/4 · Arrancando"
docker compose up -d
sleep 8
curl -fsS http://127.0.0.1:8000/salud && echo

cat <<FIN

  Restaurado desde $(basename "$ARCHIVO").
  Lo que habia antes quedo guardado en:
      ${ANTES}

FIN
