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
print(f"⚠️  /proc/{ppid}/environ ES LEGIBLE ({len(keys)} variables):")
print("   claves:", ", ".join(keys))
print("   ⚠️  GITHUB_TOKEN presente en el entorno del padre:",
      "GITHUB_TOKEN" in keys)
print("   Un script del repo podría imprimir el token: limita quién puede")
print("   ejecutar (CONSOLE_ALLOWED_USERS) y qué scripts se añaden a examples/.")
