# Desplegar en Render (paso a paso)

Tu app está lista para subir a internet. Sigue estos pasos una sola vez.

## Parte A — Subir el código a GitHub

1. Entra a https://github.com y crea un repositorio nuevo
   (botón "New"). Ponle un nombre, ej: `lector-ensayos`.
   Déjalo **Público** o **Privado**, da igual. NO marques "Add README".

2. En tu Mac, abre la Terminal en la carpeta `webapp`:

       cd ~/Downloads/webapp

3. Sube los archivos (reemplaza TU-USUARIO y lector-ensayos):

       git init
       git add .
       git commit -m "Primera version"
       git branch -M main
       git remote add origin https://github.com/TU-USUARIO/lector-ensayos.git
       git push -u origin main

   (Si te pide usuario/clave, usa tu usuario de GitHub y un
   "Personal Access Token" como contraseña — GitHub ya no acepta
   la contraseña normal. Se crea en GitHub > Settings > Developer
   settings > Personal access tokens.)

## Parte B — Conectar Render

1. Entra a https://render.com y regístrate (puedes usar tu cuenta
   de GitHub para entrar, es lo más rápido).

2. Clic en **New +** > **Web Service**.

3. Conecta tu cuenta de GitHub y elige el repositorio `lector-ensayos`.

4. Render detecta la configuración automáticamente (por el archivo
   render.yaml). Verifica que diga:
   - Build Command:  pip install -r requirements.txt
   - Start Command:  gunicorn app:app --timeout 120 --workers 1
   - Plan: Free

5. Clic en **Create Web Service**.

6. Espera unos minutos mientras instala todo (verás un registro en vivo).
   Cuando termine, te da una URL pública tipo:
   https://lector-ensayos.onrender.com

   ¡Esa es tu app en internet! Cualquiera con el enlace puede usarla.

## Notas del plan gratuito

- La app "se duerme" tras 15 min sin uso. La primera visita después
  tarda ~30 segundos en despertar; luego va normal. Es normal en el
  plan gratis.
- Si necesitas que esté siempre despierta, Render tiene planes de pago
  bajos, pero para empezar el gratuito sirve.

## Actualizar la app más adelante

Cada vez que cambies algo, en la Terminal:

       cd ~/Downloads/webapp
       git add .
       git commit -m "describe el cambio"
       git push

Render detecta el cambio y actualiza la app sola en un par de minutos.

## Cambiar la plantilla Excel

La plantilla vive en  plantilla/plantilla_base.xlsx
Para cambiarla, reemplaza ese archivo, y haz git add/commit/push.
