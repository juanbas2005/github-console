#!/usr/bin/env python3
"""Autodiagnóstico de aislamiento: ¿puede el comando leer el entorno del
proceso padre (el runner, que lleva el GITHUB_TOKEN)?

Imprime solo las CLAVES de las variables, nunca los valores, para no filtrar
nada aunque el entorno sea legible.
"""
import os

ppid = os.getppid()
print(f"PID propio: {os.getpid()}  PPid (runner): {ppid}")
try:
    with open(f"/proc/{ppid}/environ", "rb") as handle:
        raw = handle.read()
except PermissionError:
    print(f"✓ /proc/{ppid}/environ: PermissionError (bloqueado por Yama/ptrace)")
    print("  El token del runner NO es legible desde el comando. ✓")
    raise SystemExit(0)
except FileNotFoundError:
    print(f"✓ /proc/{ppid}/environ: no existe (aislamiento por namespace) ✓")
    raise SystemExit(0)

keys = sorted(k.split(b"=", 1)[0].decode("utf-8", "replace") for k in raw.split(b"\0") if k)
token_file = None
for item in raw.split(b"\0"):
    if item.startswith(b"CONSOLE_TOKEN_FILE="):
        token_file = item.split(b"=", 1)[1].decode()
print(f"⚠️  /proc/{ppid}/environ ES LEGIBLE ({len(keys)} variables):")
print("   GITHUB_TOKEN presente en el entorno del padre:",
      "GITHUB_TOKEN" in keys, "← debe ser False")
if token_file:
    print(f"   CONSOLE_TOKEN_FILE={token_file} ¿aún existe?: "
          f"{os.path.exists(token_file)} ← debe ser False (se borra antes de ejecutar)")
else:
    print("   CONSOLE_TOKEN_FILE: ausente del entorno del padre")
print("   Residual: /proc/<ppid>/mem (memory scraping) sigue siendo posible;")
print("   limita quién puede ejecutar (CONSOLE_ALLOWED_USERS) y qué scripts")
print("   se añaden a examples/.", )
