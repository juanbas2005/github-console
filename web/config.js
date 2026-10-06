// Configuración de la consola. Edita SOLO lo que necesites; owner/repo se
// autodetectan desde la URL de GitHub Pages si se dejan vacíos.
//
// IMPORTANTE: nunca pongas tokens ni secretos aquí. El token lo introduce el
// usuario en la página y se guarda solo en su navegador (localStorage).
window.CONSOLE_CONFIG = {
  // Sobrescribe la detección automática (ej: { owner: "juanbas2005", repo: "github-console" })
  owner: "",
  repo: "",

  // Rama donde el runner commitea las salidas (por defecto, la rama principal).
  branch: "",

  // Modo de ejecución: "allowlist" (recomendado) o "docker" (sandbox duro,
  // requiere la variable de repo CONSOLE_MODE=docker). Si se deja "", el
  // runner usa la variable de repositorio CONSOLE_MODE.
  mode: "",

  // Polling de la salida.
  pollIntervalMs: 3000,
  maxRunWaitMs: 600000, // 10 min máximo esperando a que termine un trabajo

  // Historial de comandos en el navegador.
  maxHistory: 100,

  // OAuth App opcional (PKCE). Crea una en github.com/settings/developers
  // con la URL de callback de esta página. Vacío = solo PAT.
  oauthClientId: "",
};
