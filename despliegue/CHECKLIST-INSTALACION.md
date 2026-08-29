# Lista de comprobación de la instalación

Para repasarla juntos en el servidor. Cada punto se marca cuando se ha
**comprobado**, no cuando se ha hecho: son cosas distintas.

---

## 1 · Servidor

- [ ] Es un **VPS**, no un alojamiento web compartido.
- [ ] Usuario de despliegue con `sudo`, en lugar de trabajar como root.
- [ ] Ubuntu 22.04 o 24.04.
- [ ] Sistema actualizado.
- [ ] Cortafuegos activo: solo SSH, 80 y 443. `ufw status`
- [ ] Acceso SSH solo con clave. **Comprobado intentando entrar con contraseña
      y viendo que la rechaza.**
- [ ] Actualizaciones de seguridad automáticas activadas.

## 2 · Dominio y HTTPS

- [ ] `admin.sudominio.com` apunta a la IP del VPS (registro A).
- [ ] El certificado se emite correctamente.
- [ ] `http://` redirige a `https://`. **Comprobado escribiendo la dirección sin
      la ese.**
- [ ] El certificado se renueva solo: `certbot renew --dry-run`.
- [ ] El puerto 8000 **no** responde desde fuera. El panel solo se llega por
      nginx, con certificado.

## 3 · Backend en marcha

- [ ] El contenedor arranca y se reinicia solo si el servidor se apaga.
      `docker compose ps`
- [ ] `/salud` responde.
- [ ] El panel carga en `https://admin.sudominio.com`.
- [ ] Las migraciones están aplicadas: `alembic current`.

## 4 · Seguridad de la sesión

- [ ] `HTTPS_ONLY=true` en el `.env`.
- [ ] La cookie de sesión sale marcada como **Secure** y **HttpOnly**.
      Se comprueba en el navegador, en Herramientas de desarrollo → Aplicación
      → Cookies.
- [ ] `SECRET_KEY` es una clave nueva y aleatoria, **no** la del ejemplo.
- [ ] Sin sesión, cualquier dirección del panel lleva a la pantalla de acceso.

## 5 · Contraseñas

- [ ] Contraseña del panel cambiada. **La escribe el cliente**, no pasa por el
      chat.
- [ ] Master Password cambiada, distinta de la anterior.
- [ ] El aviso de «contraseñas iniciales sin cambiar» desaparece del panel.
- [ ] Con la contraseña vieja ya no se entra.
- [ ] Configuración sigue pidiendo la Master Password aunque la sesión del panel
      esté abierta.
- [ ] Configuración se vuelve a bloquear sola a los 15 minutos y al cerrar
      sesión.

## 6 · Datos reales

- [ ] Datos bancarios de México: banco, beneficiario, CLABE, cuenta.
- [ ] Datos bancarios de Argentina: banco, beneficiario, CBU, cuenta.
- [ ] Datos bancarios de la versión inglesa.
- [ ] Representante de cada mercado: nombre, cargo, teléfono, email, horario.
- [ ] URL base del QR con el dominio definitivo.
- [ ] Prefijo y contador de folios acordados.
- [ ] Logotipo subido, o decidido que se queda la marca del diseño aprobado.

> La CLABE y el CBU los valida el propio panel. Si alguno se rechaza, es que hay
> un dígito mal: mejor descubrirlo aquí que cuando un cliente vaya a transferir.

## 7 · Prueba completa, con los tres mercados

Para cada uno de **México**, **English** y **Argentina**:

- [ ] Crear una factura con datos reales.
- [ ] Subir las cuatro fotografías del vehículo.
- [ ] Vista previa: se ve el documento correcto, en su idioma.
- [ ] Importes con el formato de ese mercado (`$329,000.00 MXN` /
      `$329.000,00 ARS`).
- [ ] Fechas con el formato de ese mercado.
- [ ] Etiqueta de la cuenta correcta (CLABE / CBU).
- [ ] Generar el PDF.
- [ ] El PDF es A4 y **de una sola página**.
- [ ] Las fotografías del PDF son las subidas.
- [ ] El logotipo es el correcto.
- [ ] **Escanear el QR con el móvil** y comprobar que lleva a la dirección
      esperada.
- [ ] Leer el código de barras y comprobar que da el folio.

## 8 · Estados

- [ ] Borrador → Pago pendiente → Pago validado → Entrega coordinada →
      Entregada. La barra del documento avanza donde toca en cada paso.
- [ ] El titular y la línea de debajo cambian con el estado.
- [ ] En Pago pendiente el texto es exactamente el aprobado.
- [ ] Generar el PDF **no** cambia el estado de la operación.
- [ ] El aviso de vehículo comprometido salta desde Pago validado, no antes.

## 9 · Snapshots históricos

- [ ] Generar el PDF de una factura.
- [ ] Cambiar en Configuración la cuenta bancaria y el logotipo.
- [ ] **Descargar de nuevo el PDF anterior**: sigue con la cuenta y el logotipo
      de entonces.
- [ ] Generar otra vez: la versión nueva sí recoge los cambios, y las dos
      quedan descargables.
- [ ] Cambiar una fotografía y repetir la comprobación.

