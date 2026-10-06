#!/usr/bin/env bash
# Información del runner de GitHub Actions (todo de solo lectura).
echo "== Runner =="
uname -a
echo
echo "== CPU =="
nproc
echo
echo "== Memoria =="
free -h
echo
echo "== Disco =="
df -h / | tail -n +1
echo
echo "== Procesos (top 10 por CPU) =="
ps -eo pid,pcpu,pmem,comm --sort=-pcpu | head -n 11
echo
echo "== Salida del sandbox =="
echo "GITHUB_TOKEN presente: ${GITHUB_TOKEN:+SÍ (¡MAL!)}${GITHUB_TOKEN:-no}"
echo "PATH=$PATH"
