#!/usr/bin/env python3
"""Ejemplo con salida ANSI y progreso: demuestra el streaming por polling."""
import sys
import time

COLORS = {
    "red": 31, "green": 32, "yellow": 33, "blue": 34, "magenta": 35, "cyan": 36,
}

print("Colores ANSI soportados por la consola:", flush=True)
for name, code in COLORS.items():
    print(f"  \033[{code}m{name}\033[0m", flush=True)

print("\nBarra de progreso (simula streaming):", flush=True)
for i in range(1, 21):
    bar = "█" * i + "░" * (20 - i)
    sys.stdout.write(f"\r  [{bar}] {i * 5}% ")
    sys.stdout.flush()
    time.sleep(0.25)
print("\n\nHecho. Ejecuta 'runs' para ver el historial.", flush=True)
