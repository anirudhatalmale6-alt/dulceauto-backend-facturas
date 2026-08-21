#!/usr/bin/env bash
#
# Instalacion del backend en el VPS. Ubuntu 22.04 o 24.04.
#
#   sudo bash instalar.sh admin.midominio.com correo@midominio.com
#
# Se puede ejecutar como root, pero es preferible con sudo desde un usuario de
# despliegue.
#
# El correo es el que pide Let's Encrypt para avisar si un certificado va a
# caducar sin renovarse. No se usa para nada mas.
#
# El script se puede volver a ejecutar tantas veces como haga falta: comprueba
# antes de instalar y no repite lo que ya esta. Si algo falla, se para en ese
# punto en lugar de seguir adelante dejando el servidor a medias.
set -euo pipefail

DOMINIO="${1:?Falta el dominio, por ejemplo admin.midominio.com}"
CORREO="${2:?Falta el correo para el aviso de caducidad del certificado}"
DESTINO="/opt/dulceauto"
REPO="https://github.com/anirudhatalmale6-alt/dulceauto-backend-facturas.git"

# Se puede ejecutar como root o con sudo desde un usuario de despliegue, que es
# lo recomendable. En el segundo caso el proyecto queda a nombre de ese usuario
# para que pueda trabajar despues sin sudo.
[ "$(id -u)" -eq 0 ] || { echo "Hay que ejecutarlo con sudo:  sudo bash $0 $*"; exit 1; }
OPERADOR="${SUDO_USER:-root}"

log() { printf "\n\033[1;34m==> %s\033[0m\n" "$*"; }

log "1/9 · Sistema al dia"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq ca-certificates curl git ufw nginx unattended-upgrades sqlite3

log "2/9 · Cortafuegos"
# Se abre SSH antes que nada: activar ufw sin esa regla deja el servidor
# inaccesible y hay que entrar por la consola del navegador a rescatarlo.
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
ufw status verbose

log "3/9 · Acceso SSH mas seguro"
# Sin contrasena: solo con clave. Se comprueba que haya al menos una autorizada
# -del usuario que ejecuta esto o de root- antes de desactivar la contrasena,
# para no quedarse fuera del propio servidor.
CLAVES_OPERADOR="$(getent passwd "$OPERADOR" | cut -d: -f6)/.ssh/authorized_keys"
if [ -s /root/.ssh/authorized_keys ] || [ -s "$CLAVES_OPERADOR" ]; then
  sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
  sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
  systemctl reload ssh || systemctl reload sshd
  echo "Acceso por contrasena desactivado."
else
  echo "AVISO: no hay ninguna clave autorizada, ni en /root/.ssh/authorized_keys"
  echo "ni en ${CLAVES_OPERADOR}."
  echo "No se desactiva el acceso por contrasena: hacerlo ahora dejaria el"
  echo "servidor inaccesible para todos."
fi

log "4/9 · Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
docker --version
# Docker arranca solo tras un reinicio del servidor. Sin esto, el panel no
# vuelve hasta que alguien entre a levantarlo a mano.
systemctl enable --now docker
if [ "$OPERADOR" != "root" ]; then
  usermod -aG docker "$OPERADOR"
  echo "Usuario ${OPERADOR} anadido al grupo docker (tendra efecto al volver a entrar)."
fi

log "5/9 · Codigo"
if [ -d "$DESTINO/.git" ]; then
  git -C "$DESTINO" pull --ff-only
else
  git clone --depth 1 "$REPO" "$DESTINO"
fi
[ "$OPERADOR" = "root" ] || chown -R "$OPERADOR":"$OPERADOR" "$DESTINO"
cd "$DESTINO/backend"

log "6/9 · Configuracion"
if [ ! -f .env ]; then
  cp .env.example .env
  # Clave de firma nueva y aleatoria. La del ejemplo no vale: si se filtrara,
  # se podrian falsificar sesiones del panel.
  CLAVE=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${CLAVE}|" .env
  sed -i "s|^HTTPS_ONLY=.*|HTTPS_ONLY=true|" .env
  echo "Creado .env con clave de firma nueva y cookies solo por HTTPS."
else
  echo ".env ya existe, no se toca."
fi

log "7/9 · Arranque del backend"
docker compose up -d --build
sleep 8
docker compose ps
curl -fsS http://127.0.0.1:8000/salud && echo

log "8/9 · Nginx y HTTPS"
cat > /etc/nginx/sites-available/dulceauto <<NGINX
server {
    listen 80;
    server_name ${DOMINIO};

    # El PDF y las fotografias pueden pesar unos megas.
    client_max_body_size 16m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        # El backend usa esta cabecera para registrar la IP real en Actividad.
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        # Generar un PDF puede tardar unos segundos en un VPS de 1 nucleo.
        proxy_read_timeout 120s;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/dulceauto /etc/nginx/sites-enabled/dulceauto
# El sitio por defecto responde a cualquier dominio y se adelanta al nuestro.
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

apt-get install -y -qq certbot python3-certbot-nginx
certbot --nginx -d "${DOMINIO}" --non-interactive --agree-tos -m "${CORREO}" --redirect
systemctl reload nginx

# nginx tambien tiene que volver solo despues de un reinicio.
systemctl enable nginx

log "9/9 · Copias de seguridad"
install -d /opt/dulceauto-backups
cat > /usr/local/bin/dulceauto-backup <<'BACKUP'
#!/usr/bin/env bash
# Copia la carpeta de datos: base, fotografias y snapshots.
set -euo pipefail
FECHA=$(date +%F-%H%M)
DESTINO=/opt/dulceauto-backups
tar -czf "${DESTINO}/datos-${FECHA}.tar.gz" -C /opt/dulceauto/backend data
# Se guardan 14 dias. Sin esto el disco se llena y el fallo aparece un dia
# cualquiera, al generar un PDF.
find "${DESTINO}" -name 'datos-*.tar.gz' -mtime +14 -delete
BACKUP
chmod +x /usr/local/bin/dulceauto-backup
cat > /etc/cron.d/dulceauto-backup <<'CRON'
30 3 * * * root /usr/local/bin/dulceauto-backup
CRON
/usr/local/bin/dulceauto-backup
ls -lh /opt/dulceauto-backups | tail -3
# Una copia recien hecha que no se puede abrir no sirve de nada, y es mejor
# saberlo ahora que el dia que haga falta restaurarla.
bash "${DESTINO}/backend/despliegue/restaurar.sh"

log "Listo"
cat <<FIN

  Panel:      https://${DOMINIO}
  Codigo:     ${DESTINO}
  Datos:      ${DESTINO}/backend/data      (base, fotos y snapshots)
  Copias:     /opt/dulceauto-backups       (diaria a las 3:30, se guardan 14)

  Comprobar la ultima copia:  bash despliegue/restaurar.sh
  Restaurarla de verdad:      bash despliegue/restaurar.sh <archivo> --en-serio

  Pendiente y a proposito:
   - cambiar las dos contrasenas desde Configuracion;
   - poner los datos bancarios reales de cada mercado;
   - poner la URL definitiva del QR.

FIN