## 10 · Duplicar

- [ ] Duplicar una factura: nace como **borrador**, con folio propio.
- [ ] No hereda los datos del cliente original.
- [ ] Los datos bancarios de la copia son los de Configuración **de hoy**.
- [ ] La factura de origen no cambia.
- [ ] Las dos aparecen en el historial del mismo VIN.

## 11 · Comprobaciones automáticas, en el servidor

- [ ] Las diez suites, **dos veces seguidas**.
- [ ] 435 comprobaciones en verde.

## 12 · Rendimiento real

- [ ] Tiempo de un PDF.
- [ ] Tres PDF simultáneos: se hacen en cola, sin errores.
- [ ] Latencia del panel mientras se genera un PDF.
- [ ] Memoria en uso durante la generación.

> En mi máquina, con todo restringido a un solo núcleo: 2,9 s un PDF; 2,9 / 5,8
> / 8,6 s tres seguidos; panel a 3 ms de mediana y 34 ms de máximo. Aquí lo
> repetimos y comparamos: si los números salen muy peores, se sube a 2 vCPU.

## 13 · Reinicio del servidor

Un servidor se reinicia solo antes o después: por una actualización del núcleo,
por un corte de luz o porque lo reinicia el proveedor. Hay que saber ya que
vuelve solo.

- [ ] `reboot` del VPS.
- [ ] Docker arranca solo.
- [ ] El contenedor del backend vuelve a levantarse solo.
- [ ] Nginx arranca solo.
- [ ] El panel responde en `https://admin.sudominio.com` sin tocar nada.
- [ ] Cuánto tardó desde el reinicio hasta que el panel volvió a responder.

## 14 · Copias de seguridad, con restauración de verdad

Una copia que no se ha restaurado nunca no es una copia de seguridad: es un
archivo que parece una copia.

- [ ] Copia diaria programada.
- [ ] La primera copia existe.
- [ ] Se guardan 14 días y las viejas se borran solas.
- [ ] **Comprobar la copia**: `bash despliegue/restaurar.sh`
      Descomprime en una carpeta temporal, comprueba que la base no está dañada
      (`integrity_check`) y cuenta facturas, snapshots, PDF y archivos subidos.
      No toca nada del servidor.
- [ ] Los números que devuelve cuadran con lo que hay en el panel.
- [ ] **Restauración real de prueba**: generar una factura nueva después de la
      copia, restaurar con `--en-serio`, y comprobar que esa factura ha
      desaparecido y que las anteriores están intactas. Es la única forma de
      saber que la restauración funciona.
- [ ] La restauración deja guardado lo que había antes, por si hubiera que
      deshacerla.
- [ ] Volver a dejar el sistema como estaba.

## 15 · Al terminar

- [ ] Retirar mi clave SSH (de `~/.ssh/authorized_keys` del usuario de
      despliegue, y de root si también se añadió allí).
- [ ] Comprobar que ya no puedo entrar.
- [ ] Guía de trabajo para los empleados.

## 16 · Desplegar una versión nueva

Usar **`./despliegue/desplegar.sh`**. Hace los pasos en el orden correcto y se
detiene si algo no cuadra, en lugar de seguir adelante.

El orden importa y no es opcional:

1. Copia de seguridad, y **se abre** para comprobar que sirve.
2. `git pull`.
3. **Reconstruir la imagen.**
4. Migrar la base de datos.
5. **Comprobar que `alembic current` coincide con `head`.**
6. Levantar y esperar a `healthy`.
7. `/acceso` responde 200 y no hay trazas de error en el log.

- [ ] El paso 1 **abre** la copia, no sólo la crea. Una copia truncada también
      existe; existir no es servir.
- [ ] El paso 3 va **antes** del 4. `docker compose run` levanta un contenedor
      de la **imagen que ya existe**: si se migra sin reconstruir, ese contenedor
      es el de la versión anterior y **ni siquiera tiene dentro el archivo de la
      migración nueva**. Alembic termina diciendo que ha ido bien y no migra
      nada. Pasó el 29-ago-2026 desplegando el Milestone 4 y no dio ningún
      error: la única señal era que faltaba la línea `Running upgrade`.
- [ ] El paso 3b comprueba que la imagen **es la de ese commit**, leyendo la
      variable `DULCEAUTO_COMMIT` que se graba dentro al construirla. El mismo
      error se repitió dos veces el 29-ago-2026: una migración que dijo que
      había terminado sin migrar nada, y un script que se cortó a medias porque
      una constante nueva no existía dentro del contenedor. Con esta
      comprobación deja de depender de que alguien se acuerde.
- [ ] El paso 5 no se salta nunca, y **no mira la salida de la migración**: le
      pregunta a la base. Era esa salida la que decía "terminado" cuando no
      había migrado nada. Si `current` y `head` no coinciden, el script **no
      levanta el servicio**.
- [ ] Si algo falla, volver atrás con
      `./despliegue/restaurar.sh <la copia que imprimió el paso 1>`.
- [ ] Una migración se prueba antes sobre una **copia** de la base de
      producción, en ida **y** en vuelta, comprobando que después del rollback
      los recuentos y las rutas del histórico quedan idénticos.
