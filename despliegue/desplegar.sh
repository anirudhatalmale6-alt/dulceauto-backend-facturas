#!/usr/bin/env bash
#
# Despliegue de una version nueva, con el orden protegido.
#
#   cd /opt/dulceauto && ./despliegue/desplegar.sh
#
# POR QUE EXISTE ESTE SCRIPT
# --------------------------
# El 29-ago-2026, desplegando el Milestone 4, se ejecuto la migracion justo
# despues del `git pull` y ANTES de reconstruir la imagen. Alembic dijo que
# habia terminado y no migro nada: `docker compose run` levanta un contenedor
# de la IMAGEN QUE YA EXISTE, que era la de la version anterior y ni siquiera
# tenia dentro el archivo de la migracion nueva. No hubo ningun error; la unica
# senal era que faltaba la linea "Running upgrade".
#
# Por eso aqui el orden es fijo y no depende de que nadie se acuerde:
#
#   1. copia de seguridad, y se ABRE para comprobar que sirve
#   2. git pull
#   3. RECONSTRUIR la imagen            <- antes de migrar, siempre
#   3b. comprobar que la imagen ES la de este commit
#   4. migrar
#   5. COMPROBAR que alembic current == head   <- si no coincide, se para
#   6. levantar y esperar a healthy
#   7. comprobaciones finales
#
# Si cualquier paso falla, el script se detiene (set -e) y dice donde.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

COPIAS=/opt/dulceauto-backups
SERVICIO=backend
SELLO="$(date +%Y%m%d-%H%M%S)"

paso() { printf "\n\033[1m== %s\033[0m\n" "$*"; }
malo() { printf "\033[31mFALLA: %s\033[0m\n" "$*" >&2; exit 1; }

# --- 1 · copia de seguridad --------------------------------------------------
paso "1 · Copia de seguridad"
sudo mkdir -p "$COPIAS"
COPIA="$COPIAS/pre-despliegue-$SELLO.tar.gz"
sudo tar czf "$COPIA" data
# Crear una copia y comprobar que existe no es lo mismo que comprobar que sirve:
# un tar truncado tambien existe. Se abre y se cuentan las entradas.
ENTRADAS=$(sudo tar tzf "$COPIA" | wc -l) || malo "la copia no se puede abrir"
[ "$ENTRADAS" -gt 0 ] || malo "la copia esta vacia"
echo "   $COPIA · $ENTRADAS entradas · se abre correctamente"

# --- 2 · codigo --------------------------------------------------------------
paso "2 · Traer el codigo"
ANTES=$(git rev-parse --short HEAD)
git pull --rebase
DESPUES=$(git rev-parse --short HEAD)
echo "   $ANTES -> $DESPUES"

# --- 3 · imagen (ANTES de migrar) --------------------------------------------
paso "3 · Reconstruir la imagen"
echo "   Va antes de la migracion a proposito: 'compose run' usa la imagen que"
echo "   ya existe, y sin reconstruir la migracion nueva ni siquiera esta dentro."
docker compose build --build-arg COMMIT="$DESPUES"

# --- 3b · la imagen es la del codigo -----------------------------------------
paso "3b · Comprobar que la imagen corresponde al codigo"
EN_IMAGEN=$(docker compose run --rm "$SERVICIO" printenv DULCEAUTO_COMMIT 2>/dev/null | tr -d '\r' | tail -1 || true)
echo "   commit en disco : $DESPUES"
echo "   commit en imagen: ${EN_IMAGEN:-vacio}"
[ "$EN_IMAGEN" = "$DESPUES" ] || malo "la imagen NO es la de este commit.
   Ejecutar migraciones o scripts asi corre codigo viejo sin avisar: paso el
   29-ago-2026 dos veces. Reconstruye antes de seguir."
echo "   coinciden"

# --- 4 · migrar --------------------------------------------------------------
paso "4 · Migrar la base de datos"
docker compose run --rm "$SERVICIO" python -m alembic upgrade head

# --- 5 · comprobar la migracion ----------------------------------------------
paso "5 · Comprobar que la base quedo en la ultima revision"
# No se mira la salida del paso anterior: decia "terminado" tambien cuando no
# habia migrado nada. Se pregunta a la base.
ACTUAL=$(docker compose run --rm "$SERVICIO" python -m alembic current 2>/dev/null \
         | grep -oE '^[0-9a-f]{12}' | head -1 || true)
CABEZA=$(docker compose run --rm "$SERVICIO" python -m alembic heads 2>/dev/null \
         | grep -oE '^[0-9a-f]{12}' | head -1 || true)
echo "   current = ${ACTUAL:-vacio}"
echo "   head    = ${CABEZA:-vacio}"
[ -n "$ACTUAL" ] || malo "no se ha podido leer la revision actual de la base"
[ -n "$CABEZA" ] || malo "no se ha podido leer la ultima revision del codigo"
[ "$ACTUAL" = "$CABEZA" ] || malo "la base quedo en $ACTUAL y el codigo espera $CABEZA.
   La migracion NO se ha aplicado. NO se levanta el servicio.
   Para volver atras: ./despliegue/restaurar.sh $COPIA"
echo "   coinciden"

# --- 6 · levantar ------------------------------------------------------------
paso "6 · Levantar el servicio"
docker compose up -d
for i in $(seq 1 30); do
  ESTADO=$(docker compose ps --format '{{.Status}}' "$SERVICIO" 2>/dev/null || true)
  case "$ESTADO" in
    *healthy*) echo "   $ESTADO"; break ;;
    *unhealthy*) malo "el contenedor esta unhealthy: $ESTADO" ;;
  esac
  [ "$i" = 30 ] && malo "el contenedor no llego a healthy: $ESTADO"
  sleep 2
done

# --- 7 · comprobaciones ------------------------------------------------------
paso "7 · Comprobaciones"
CODIGO=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/acceso || true)
[ "$CODIGO" = "200" ] || malo "la pantalla de acceso devuelve $CODIGO"
echo "   /acceso responde 200"
ERRORES=$(docker compose logs --since 2m "$SERVICIO" 2>&1 | grep -ciE 'traceback' || true)
echo "   trazas de error en el arranque: $ERRORES"
[ "$ERRORES" = "0" ] || malo "hay trazas de error en el log"

paso "Desplegado: $ANTES -> $DESPUES · base en $ACTUAL"
echo "Copia de seguridad de este despliegue: $COPIA"
