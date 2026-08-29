#!/usr/bin/env bash
#
# Punto de recuperacion estable del proyecto.
#
#   cd /opt/dulceauto && ./despliegue/punto-de-recuperacion.sh release-m4-stable-2026-08-29
#
# Deja preparado todo lo necesario para reconstruir el servidor desde cero:
#
#   1. copia de PRODUCCION      base de datos, subidas, snapshots/PDF historicos
#                               y la configuracion de Docker y de nginx
#   2. copia de SECRETOS aparte el .env, que NO va en la copia general ni a
#                               GitHub, con permisos 600
#   3. inventario               commit, revision de Alembic, fecha, recuentos y
#                               checksum de cada archivo
#   4. verificacion             la copia SE ABRE, la base pasa la comprobacion
#                               de integridad de SQLite y los recuentos leidos
#                               DE DENTRO de la copia coinciden con produccion
#
# Lo que este script NO hace, y es a proposito:
#   - no sube nada a ningun sitio;
#   - no borra ninguna copia anterior;
#   - no toca la base de datos ni el contenedor. Se puede ejecutar con el
#     sistema en marcha.
#
# La copia FUERA del VPS se hace despues, con el comando que imprime al final.

set -euo pipefail

ETIQUETA="${1:-}"
[ -n "$ETIQUETA" ] || { echo "Uso: $0 <etiqueta>   p.ej. release-m4-stable-2026-08-29" >&2; exit 1; }

# Commit de la aplicacion que se PROBO en produccion. Puede no ser el mismo que
# el del release si despues se anadieron cambios que no tocan la aplicacion
# (instrucciones, scripts de copia). Se pasa aparte y queda escrito en el
# inventario, para que dentro de unos meses se sepa exactamente que codigo de
# aplicacion se valido y que commit solo anadio documentacion.
#   COMMIT_APP=af0b243 ./despliegue/punto-de-recuperacion.sh <etiqueta>
COMMIT_APP="${COMMIT_APP:-}"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

# Se puede apuntar a otro sitio para ensayar el script sin tocar /opt.
DESTINO="${DESTINO:-/opt/dulceauto-backups/puntos}"
BASE="$DESTINO/$ETIQUETA"
GENERAL="$BASE.tar.gz"
SECRETOS="$BASE.secretos.tar.gz"
INVENTARIO="$BASE.inventario.txt"

paso() { printf "\n\033[1m== %s\033[0m\n" "$*"; }
malo() { printf "\033[31mFALLA: %s\033[0m\n" "$*" >&2; exit 1; }

sudo mkdir -p "$DESTINO"
[ -e "$GENERAL" ] && malo "ya existe $GENERAL. Usa otra etiqueta; aqui no se sobrescribe nada."

# --- 1 · datos de produccion -------------------------------------------------
paso "1 · Copia de produccion"

# La base NO se copia con tar. Va en modo WAL: las escrituras recientes estan en
# dulceauto.db-wal y no dentro del .db, asi que un tar del archivo se deja las
# ultimas. Medido en el primer ensayo: 337 facturas en la copia contra 350 en
# produccion. La copia pesaba lo suyo y se abria sin errores.
#
# Se hace con la API de copia de SQLite, que integra el WAL y deja UN archivo
# consistente con el sistema en marcha.
BASE_TMP="$(mktemp -d)"
sudo python3 "$DIR/despliegue/copia_consistente.py" \
  data/dulceauto.db "$BASE_TMP/data/dulceauto.db" | sed 's/^/   /' || \
  malo "no se ha podido hacer la copia consistente de la base"

# El .env se EXCLUYE a proposito: va en su propio archivo, con otros permisos.
# Los tres archivos de la base tambien: los sustituye la copia consistente.
SIN_COMPRIMIR="${GENERAL%.gz}"
sudo tar cf "$SIN_COMPRIMIR" \
  --exclude='./.env' \
  --exclude='./.git' \
  --exclude='data/dulceauto.db' \
  --exclude='data/dulceauto.db-wal' \
  --exclude='data/dulceauto.db-shm' \
  data docker-compose.yml Dockerfile despliegue alembic.ini 2>/dev/null || \
  malo "no se ha podido crear la copia"
