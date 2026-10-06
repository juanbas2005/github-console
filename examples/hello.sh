#!/usr/bin/env bash
# Ejemplo clásico: saludo + info del entorno sandboxeado.
echo "¡Hola desde GitHub Actions!"
echo "---------------------------"
echo "Fecha      : $(date)"
echo "Usuario    : $(whoami)"
echo "Directorio : $(pwd)"
echo "Hostname   : $(hostname)"
echo "Argumentos : $*"
echo
echo "Variables CONSOLE_* del sandbox:"
env | grep -E '^CONSOLE_' | sort
