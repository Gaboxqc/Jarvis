/**
 * English and Spanish — REQ-28.
 *
 * A flat dictionary rather than a library: there are two languages and ~50
 * strings, and pulling in an i18n framework for that would be more code than
 * the thing it replaces.
 */

export type Lang = "en" | "es";

const STRINGS = {
  en: {
    "app.title": "Kai",
    "nav.chat": "Chat",
    "nav.memory": "Memory",
    "nav.history": "History",
    "nav.settings": "Settings",
    "nav.label": "Sections",

    "chat.placeholder": "Ask me something, or tell me to do something",
    "chat.send": "Send",
    "chat.you": "You",
    "chat.empty": "Nothing yet. Ask a question, or say what you need doing.",
    "chat.thinking": "Thinking",
    "chat.log": "Conversation",

    "confirm.title": "Needs your go-ahead",
    "confirm.yes": "Go ahead",
    "confirm.no": "Cancel",
    "confirm.undoable": "This can be undone.",
    "confirm.permanent": "This cannot be undone.",

    "state.idle": "Idle",
    "state.listening": "Listening",
    "state.thinking": "Thinking",
    "state.speaking": "Speaking",
    "state.recording": "Recording",
    "state.offline": "Backend unreachable",
    "state.focus": "Focus session active",

    "memory.title": "What Kai remembers about you",
    "memory.empty": "Nothing stored yet.",
    "memory.forget": "Forget",
    "memory.forgetAll": "Forget everything",
    "memory.confirmAll": "Delete every stored memory? This cannot be undone.",

    "history.title": "What Kai has done",
    "history.empty": "No actions yet.",
    "history.undo": "Undo",
    "history.undoLast": "Undo the last thing",

    "settings.title": "Settings and privacy",
    "settings.brain": "Language model",
    "settings.skills": "Skills loaded",
    "settings.config": "Config file",
    "settings.data": "Your data lives in",
    "settings.egress": "What leaves this machine",
    "settings.webSearch": "Web search",
    "settings.liveData": "Live data (rates, weather)",
    "settings.cloudLlm": "Cloud language model",
    "settings.egressNote":
      "Everything not listed here runs locally. Change these in kai.config.yaml.",
    "settings.language": "Language",
    "settings.danger": "Delete all local data",
    "settings.dangerNote":
      "Removes every conversation, memory, action record, reminder, task, transcript and document index. This cannot be undone.",
    "settings.wipe": "Delete everything",
    "settings.wipeConfirm":
      "Delete ALL local data? Conversations, memories, reminders, tasks, transcripts and indexes. This cannot be undone.",
    "settings.wiped": "Removed {count} records.",

    "prereq.startingTitle": "Starting up…",
    "prereq.startingBody":
      "Kai is loading its skills. This takes a few seconds on the first launch after installing.",
    "prereq.backendTitle": "Kai's backend isn't running",
    "prereq.backendBody":
      "The assistant service isn't answering on port 8756. If you started Kai from source, run the backend too; if you installed it, try quitting from the tray and reopening.",
    "prereq.modelTitle": "The language model isn't ready",
    "prereq.modelBody":
      "Everything else is installed, but Kai needs Ollama for the language model — it's the one part too large to bundle. Install it, then pull a model:",

    "common.on": "on",
    "common.off": "off",
    "common.retry": "Try again",
    "common.loading": "Loading",
    "common.error": "Something went wrong",
  },
  es: {
    "app.title": "Kai",
    "nav.chat": "Chat",
    "nav.memory": "Memoria",
    "nav.history": "Historial",
    "nav.settings": "Ajustes",
    "nav.label": "Secciones",

    "chat.placeholder": "Pregúntame algo, o dime qué hacer",
    "chat.send": "Enviar",
    "chat.you": "Tú",
    "chat.empty": "Nada todavía. Pregunta algo, o di qué necesitas.",
    "chat.thinking": "Pensando",
    "chat.log": "Conversación",

    "confirm.title": "Necesita tu confirmación",
    "confirm.yes": "Adelante",
    "confirm.no": "Cancelar",
    "confirm.undoable": "Esto se puede deshacer.",
    "confirm.permanent": "Esto no se puede deshacer.",

    "state.idle": "En reposo",
    "state.listening": "Escuchando",
    "state.thinking": "Pensando",
    "state.speaking": "Hablando",
    "state.recording": "Grabando",
    "state.offline": "Sin conexión con el servidor",
    "state.focus": "Sesión de concentración activa",

    "memory.title": "Lo que Kai recuerda de ti",
    "memory.empty": "No hay nada guardado.",
    "memory.forget": "Olvidar",
    "memory.forgetAll": "Olvidarlo todo",
    "memory.confirmAll": "¿Borrar todos los recuerdos? No se puede deshacer.",

    "history.title": "Lo que Kai ha hecho",
    "history.empty": "Todavía no hay acciones.",
    "history.undo": "Deshacer",
    "history.undoLast": "Deshacer lo último",

    "settings.title": "Ajustes y privacidad",
    "settings.brain": "Modelo de lenguaje",
    "settings.skills": "Habilidades cargadas",
    "settings.config": "Archivo de configuración",
    "settings.data": "Tus datos están en",
    "settings.egress": "Lo que sale de este equipo",
    "settings.webSearch": "Búsqueda web",
    "settings.liveData": "Datos en vivo (divisas, clima)",
    "settings.cloudLlm": "Modelo en la nube",
    "settings.egressNote":
      "Todo lo que no aparece aquí se ejecuta localmente. Cámbialo en kai.config.yaml.",
    "settings.language": "Idioma",
    "settings.danger": "Borrar todos los datos locales",
    "settings.dangerNote":
      "Elimina cada conversación, recuerdo, acción, recordatorio, tarea, transcripción e índice. No se puede deshacer.",
    "settings.wipe": "Borrarlo todo",
    "settings.wipeConfirm":
      "¿Borrar TODOS los datos locales? Conversaciones, recuerdos, recordatorios, tareas, transcripciones e índices. No se puede deshacer.",
    "settings.wiped": "Se eliminaron {count} registros.",

    "prereq.startingTitle": "Iniciando…",
    "prereq.startingBody":
      "Kai está cargando sus habilidades. Tarda unos segundos la primera vez tras instalarlo.",
    "prereq.backendTitle": "El servicio de Kai no está en marcha",
    "prereq.backendBody":
      "El servicio no responde en el puerto 8756. Si iniciaste Kai desde el código, arranca también el backend; si lo instalaste, ciérralo desde la bandeja y ábrelo de nuevo.",
    "prereq.modelTitle": "El modelo de lenguaje no está listo",
    "prereq.modelBody":
      "Todo lo demás está instalado, pero Kai necesita Ollama para el modelo de lenguaje: es la única pieza demasiado grande para incluir. Instálalo y descarga un modelo:",

    "common.on": "activado",
    "common.off": "desactivado",
    "common.retry": "Reintentar",
    "common.loading": "Cargando",
    "common.error": "Algo salió mal",
  },
} as const;

export type Key = keyof (typeof STRINGS)["en"];

export function translate(lang: Lang, key: Key, vars?: Record<string, string | number>) {
  const table = STRINGS[lang] ?? STRINGS.en;
  let value: string = table[key] ?? STRINGS.en[key] ?? key;
  if (vars) {
    for (const [name, replacement] of Object.entries(vars)) {
      value = value.replace(`{${name}}`, String(replacement));
    }
  }
  return value;
}

export function detectLang(): Lang {
  const stored = localStorage.getItem("kai.lang");
  if (stored === "en" || stored === "es") return stored;
  return navigator.language.toLowerCase().startsWith("es") ? "es" : "en";
}
