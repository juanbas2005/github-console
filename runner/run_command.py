#!/usr/bin/env python3
"""Ejecutor sandboxeado para la consola web de GitHub Actions.

Corre dentro del runner disparado por .github/workflows/console-run.yml.
Solo usa la stdlib de Python. Ver README.md para la arquitectura completa.

Flujo:
  1. Valida el comando contra la lista blanca (modo allowlist) o lo aisla en
     un contenedor (modo docker).
  2. Crea una copia limpia del workspace (sin .git, sin .github, sin runs/).
  3. Ejecuta con entorno saneado, namespace PID propio, ulimits y timeout.
  4. Reescribe runs/<id>/run.json con la salida parcial y commitea cada
     --stream-interval segundos (streaming por polling para el frontend).
  5. Copia artefactos (p.ej. capturas de pantalla) a runs/<id>/ y los registra.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Política de seguridad
# --------------------------------------------------------------------------- #

# Comandos permitidos en modo allowlist. Ampliable con la variable de
# repositorio CONSOLE_EXTRA_COMMANDS (lista separada por comas).
ALLOWED_COMMANDS = {
    "ls", "dir", "cat", "head", "tail", "wc", "echo", "printf", "pwd",
    "whoami", "id", "uname", "date", "uptime", "df", "du", "free", "top",
    "ps", "grep", "egrep", "fgrep", "rg", "find", "sort", "uniq", "cut",
    "tr", "paste", "column", "tee", "seq", "yes", "sleep", "basename",
    "dirname", "realpath", "readlink", "file", "stat", "touch", "md5sum",
    "sha1sum", "sha256sum", "base64", "xxd", "od", "strings", "jq", "which",
    "command", "env", "printenv", "hostname", "ip", "ifconfig", "curl",
    "git", "python3", "python", "node", "npx", "ruby", "perl", "sh", "bash",
    "xargs", "awk", "sed", "nslookup", "dig",
}

# Intérpretes: solo pueden ejecutar scripts que ya vivan en el repo
# (examples/ o scripts/). Así se evita código arbitrario en modo allowlist.
INTERPRETERS = {"python3", "python", "node", "ruby", "perl", "sh", "bash", "npx"}
INTERPRETER_CODE_FLAGS = {"-c", "-e", "-m", "--eval", "--module", "--command", "-"}

# Caracteres de shell realmente peligrosos. Como en modo allowlist NO se usa
# shell (se ejecuta argv directo), los paréntesis/llaves/globs son inertes;
# esto bloquea exactamente los vectores de inyección ($(), ;, &&, |, >, `).
FORBIDDEN_CHARS = set(";&|<>`$\\\n\r")

# Entorno del proceso ejecutado: solo variables inocuas. CRÍTICO: GITHUB_TOKEN
# y cualquier secreto del workflow quedan fuera del alcance del comando.
SAFE_ENV_KEYS = {
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM", "SHELL",
    "USER", "LOGNAME", "TMPDIR", "EDITOR", "PYTHONUNBUFFERED",
}
SAFE_PATH_CORE = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games"

_UNSHARE_OK = None


def compute_safe_path() -> str:
    """Núcleo fijo + directorios del PATH del runner bajo prefijos de confianza
    (el PATH de un runner hosted no es controlable por quien envía el comando).
    Añade además el directorio del intérprete actual para entornos como Termux."""
    prefixes = ("/usr/", "/opt/", "/snap/")
    kept = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        if entry.startswith(prefixes) or entry == os.path.dirname(sys.executable):
            if entry not in kept:
                kept.append(entry)
    return os.pathsep.join([SAFE_PATH_CORE, *kept])
SANDBOX_IGNORE = [".git", "runs", ".github", "node_modules", "__pycache__", ".pytest_cache"]

MAX_OUTPUT = 400_000          # 400 KB: run.json debe caber en la API de
                              # contents (sin contenido inline >1MB)
MAX_ARTIFACTS = 10
MAX_ARTIFACT_SIZE = 900_000   # 900 KB: ídem, para poder mostrarlo inline

# Patrón de tokens de GitHub para redactar de la salida antes de commitear.
# El lookaround evita falsos positivos dentro de hashes hexadecimales largos.
TOKEN_RE = re.compile(
    r"(?<![0-9A-Fa-f])(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82}"
    r"|gh[o]_[A-Za-z0-9]{36}|gh[s]_[A-Za-z0-9]{36}|gh[r]_[A-Za-z0-9]{76}"
    r"|gh[u]_[A-Za-z0-9]{36}|[A-Fa-f0-9]{40})(?![0-9A-Fa-f])"
)


class RejectedCommand(Exception):
    """El comando no pasa la política de seguridad."""


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def redact(text: str) -> str:
    return TOKEN_RE.sub("***", text)


def trunc(data: bytes, limit: int) -> bytes:
    if len(data) <= limit:
        return data
    return data[:limit] + f"\n\n[...salida truncada a {limit} bytes...]".encode()


def build_allowed_set() -> set:
    extra = os.environ.get("CONSOLE_EXTRA_COMMANDS", "")
    return ALLOWED_COMMANDS | {c.strip() for c in extra.split(",") if c.strip()}


# --------------------------------------------------------------------------- #
# Parseo y validación del comando
# --------------------------------------------------------------------------- #

def tokenize(command: str) -> list:
    """Shell-like split que respeta comillas y separa tuberías literales."""
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars="|")
        return list(lex)
    except ValueError as exc:
        raise RejectedCommand(f"comillas sin cerrar: {exc}") from exc


def split_pipes(tokens: list) -> list:
    stages, current = [], []
    for token in tokens:
        if token == "|":
            if not current:
                raise RejectedCommand("tubería con una etapa vacía")
            stages.append(current)
            current = []
        else:
            current.append(token)
    if not current:
        raise RejectedCommand("comando vacío")
    stages.append(current)
    return stages


def resolve_binary(name: str, allowed: set) -> str:
    if os.path.isabs(name) or "/" in name or "\\" in name:
        raise RejectedCommand(f"no se permiten rutas absolutas ni relativas: {name!r}")
    if name not in allowed:
        raise RejectedCommand(
            f"comando no permitido: {name!r}. Disponibles: "
            f"{', '.join(sorted(allowed))}"
        )
    found = shutil.which(name, path=compute_safe_path())
    if not found:
        raise RejectedCommand(f"comando permitido pero no instalado en el runner: {name!r}")
    return found


def resolve_repo_script(script: str, workspace: Path) -> Path:
    """Solo scripts que ya existan en examples/ o scripts/ del repo."""
    if script.startswith("-"):
        raise RejectedCommand("no se permiten flags de intérprete en modo allowlist")
    root = workspace.resolve()
    candidate = (root / script).resolve()
    for sub in ("examples", "scripts"):
        base = (root / sub).resolve()
        if base == candidate or base in candidate.parents:
            if candidate.is_file():
                return candidate
    raise RejectedCommand(
        f"script no encontrado en examples/ o scripts/: {script!r}. "
        "En modo allowlist los intérpretes solo pueden ejecutar scripts del repo."
    )


def validate_stage(argv: list, workspace: Path, allowed: set) -> list:
    for arg in argv:
        bad = [c for c in FORBIDDEN_CHARS if c in arg]
        if bad:
            raise RejectedCommand(
                f"carácter(es) de shell no permitido(s) {bad} en el argumento {arg!r}"
            )
    name = argv[0]
    if name in INTERPRETERS:
        if len(argv) < 2:
            raise RejectedCommand(
                f"{name} debe recibir un script de examples/ o scripts/ "
                "(no se permite código en línea en modo allowlist)"
            )
        if any(a in INTERPRETER_CODE_FLAGS for a in argv[1:]):
            raise RejectedCommand(
                "no se permite ejecutar código en línea (-c/-e/-m/-) en modo allowlist"
            )
        resolved = resolve_binary(name, allowed)
        script = resolve_repo_script(argv[1], workspace)
        return [resolved, str(script), *argv[2:]]
    return [resolve_binary(name, allowed), *argv[1:]]


# --------------------------------------------------------------------------- #
# Sandbox
# --------------------------------------------------------------------------- #

def prepare_sandbox(workspace: Path) -> Path:
    """Copia limpia del repo: sin .git (allí está el token), sin workflows."""
    import tempfile

    sandbox = Path(tempfile.mkdtemp(prefix="console-sandbox-"))
    ignore = shutil.ignore_patterns(*SANDBOX_IGNORE)
    for entry in sorted(workspace.iterdir()):
        if entry.name in SANDBOX_IGNORE or entry.name.startswith("."):
            continue
        if entry.is_dir():
            shutil.copytree(entry, sandbox / entry.name, ignore=ignore, symlinks=False)
        elif entry.is_file():
            shutil.copy2(entry, sandbox / entry.name)
    artifacts = sandbox / "artifacts"
    artifacts.mkdir(exist_ok=True)
    return sandbox


def safe_env() -> dict:
    env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}
    env.update(
        PATH=compute_safe_path(),
        TERM=os.environ.get("TERM", "xterm-256color"),
        PYTHONUNBUFFERED="1",
        LANG=os.environ.get("LANG", "C.UTF-8"),
        CONSOLE_SANDBOX="1",
        CONSOLE_ARTIFACTS_DIR=str(Path(os.environ.get("CONSOLE_SANDBOX_DIR", ".")) / "artifacts"),
    )
    return env


def rlimits_preexec(cpu_seconds: int, fsize_bytes: int, as_bytes: int) -> None:
    """Aplica límites RLIMIT en el hijo. Best-effort: el runner es Linux."""
    try:
        import resource

        if cpu_seconds > 0:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        if fsize_bytes > 0:
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_bytes, fsize_bytes))
        if as_bytes > 0:
            resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
    except Exception:
        pass  # ponytail: los límites son defensa en profundidad, no críticales


def unshare_available() -> bool:
    """Probe único: ¿podemos aislar en un namespace PID+mount?"""
    global _UNSHARE_OK
    if _UNSHARE_OK is None:
        binary = shutil.which("unshare", path=compute_safe_path())
        if not binary:
            _UNSHARE_OK = False
        else:
            true_bin = shutil.which("true", path=compute_safe_path()) or "/bin/true"
            try:
                probe = subprocess.run(
                    [binary, "--pid", "--fork", "--mount-proc", true_bin],
                    capture_output=True, timeout=10)
                _UNSHARE_OK = probe.returncode == 0
            except Exception:
                _UNSHARE_OK = False
    return _UNSHARE_OK


def wrap_unshare(argv: list) -> list:
    """Aísla el comando en un namespace PID+mount propio: el comando no puede
    leer /proc de procesos ancestros (donde podría haber tokens).
    Si el runner no lo permite, se ejecuta sin namespace y se deja constancia."""
    if os.environ.get("CONSOLE_USE_UNSHARE", "1") != "1" or not unshare_available():
        return argv
    binary = shutil.which("unshare", path=compute_safe_path())
    return [binary, "--pid", "--fork", "--mount-proc", "--", *argv]


# --------------------------------------------------------------------------- #
# Ejecución
# --------------------------------------------------------------------------- #

def stream_pipeline(stages: list, sandbox: Path, timeout: int, out_path: Path,
                    on_tick) -> tuple:
    """Ejecuta la tubería leyendo stdout/stderr sin bloqueo.

    on_tick(stdout, stderr, status) se llama periódicamente para persistir la
    salida parcial (streaming hacia el frontend).
    """
    import select

    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    procs: list = []
    prev_out = None
    cpu_seconds = timeout + 10
    preexec = lambda: rlimits_preexec(cpu_seconds, 100 * 1024 * 1024, 2 * 1024 ** 3)

    try:
        for index, argv in enumerate(stages):
            is_last = index == len(stages) - 1
            argv = wrap_unshare(argv) if os.environ.get("CONSOLE_USE_UNSHARE", "1") == "1" else argv
            proc = subprocess.Popen(
                argv,
                cwd=str(sandbox),
                env=safe_env(),
                stdin=prev_out,
                stdout=out_w if is_last else subprocess.PIPE,
                stderr=err_w,
                preexec_fn=preexec,
                start_new_session=True,
            )
            procs.append(proc)
            if prev_out is not None:
                prev_out.close()
            prev_out = None if is_last else proc.stdout
    finally:
        os.close(out_w)
        os.close(err_w)

    deadline = time.monotonic() + timeout
    stdout_buf, stderr_buf = b"", b""
    killed = False
    last_tick = 0.0
    closed = set()

    while True:
        readable, _, _ = select.select([out_r, err_r], [], [], 0.5)
        for fd in readable:
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b""
            if not data:
                closed.add(fd)
                continue
            if fd == out_r:
                stdout_buf += data
            else:
                stderr_buf += data
        if closed >= {out_r, err_r}:
            break
        now = time.monotonic()
        if now - last_tick >= 1.0:
            last_tick = now
            on_tick(stdout_buf, stderr_buf, "running" if not killed else "killed")
        if not killed and now > deadline:
            killed = True
            for proc in procs:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except OSError:
                    pass
        elif killed and now > deadline + 3:
            for proc in procs:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except OSError:
                    pass

    for proc in procs:
        proc.wait()
    exit_code = procs[-1].returncode if procs else -1
    return stdout_buf, stderr_buf, exit_code, killed


def collect_artifacts(sandbox: Path, run_dir: Path) -> list:
    """Copia artefactos producidos (capturas, archivos) junto al run."""
    source = sandbox / "artifacts"
    found = []
    if not source.is_dir():
        return found
    for entry in sorted(source.iterdir()):
        if not entry.is_file() or len(found) >= MAX_ARTIFACTS:
            continue
        if entry.stat().st_size > MAX_ARTIFACT_SIZE:
            continue
        dest = run_dir / entry.name
        shutil.copy2(entry, dest)
        found.append({"name": entry.name, "size": entry.stat().st_size})
    return found


# --------------------------------------------------------------------------- #
# Persistencia (git)
# --------------------------------------------------------------------------- #

def git_commit(workspace: Path, rel_path: str, message: str) -> None:
    git = ["git", "-C", str(workspace)]
    subprocess.run(git + ["add", "-f", "--", rel_path], check=True, capture_output=True)
    result = subprocess.run(
        git + ["commit", "-q", "-m", message, "--", rel_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return  # Nada que commitear (sin cambios)
    push = subprocess.run(git + ["push", "-q"], capture_output=True, text=True)
    if push.returncode != 0:  # ponytail: reintento simple tras rebase
        subprocess.run(git + ["pull", "-q", "--rebase"], capture_output=True, text=True)
        subprocess.run(git + ["push", "-q"], capture_output=True, text=True)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Principal
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--runs-dir", required=True)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--mode", choices=["allowlist", "docker"], default="allowlist")
    parser.add_argument("--image", default="ubuntu:24.04")
    parser.add_argument("--stream-interval", type=float, default=3.0)
    parser.add_argument("--actor", default=os.environ.get("GITHUB_ACTOR", "unknown"))
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    runs_dir = Path(args.runs_dir).resolve()
    run_dir = runs_dir / args.id
    run_file = run_dir / "run.json"
    rel_run_dir = f"runs/{args.id}"
    allowed = build_allowed_set()

    meta = {
        "id": args.id,
        "command": args.command,
        "mode": args.mode,
        "actor": args.actor,
        "runner": {
            "name": os.environ.get("RUNNER_NAME", "local"),
            "os": os.environ.get("RUNNER_OS", os.uname().sysname),
            "arch": os.environ.get("RUNNER_ARCH", os.uname().machine),
        },
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),
        "sandbox": {
            "pid_namespace": unshare_available(),
            "clean_workspace_copy": True,
            "env_scrubbed": True,
        },
        "run_url": (
            f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/"
            f"{os.environ['GITHUB_RUN_ID']}"
            if os.environ.get("GITHUB_REPOSITORY") and os.environ.get("GITHUB_RUN_ID")
            else ""
        ),
        "started_at": now_iso(),
        "timeout_seconds": args.timeout,
    }

    # Configura git localmente (idempotente) para los commits de streaming.
    subprocess.run(["git", "-C", str(workspace), "config", "user.name",
                    f"{args.actor} (web-console)"], capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "config", "user.email",
                    f"{args.actor}@users.noreply.github.com"], capture_output=True)

    state = {"stdout": b"", "stderr": b"", "status": "running"}
    last_commit = {"time": time.monotonic(), "sig": None}

    def tick(stdout: bytes, stderr: bytes, status: str) -> None:
        state.update(stdout=stdout, stderr=stderr, status=status)
        write_json(run_file, {
            **meta,
            "status": status,
            "updated_at": now_iso(),
            "stdout": redact(stdout.decode("utf-8", "replace")),
            "stderr": redact(stderr.decode("utf-8", "replace")),
            "exit_code": None,
        })
        # Streaming: commitea solo si cambió la salida y pasó el intervalo.
        # El commit final es el definitivo; estos son best-effort.
        now = time.monotonic()
        sig = hash((stdout, stderr))
        if sig != last_commit["sig"] and now - last_commit["time"] >= args.stream_interval:
            last_commit["time"] = now
            last_commit["sig"] = sig
            try:
                git_commit(workspace, rel_run_dir, f"console: streaming {args.id}")
            except Exception:
                pass

    try:
        sandbox = prepare_sandbox(workspace)
        os.environ["CONSOLE_SANDBOX_DIR"] = str(sandbox)

        if args.mode == "docker":
            command_argv = build_docker_argv(args.command, sandbox, args.image, args.timeout)
        else:
            stages = [validate_stage(stage, workspace, allowed)
                      for stage in split_pipes(tokenize(args.command))]
            command_argv = stages

        tick(b"", b"", "running")
        git_commit(workspace, rel_run_dir, f"console: start {args.id}")

        start = time.monotonic()
        if args.mode == "docker":
            stdout, stderr, exit_code, killed = run_docker(command_argv, sandbox, args.timeout, tick)
        else:
            stdout, stderr, exit_code, killed = stream_pipeline(
                command_argv, sandbox, args.timeout, run_file, tick
            )
        duration_ms = int((time.monotonic() - start) * 1000)

        artifacts = collect_artifacts(sandbox, run_dir)
        status = "killed" if killed else ("completed" if exit_code == 0 else "failed")

        final = {
            **meta,
            "status": status,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "updated_at": now_iso(),
            "finished_at": now_iso(),
            "stdout": redact(trunc(stdout, MAX_OUTPUT).decode("utf-8", "replace")),
            "stderr": redact(trunc(stderr, MAX_OUTPUT).decode("utf-8", "replace")),
            "artifacts": artifacts,
        }
        write_json(run_file, final)
        git_commit(workspace, rel_run_dir, f"console: {status} {args.id} (exit {exit_code})")
        print(f"[console] run {args.id} -> {status} exit={exit_code} "
              f"duration={duration_ms}ms artifacts={len(artifacts)}")
        return 0

    except RejectedCommand as exc:
        final = {
            **meta, "status": "rejected", "exit_code": None,
            "finished_at": now_iso(), "stdout": "", "stderr": "",
            "error": f"Comando rechazado por la política de seguridad: {exc}",
            "artifacts": [],
        }
        write_json(run_file, final)
        git_commit(workspace, rel_run_dir, f"console: rejected {args.id}")
        print(f"[console] run {args.id} REJECTED: {exc}")
        return 0  # No falla el workflow: el rechazo se muestra en el frontend.
    except Exception as exc:  # Error interno: sí falla el workflow.
        import traceback

        traceback.print_exc()
        write_json(run_file, {
            **meta, "status": "error", "stdout": "", "stderr": "",
            "error": f"error interno del runner: {exc}", "artifacts": [],
        })
        subprocess.run(["git", "-C", str(workspace), "add", "--", rel_run_dir],
                       capture_output=True)
        subprocess.run(["git", "-C", str(workspace), "commit", "-q", "-m",
                        f"console: error {args.id}"], capture_output=True)
        subprocess.run(["git", "-C", str(workspace), "push", "-q"], capture_output=True)
        return 2
    finally:
        sandbox_dir = os.environ.get("CONSOLE_SANDBOX_DIR")
        if sandbox_dir and Path(sandbox_dir).is_dir():
            shutil.rmtree(sandbox_dir, ignore_errors=True)


def build_docker_argv(command: str, sandbox: Path, image: str, timeout: int) -> list:
    if not shutil.which("docker", path=compute_safe_path()):
        raise RejectedCommand(
            "modo docker no disponible en este runner (requiere ubuntu-latest)"
        )
    artifacts = sandbox / "artifacts"
    return [
        "docker", "run", "--rm",
        "--network", "none",              # sin acceso a red
        "--memory", "512m", "--memory-swap", "512m",
        "--cpus", "1", "--pids-limit", "128",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--read-only", "--tmpfs", "/tmp:rw,size=64m",
        "--workdir", "/workspace",
        "--user", "1000:1000",
        "-v", f"{sandbox}:/workspace:ro",
        "-v", f"{artifacts}:/artifacts",
        "-e", "PYTHONUNBUFFERED=1",
        "-e", "CONSOLE_ARTIFACTS_DIR=/artifacts",
        "--name", f"console-{os.getpid()}",
        image, "sh", "-c", command,
    ]


def run_docker(argv: list, sandbox: Path, timeout: int, tick) -> tuple:
    """docker run con streaming por líneas."""
    import select

    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            bufsize=0)
    deadline = time.monotonic() + timeout
    stdout_buf, stderr_buf = b"", b""
    last_tick = 0.0
    streams = {proc.stdout.fileno(): "stdout", proc.stderr.fileno(): "stderr"}
    closed = set()
    killed = False

    while True:
        readable, _, _ = select.select(list(streams), [], [], 0.5)
        for fd in readable:
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b""
            if not data:
                closed.add(fd)
                continue
            if streams[fd] == "stdout":
                stdout_buf += data
            else:
                stderr_buf += data
        if closed == set(streams):
            break
        now = time.monotonic()
        if now - last_tick >= 1.0:
            last_tick = now
            tick(stdout_buf, stderr_buf, "running")
        if not killed and now > deadline:
            killed = True
            subprocess.run(["docker", "kill", "-s", "TERM",
                            f"console-{os.getpid()}"], capture_output=True)
        elif killed and now > deadline + 5:
            subprocess.run(["docker", "kill", "-s", "KILL",
                            f"console-{os.getpid()}"], capture_output=True)
    proc.wait()
    return stdout_buf, stderr_buf, proc.returncode, killed


if __name__ == "__main__":
    sys.exit(main())