# Anadir la base consistente. `tar r` solo funciona SIN comprimir, por eso se
# comprime al final y no con czf.
sudo tar rf "$SIN_COMPRIMIR" -C "$BASE_TMP" data/dulceauto.db || \
  malo "no se ha podido anadir la base a la copia"
sudo gzip -f "$SIN_COMPRIMIR" || malo "no se ha podido comprimir la copia"
sudo rm -rf "$BASE_TMP"
# nginx vive fuera del proyecto, asi que va en su propio archivo.
# (No se intenta anadirlo al general: `tar r` no funciona sobre un .tar.gz.)
if [ -f /etc/nginx/sites-available/dulceauto ]; then
  sudo tar czf "$BASE.nginx.tar.gz" -C / etc/nginx/sites-available/dulceauto
  echo "   $(sudo du -h "$BASE.nginx.tar.gz" | cut -f1)  $BASE.nginx.tar.gz"
else
  echo "   no se encuentra la configuracion de nginx en la ruta habitual"
fi
echo "   $(sudo du -h "$GENERAL" | cut -f1)  $GENERAL"

# --- 2 · secretos, aparte ----------------------------------------------------
paso "2 · Secretos, en archivo separado"
if [ -f .env ]; then
  sudo tar czf "$SECRETOS" .env
  sudo chmod 600 "$SECRETOS"
  echo "   $SECRETOS  (permisos 600)"
  echo "   NO subir este archivo a GitHub ni mezclarlo con la copia general."
else
  echo "   no hay .env que guardar"
fi

# --- 3 · recuentos, leidos de la base ----------------------------------------
paso "3 · Recuentos e inventario"
RECUENTOS_PY="$DIR/despliegue/recuentos.py"
RECUENTOS="$(sudo python3 "$RECUENTOS_PY" data/dulceauto.db)"
echo "$RECUENTOS" | sed 's/^/   /'

ARCHIVOS_SUBIDOS=$(sudo find data/uploads -type f 2>/dev/null | wc -l || echo 0)
ARCHIVOS_SNAP=$(sudo find data/snapshots -type f 2>/dev/null | wc -l || echo 0)
COMMIT="$(git rev-parse HEAD)"
COMMIT_CORTO="$(git rev-parse --short HEAD)"

