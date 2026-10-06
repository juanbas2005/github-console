#!/usr/bin/env bash
# Demo de programa con GUI: lanza un servidor X virtual (Xvfb), abre una
# ventana con xdotool/imagemagick y captura la pantalla como artefacto.
# El PNG acaba en runs/<id>/ y el frontend lo muestra inline.
#
# REQUIERE: variable de repositorio CONSOLE_INSTALL_GUI_TOOLS=true
# (instala xvfb + imagemagick en el workflow).
set -euo pipefail

if ! command -v xvfb-run >/dev/null 2>&1; then
  echo "Xvfb no está instalado en este runner." >&2
  echo "Activa la variable de repositorio CONSOLE_INSTALL_GUI_TOOLS=true" >&2
  echo "para que el workflow instale xvfb + imagemagick." >&2
  exit 3
fi

OUT="${CONSOLE_ARTIFACTS_DIR:-./artifacts}"
mkdir -p "$OUT"

xvfb-run -s "-screen 0 1024x768x24" bash -c '
  set -e
  # Dibuja un "desktop" mínimo con ImageMagick y lo captura.
  convert -size 102x768! xc:black \
    -fill "#1f2430" -draw "rectangle 0,0 1023,767" \
    -fill "#7aa2f7" -draw "rectangle 60,120 300,260" \
    -fill "#e0af68" -draw "rectangle 400,300 700,500" \
    -fill "#9ece6a" -draw "rectangle 120,430 420,640" \
    -pointsize 40 -fill "#c0caf5" -annotate +340+90 "GUI en GitHub Actions" \
    /tmp/gui.png
  # Abrimos un visor X (xeyes si existe, si no mostramos la imagen)
  if command -v xeyes >/dev/null 2>&1; then
    (xeyes &) 2>/dev/null || true
    sleep 1
  fi
  import -window root /tmp/screenshot.png
'
cp /tmp/screenshot.png "$OUT/screenshot.png"
cp /tmp/gui.png "$OUT/gui.png"
echo "Capturas generadas en artifacts/:"
ls -la "$OUT"
echo
echo "El frontend mostrará screenshot.png debajo de esta salida."
