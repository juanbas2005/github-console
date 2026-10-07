# ⬡ github-console — Terminal web sobre GitHub Actions (sin servidor propio)

Una página estática (GitHub Pages) que funciona como consola: escribes un
comando, se ejecuta **de verdad en un runner de GitHub Actions** y la salida
vuelve a la página. No necesitas ningún servidor propio: todo vive en GitHub
(Pages + Actions + API REST).

![flujo](https://img.shields.io/badge/arquitectura-Pages%20%E2%86%92%20API%20%E2%86%92%20Actions%20%E2%86%92%20git-blue)

---

## 1. Cómo funciona (arquitectura)

GitHub Actions **no ofrece streaming bidireccional ni conexiones entrantes**
hacia el runner. Por eso la única arquitectura 100 % GitHub es **disparo por
API + polling de un archivo commiteado**:

```
┌────────────────────────────┐        ┌─────────────────────────────────────┐
│  Navegador (GitHub Pages)  │        │            GitHub                   │
│  web/index.html · app.js   │        │                                     │
│                            │  ①  POST /repos/O/R/dispatches           │
│  token (localStorage)  ────┼───────▶│  event_type: console_run            │
│                            │        │  client_payload: {id, command}      │
│                            │        │        │                            │
│                            │        │        ▼                            │
│                            │        │  .github/workflows/console-run.yml  │
│                            │        │  (ubuntu-latest, sandboxeado)       │
│                            │        │        │                            │
│                            │        │        ▼  cada ~3 s                 │
│ ③  GET /contents/runs/<id>/│        │  PUT /contents/runs/<id>/run.json  │
│      run.json  ◀───────────┼────────│  (stdout/stderr/exit/artefactos)    │
│      (polling cada 3 s)    │        │  con un token efímero que se borra │
│                            │        │  antes de ejecutar el comando       │
└────────────────────────────┘        └─────────────────────────────────────┘
```

**Latencia típica: 3–6 s** entre que se produce la salida y la ves (polling +
subida por API). No es streaming real; es "casi tiempo real". Ver §7 para
alternativas.

> **Detalle clave**: la salida se publica con la **Contents API** (no con
> `git push`), usando un token efímero escrito en un archivo de permisos `600`
> que el runner **lee y borra antes de ejecutar nada**. Así el token nunca está
> en el entorno del proceso (sería legible vía `/proc/<pid>/environ`) ni en
> archivos del workspace.

### Componentes

| Carpeta/archivo | Qué es |
|---|---|
| `web/` | Frontend estático: `index.html`, `app.js`, `styles.css`, `config.js`. Se publica tal cual en Pages. |
| `runner/run_command.py` | El "backend": valida, sandboxea, ejecuta y sube la salida por la Contents API. Corre *dentro* del runner. Solo stdlib. |
| `runner/prune_runs.py` | Poda `runs/` antiguo (cron diario). |
| `.github/workflows/console-run.yml` | Ejecuta comandos (`repository_dispatch`). |
| `.github/workflows/deploy-pages.yml` | Publica `web/` en Pages. |
| `.github/workflows/cleanup-runs.yml` | Limpieza programada de `runs/`. |
| `examples/` | Scripts de ejemplo (bash, python, GUI con Xvfb). |
| `runs/<id>/` | Salida de cada ejecución (JSON + artefactos). Se publica en esta ruta por la API. |

---

## 2. Puesta en marcha (5 minutos)

> Ya está subido a `juanbas2005/github-console`. Solo faltan los pasos 2 y 4.

### ① Código

```bash
git clone https://github.com/juanbas2005/github-console.git
cd github-console
```

### ② Activar GitHub Pages (una sola vez)

`Settings → Pages → Build and deployment → Source: **GitHub Actions`**
(no "Deploy from a branch"). El workflow `deploy-pages` publica `web/`.

Al acabar (1–2 min): **https://juanbas2005.github.io/github-console/**

### ③ Crear tu token (PAT)

La página es estática: para disparar workflows necesita un token. **El token lo
pegas tú en la página; se guarda solo en `localStorage` de tu navegador** y jamás
se commitea al repo.

`Settings → Developer settings → Personal access tokens → Fine-grained tokens →
Generate new token`

- **Repository access**: *Only select repositories* → `github-console`
- **Permissions**:
  - `Actions` → **Read and write** (disparar el workflow)
  - `Contents` → **Read and write** (el endpoint `dispatches` la requiere; también
    permite leer la salida)
  - `Metadata` → Read (viene por defecto)
- Expiración: la que quieras (mínima recomendada).

> Alternativa rápida: token clásico con scopes `repo` + `workflow`.
> Ambos requieren **access token**, no se puede con un deploy key ni con `GITHUB_TOKEN`.

### ④ Abrir la página y configurar

1. Abre la URL de Pages.
2. Pulsa **⚙** (o escribe `auth`), pega el token, guarda.
3. Deberías ver `✓ token válido: <tu-usuario>`.

### ⑤ Ejecutar tu primer comando

```
juanbas2005@github-console:~$ help
juanbas2005@github-console:~$ ls -la
juanbas2005@github-console:~$ python3 examples/colors.py
```

`ls -la` se ejecuta en el runner de GitHub. En serio: abre la pestaña
**Actions** del repo y mira el workflow `console-run` corriendo.

---

## 3. Uso

### Comandos locales (no van al runner)

| Comando | Qué hace |
|---|---|
| `help` | Lista completa de comandos. |
| `auth` | Abre la configuración (token, owner, repo, rama). |
| `whoami` | Usuario autenticado en GitHub. |
| `repo` | Repo/rama/token configurados. |
| `runs` | Últimas ejecuciones guardadas en `runs/`. |
| `show <id>` | Muestra una ejecución pasada completa. |
| `history` | Historial de comandos (guardado en el navegador). |
| `clear` | Limpia la pantalla (`Ctrl+L`). |
| `about` | Arquitectura y notas de seguridad. |

Atajos: `↑`/`↓` navegan el historial, `Ctrl+C` detiene el polling.

### Comandos remotos (se ejecutan en el runner)

```
ls -la                        # listado del sandbox
pwd && whoami                 # ⚠ && está prohibido: no hay shell
pwd                           # dónde se ejecuta (una copia temporal del repo)
df -h | head -5               # tuberías ✓
free -h                       # memoria del runner
ps aux | head -n 11           # procesos (solo los del namespace del comando)
env | grep -i token           # ⚡ no imprime nada: el entorno está saneado
python3 examples/colors.py    # salida ANSI + barra de progreso (streaming)
bash examples/sysinfo.sh      # info completa del runner
bash examples/gui_screenshot.sh   # GUI con Xvfb → captura PNG incrustada
python3 examples/proc_probe.py    # autodiagnóstico de aislamiento ⚡
cat examples/hello.sh         # leer archivos del repo
curl -s https://api.github.com/zen   # salida a Internet del runner
```

### Añadir tus propios scripts

Sube tus scripts a `examples/` o `scripts/` y ejecútalos con un intérprete
permitido: `python3 examples/mi_script.py`. En modo `allowlist` **solo se pueden
ejecutar scripts que ya estén en el repo** (no código pegado en la línea de
comandos): esa es la frontera de seguridad principal.

---

## 4. 🔒 Seguridad

### Modelo de amenazas y mitigaciones

| Riesgo | Mitigación |
|---|---|
| **Ejecución de código arbitrario** | Modo `allowlist`: solo binarios de una lista blanca, argv directo (**sin shell**), sin `-c`/`-e`/`-m`, sin metacaracteres (`; & \| $ \` > < \`), intérpretes limitados a scripts del repo. |
| **Inyección de shell** | No se usa `shell=True` en ningún punto: `subprocess.Popen(argv)` con argv validado. |
| Robo del `GITHUB_TOKEN` | El comando se ejecuta en una **copia del repo sin `.git`**; el workflow **borra el `extraheader`** que `actions/checkout` deja en `.git/config`; y la salida se sube con un **token efímero** (archivo `600` que el runner lee y elimina antes de ejecutar) que **nunca está en el entorno del proceso**. Sin esto, en los runners hosted el token SÍ es legible vía `/proc/<pid>/environ` (verificado con `examples/proc_probe.py`). |
| **Token en la salida pública** | Antes de subirse, la salida se redacta (regex para `ghp_…`, `github_pat_…`, `gho_/ghs_/ghu_/ghr_…` y tokens hex de 40 chars, sin falsos positivos en hashes largos). Doble red de seguridad por si un script imprime un token por accidente. |
| **Abuso (mining, spam, costes)** | `concurrency` con grupo único (**1 trabajo a la vez**; los dispatches simultáneos adicionales GitHub los *cancela*), `timeout-minutes` del job, `timeout` por comando, `ulimit` (CPU, tamaño de archivo, memoria virtual), salida truncada. La web bloquea enviar un segundo comando mientras hay uno en curso. |
| **Acceso no autorizado** | Disparar el workflow requiere un token con `Actions: write` **sobre este repo** — GitHub lo impone. Opcionalmente restringe con la variable `CONSOLE_ALLOWED_USERS` (se valida `github.actor` en el workflow, no es falsificable desde el navegador). |
| **Token expuesto en la web** | El token **no está en el repo ni en el código**. Vive en `localStorage` y solo viaja a `api.github.com`. Si la página es pública, cualquiera puede *abrir la página*, pero necesita su propio token válido para ejecutar algo. |
| **Secretos del workflow** | No se inyectan en el entorno del comando. Si necesitas que un script use un secreto, hazlo explícito (y asume que la salida es pública). |

### Lo que **no** cubre (y hay que saber)

- El token en `localStorage` es tan seguro como la sesión de tu navegador.
  Cualquiera que use tu navegador puede ejecutar comandos con tu identidad.
  Cierra sesión / borra el token en una máquina compartida.
- El sandbox de modo `allowlist` aísla del *repo y sus credenciales*, pero el
  comando sigue siendo del usuario autorizado: si ese usuario es malintencionado,
  el daño está limitado por la lista blanca y los recursos, no por un contenedor.
- **Residual demostrado en runners hosted**: no hay aislamiento por namespace
  (los user namespaces están deshabilitados y el job no es root), y `/proc/<pid>/environ`
  de procesos ancestros ES legible. Por eso el token va por archivo efímero y
  no en el entorno. Queda la posibilidad teórica de *memory scraping* leyendo
  `/proc/<ppid>/mem`: es un ataque deliberado y difícil, equivalente en
  privilegio a añadir un workflow tuyo (cosa que cualquier colaborador con
  `Actions: write` ya puede hacer). Para código no confiable → modo `docker`.
- Para código verdaderamente arbitrario y no confiable → modo `docker` (§5).

---

## 5. Modos de ejecución

Controlado por la **variable de repositorio** `CONSOLE_MODE`
(`Settings → Secrets and variables → Actions → Variables`).

### `allowlist` (por defecto, recomendado)

Lista blanca + sin shell + entorno saneado + namespace + ulimits. Perfecto para
uso interactivo y para scripts del repo.

Amplía la lista blanca con la variable `CONSOLE_EXTRA_COMMANDS`
(comas separadas), por ejemplo: `CONSOLE_EXTRA_COMMANDS=terraform,helm,kubectl`.

### `docker` (opt-in, sandbox duro)

Para **código arbitrario** (incluido código pegado por el usuario). Se ejecuta
dentro de un contenedor blindado:

```
docker run --rm --network none --memory 512m --memory-swap 512m --cpus 1 \
  --pids-limit 128 --cap-drop ALL --security-opt no-new-privileges \
  --read-only --tmpfs /tmp:rw,size=64m --user 1000:1000 \
  -v sandbox:/workspace:ro -v artifacts:/artifacts \
  <imagen> sh -c "<comando>"
```

- Sin red (`--network none`), sin capacidades, sin privilegios, filesystem
  raíz de solo lectura, límites duros de CPU/memoria/procesos.
- **Contrapartida**: sin red no hay `pip install`, `npm install` ni `curl`.
  Si los necesitas, preinstálalo en la imagen o crea un workflow propio.
- Requiere `ubuntu-latest` (Docker no está en los runners de macOS) y la
  variable `CONSOLE_IMAGE` (por defecto `ubuntu:24.04`). Es la **única
  dependencia externa** del proyecto (pull de una imagen pública de Docker Hub).
- Configúralo con la variable `CONSOLE_MODE=docker`.

### Variables de repositorio disponibles

| Variable | Default | Descripción |
|---|---|---|
| `CONSOLE_MODE` | `allowlist` | `allowlist` \| `docker` |
| `CONSOLE_TIMEOUT` | `30` | Segundos máximos por comando |
| `CONSOLE_ALLOWED_USERS` | *(vacío)* | JSON: `["usuario1"]`. Vacío = cualquiera con `Actions: write`. |
| `CONSOLE_EXTRA_COMMANDS` | *(vacío)* | Comandos extra permitidos (separados por comas) |
| `CONSOLE_IMAGE` | `ubuntu:24.04` | Imagen del modo docker |
| `CONSOLE_INSTALL_GUI_TOOLS` | *(vacío)* | `true` instala `xvfb`, `imagemagick` y `x11-apps` |
| `CONSOLE_RUNS_RETENTION_DAYS` | `7` | Días antes de podar `runs/` |

---

## 6. 🖥️ Programas con interfaz gráfica (Xvfb / VNC / noVNC)

| Necesidad | ¿ viable en Actions ? | Cómo |
|---|---|---|
| **App gráfica headless** (abrir ventana, automatizar, capturar) | ✅ **Sí** | `Xvfb` + captura. Activa `CONSOLE_INSTALL_GUI_TOOLS=true` y ejecuta `bash examples/gui_screenshot.sh`. El PNG se commitea en `runs/<id>/` y se **muestra incrustado** en la consola. |
| **Streaming de frames "en vivo"** | ⚠️ **A medias** | No hay VNC accesible, pero el runner puede commitear capturas cada N segundos y la página las muestra al refrescar. Latencia de segundos. Implementable reutilizando `artifacts/`. |
| **Sesión VNC/noVNC interactiva real** | ❌ **No (solo con GitHub)** | Los runners **no tienen conectividad entrante**: no hay IP/puerto público al que conectar un cliente VNC. Hace falta un **relay con URL pública** (ngrok, Cloudflare Tunnel, o un servidor tuyo) → sería la *única* dependencia externa justificada. |
| **Grabar la sesión para verla después** | ✅ **Sí** | Graba con `asciinema`/`ffmpeg`, guárdalo en `artifacts/`, y reprodúcelo en el navegador (asciinema-player o `<video>`). |

**Dicho claramente — qué hay y qué no hay en un runner `ubuntu-latest`:**

- ✅ Hay: root (con `sudo`), 4 vCPU, 16 GB RAM, 14 GB SSD, salida a Internet,
  Docker, `apt`, lenguajes (Python, Node, Ruby, Go…), **sin GPU** (salvo runners
  grandes con GPU), **sin systemd**, **sin audio**, **sin puerto serie**.
- ❌ No hay: IP pública entrante, sockets persistentes hacia el runner después
  de que el job termine, GPU, display físico, estado entre jobs (todo es efímero).

---

## 7. Límites conocidos (y alternativas realistas)

| Límite | Detalle | Alternativa |
|---|---|---|
| **No es streaming real** | Latencia 3–6 s por polling + commit. | Para tiempo real necesitas un **relay externo** (Cloudflare Tunnel/Worker con WebSocket, o un servidor propio). GitHub no ofrece push a un cliente estático. |
| **1 ejecución a la vez** | `concurrency.group: console-run` serializa. Las demás se encolan. | Varios grupos (`console-run-${{ github.actor }}`) si quieres paralelismo por usuario. |
| **Límite de Actions** | 6 h por job; minutos consumidos de tu cuota (gratis en repos públicos). | `CONSOLE_TIMEOUT` y `timeout-minutes` ya limitan. |
| **`runs/` crece en git** | Cada ejecución commitea archivos. | `cleanup-runs.yml` poda a diario (`CONSOLE_RUNS_RETENTION_DAYS`). |
| **Salida truncada a 250 KB por stream** | La Contents API limita el cuerpo a 1 MB (base64 incluido). | Vuelve a ejecutar con `head` o escribe a un artefacto. |
| **Artefactos inline < 900 KB** | La API de contents no devuelve archivos > 1 MB inline. | La web cae a la blob API automáticamente; para archivos muy grandes usa artifacts de Actions. |
| **`repository_dispatch` solo en la rama por defecto** | Si el workflow no está en `main`, no se dispara. | Mantén los workflows en `main`. |
| **Polling consume API** | ~1 llamada cada 3 s mientras corre (5000/h con token). | Sube `pollIntervalMs` en `config.js` si lo necesitas. |

---

## 8. Troubleshooting

| Síntoma | Causa / solución |
|---|---|
| `✗ No se pudo disparar el workflow: Not Found` | Owner/repo equivocado en ⚙, o el workflow no está en la rama principal. |
| `✗ Bad credentials` / 401 | Token caducado o sin permisos. Regénéralo con `Actions` + `Contents`. |
| `✗ Resource not accessible` / 403 | El token no tiene `Actions: write` sobre **este** repo. |
| `⏳ trabajo en cola…` mucho rato | Hay otro comando en ejecución (concurrencia 1) o la cola de Actions. |
| La página no carga / 404 | Pages debe estar en `Source: GitHub Actions`. Revisa el workflow `deploy-pages` en la pestaña Actions. |
| `⛔ rechazado por seguridad` | Lee el mensaje: dice exactamente qué carácter/comando no está permitido. Usa `help`. |
| `env | grep token` no imprime nada | **Es lo que debe pasar**: el entorno del comando está saneado. |
| El comando no encuentra `runs/` | El sandbox es una copia sin `runs/`, `.github` ni `.git`. Es deliberado. |

---

## 9. Ampliaciones posibles

1. **Login con GitHub (OAuth + PKCE)** — crea una OAuth App
   (`Settings → Developer settings → OAuth apps`) con la URL de esta página como
   callback y pon el `client_id` en `web/config.js` (`oauthClientId`). Así no
   pegas un token a mano. *Nota:* GitHub habilitó PKCE para apps públicas, pero
   el intercambio del token depende de CORS en `github.com/login/oauth/access_token`;
   si tu navegador lo bloquea, sigue el flujo PAT.
2. **Streaming real** — la única forma es salir de GitHub para el transporte:
   un Cloudflare Worker / Durable Object o un pequeño servidor WebSocket que el
   runner envíe (`curl` por cada chunk) y la página escuche. GitHub Actions no
   puede empujar a un cliente estático.
3. **Historial compartido** — `runs` ya es público en el repo; la web lo lista
   con el comando `runs`.
4. **Más recursos** — self-hosted runners o runners más grandes (`runs-on`).
5. **Multi-entorno** — variables `CONSOLE_*` por entorno (dev/prod) usando
   environments de Actions.

---

## 10. FAQ

**¿De verdad se ejecuta en GitHub Actions?** Sí. Cada comando dispara el
workflow `console-run` (verifícalo en la pestaña *Actions*) y corre en un
`ubuntu-latest` real. La web lee la salida desde el git del repo.

**¿El token queda expuesto?** No en el repositorio ni en el código fuente —
está en el `localStorage` de tu navegador. Si la página es pública, alguien
podría *abrir la consola*, pero necesitaría un token propio con permisos sobre
el repo para ejecutar algo.

**¿Esto cuesta dinero?** Consume minutos de Actions. En repos públicos,
GitHub Actions es gratis (con límites de uso). Cada comando son unos segundos.

**¿Por qué no se puede hacer `npm install`?** En `allowlist` no hay código
arbitrario; en `docker` la red está cortada. Solución: añade la dependencia al
repo o crea un workflow dedicado que la instale.

**¿Puedo cancelar un comando?** `Ctrl+C` detiene el polling de la página, pero
el job sigue en el runner hasta que termina o agota `CONSOLE_TIMEOUT`. Cancelar
el job vía API queda como ejercicio (necesitarías mapear run → `actions/runs/<id>`).

**¿Por qué la salida se sube por la Contents API y no con artifacts/git push?**
Descargar artifacts desde el navegador requiere auth y una redirección que el
navegador sigue sin el header `Authorization`; y hacer `git push` desde el runner
requeriría dejar credenciales en el workspace o en el entorno del proceso
(legibles por el comando vía `/proc`). La Contents API sube el archivo y queda
listo para que la página estática lo lea, con el token efímero ya borrado.

---

## 11. Licencia

MIT. Úsalo como quieras. Sin garantía: esto ejecuta código en tu cuenta de
GitHub bajo tu responsabilidad — revisa la §4 antes de ponerlo en producción.