{
  echo "PUNTO DE RECUPERACION · DulceAuto"
  echo "================================="
  echo
  echo "etiqueta            $ETIQUETA"
  echo "fecha               $(date -Is)"
  echo "servidor            $(hostname)"
  echo
  echo "commit del release  $COMMIT"
  echo "  (corto)           $COMMIT_CORTO"
  if [ -n "$COMMIT_APP" ]; then
    echo "commit de aplicacion $COMMIT_APP   <- el que se probo en produccion"
    echo "  diferencia entre ambos:"
    git diff --stat "$COMMIT_APP" "$COMMIT_CORTO" | sed 's/^/    /'
    # Se deja escrito que el contenido de la imagen es el mismo, no solo dicho.
    A=$(git ls-tree -r "$COMMIT_APP" --format='%(objectname) %(path)' -- app alembic alembic.ini templates_html requirements.txt Dockerfile | sha256sum | cut -d" " -f1)
    B=$(git ls-tree -r "$COMMIT_CORTO" --format='%(objectname) %(path)' -- app alembic alembic.ini templates_html requirements.txt Dockerfile | sha256sum | cut -d" " -f1)
    echo "  huella del contenido de la imagen (app, alembic, plantillas, Dockerfile):"
    echo "    $COMMIT_APP  $A"
    echo "    $COMMIT_CORTO  $B"
    [ "$A" = "$B" ] && echo "    IDENTICOS: el codigo que corre es el mismo en los dos" \
                    || echo "    DISTINTOS: OJO, el release cambia el codigo de la aplicacion"
  fi
  echo "rama                $(git rev-parse --abbrev-ref HEAD)"
  echo
  echo "$RECUENTOS"
  echo "archivos subidos   $ARCHIVOS_SUBIDOS"
  echo "archivos historico $ARCHIVOS_SNAP"
  echo
  echo "ARCHIVOS Y CHECKSUM (sha256)"
  for f in "$GENERAL" "$SECRETOS" "$BASE.nginx.tar.gz"; do
    [ -f "$f" ] || continue
    echo "  $(sudo sha256sum "$f" | cut -d' ' -f1)  $(basename "$f")  $(sudo du -h "$f" | cut -f1)"
  done
  echo
  echo "PARA RESTAURAR, EN ESTE ORDEN"
  echo "  1. git clone <repo> /opt/dulceauto && cd /opt/dulceauto && git checkout $ETIQUETA"
  echo "  2. tar xzf $(basename "$GENERAL") -C /opt/dulceauto"
  echo "  3. tar xzf $(basename "$SECRETOS") -C /opt/dulceauto     # el .env, PRIMERO"
  echo "     chmod 600 /opt/dulceauto/.env && ls -l /opt/dulceauto/.env"
  echo "     Comprobar que esta y que solo lo lee su dueno antes de seguir."
  echo "  4. Apuntar el dominio admin.mxenar.pro a la IP del servidor nuevo (registro A)"
  echo "     y esperar a que resuelva. Sin esto, certbot no puede emitir."
  echo "  5. tar xzf $(basename "$BASE").nginx.tar.gz -C /   # configuracion de nginx"
  echo "  6. sudo certbot --nginx -d admin.mxenar.pro       # emitir el certificado"
  echo "  7. sudo nginx -t && sudo systemctl reload nginx   # validar ANTES de recargar"
  echo "  8. docker compose build --build-arg COMMIT=\$(git rev-parse --short HEAD)"
  echo "     docker compose up -d"
  echo "  9. comprobar que alembic current == $(echo "$RECUENTOS" | awk '/^alembic/{print $2}')"
  echo " 10. comprobar los recuentos contra los de este inventario"
  echo
  echo "LO QUE NO VA EN LA COPIA, Y POR QUE"
  echo "  - Los certificados de /etc/letsencrypt NO se copian. Son secretos, caducan"
  echo "    cada 90 dias y se vuelven a emitir solos. En un servidor nuevo:"
  echo "        sudo certbot --nginx -d admin.mxenar.pro"
  echo "    La configuracion de nginx que si esta en la copia los referencia, asi que"
  echo "    hay que emitirlos ANTES de recargar nginx o no arrancara."
  echo "  - El .env va en su propio archivo, no en este. Sin el, el panel arranca con"
  echo "    otra clave de firma y las sesiones y la Master Password no funcionan."
} | sudo tee "$INVENTARIO" > /dev/null
echo "   $INVENTARIO"

# --- 4 · verificar la copia --------------------------------------------------
paso "4 · Verificar la copia (no basta con que exista)"
ENTRADAS=$(sudo tar tzf "$GENERAL" | wc -l) || malo "la copia general no se abre"
echo "   se abre: $ENTRADAS entradas"
[ "$ENTRADAS" -gt 0 ] || malo "la copia esta vacia"

# La base se saca de DENTRO de la copia y se comprueba ahi. Comprobar la de
# produccion no diria nada de la copia, que es lo que hay que poder restaurar.
TMP="$(mktemp -d)"
sudo tar xzf "$GENERAL" -C "$TMP" data/dulceauto.db
DENTRO=$(sudo python3 "$RECUENTOS_PY" "$TMP/data/dulceauto.db")
INTEGRIDAD=$(echo "$DENTRO" | awk '/^integridad/{print $2}')
echo "   integridad de la base DENTRO de la copia: $INTEGRIDAD"
[ "$INTEGRIDAD" = "ok" ] || malo "la base de la copia no pasa la comprobacion de integridad"
if [ "$DENTRO" = "$RECUENTOS" ]; then
  echo "   los recuentos de la copia coinciden con produccion"
else
  echo "$DENTRO" | sed 's/^/     copia: /'
  malo "los recuentos de la copia NO coinciden con produccion"
fi
sudo rm -rf "$TMP"

paso "Listo · $ETIQUETA"
sudo cat "$INVENTARIO"
cat <<TXT

FALTA LA COPIA FUERA DEL VPS. Desde tu maquina:

  scp deploy@$(hostname -I | awk '{print $1}'):$GENERAL     .
  scp deploy@$(hostname -I | awk '{print $1}'):$SECRETOS    .
  scp deploy@$(hostname -I | awk '{print $1}'):$INVENTARIO  .

Y comprueba que el checksum coincide con el del inventario:

  sha256sum $(basename "$GENERAL")

Guarda el archivo de secretos aparte del general y con acceso restringido.
TXT
