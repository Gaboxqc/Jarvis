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
    // Named so the wait is legible: the two slowest parts of a turn cannot
    // stream, and "Thinking" for eight seconds reads as a hang.
    "chat.stageRouting": "Working out what you need",
    "chat.stageWorking": "Doing it",
    "chat.stageWriting": "Writing",
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

    "voice.talk": "Hold a conversation out loud",
    "voice.listening": "Listening — speak now, I'll stop when you pause",
    "voice.heard": "heard",
    "voice.speakOn": "Speaking replies aloud",
    "voice.speakOff": "Replies are silent",
    "voiceBlocked.offline": "I can't reach the voice service.",
    "voiceBlocked.models": "The speech models aren't downloaded yet. Open Settings to get them.",
    "voiceBlocked.mic": "No microphone was found.",
    "voiceBlocked.off": "Voice is switched off.",
    "voiceBlocked.inputOff": "Voice input is switched off. Turn it on in Settings.",
    "voiceBlocked.failed": "Something went wrong while listening.",

    "settings.voice": "Voice",
    "settings.voiceEnabled": "Talk and listen",
    "settings.voiceInput": "Microphone input",
    "settings.voiceOutput": "Speak replies aloud",
    "settings.voiceWake": "Wake word",
    "settings.voiceWakeNote":
      "Leaves the microphone open all the time. Nothing is transcribed before the phrase is heard.",
    "settings.voiceModels": "Speech models",
    "settings.voiceDownload": "Download ({mb} MB)",
    "settings.voiceDownloading": "Downloading… this takes a few minutes",
    "settings.voiceReady": "Downloaded and ready",
    "settings.voiceNoMic": "No microphone detected",
    "settings.voiceLocal":
      "Speech recognition and synthesis run on this machine. Audio never leaves it.",

    "accounts.title": "Mail and calendar accounts",
    "accounts.mail": "Mail",
    "accounts.calendar": "Calendars",
    "accounts.noneMail": "No mail account connected.",
    "accounts.noneCalendar": "No calendar connected.",
    "accounts.addMail": "Add a mail account",
    "accounts.addCalendar": "Add a calendar",
    "accounts.label": "Name it (how you'll refer to it)",
    "accounts.imapHost": "IMAP server",
    "accounts.port": "Port",
    "accounts.username": "Username",
    "accounts.smtpHost": "SMTP server (to send)",
    "accounts.smtpPort": "SMTP port",
    "accounts.caldavUrl": "CalDAV server URL",
    "accounts.save": "Add account",
    "accounts.remove": "Remove",
    "accounts.removeConfirm": "Remove {label}? The saved password stays in Windows Credential Manager.",
    "accounts.removed": "Removed {label}.",
    "accounts.added": "{label} added.",
    "accounts.noPassword": "no password yet",
    "accounts.noStore": "Windows Credential Manager isn't available, so passwords can't be stored.",
    "accounts.passwordNote":
      "Stored in Windows Credential Manager, not in any file Kai writes. Gmail and Outlook with two-factor need an app password rather than your normal one.",
    "accounts.icsNote":
      "In Google Calendar: Settings → your calendar → \"Secret address in iCal format\". Treat it like a password — anyone holding it can read your whole calendar — so Kai stores it in Windows Credential Manager, not in a file.",
    "accounts.password": "Password",
    "accounts.icsUrl": "Calendar address (iCal URL)",
    "accounts.calendarType": "Calendar type",
    "accounts.typeIcs": "Read-only iCal link (Google, Outlook)",
    "accounts.typeCaldav": "Two-way CalDAV (Fastmail, Nextcloud, iCloud)",
    "accounts.needsSecret": "Not connected yet — remove it and add it again with its password.",
    "accounts.check": "Test",
    "accounts.checking": "Testing…",
    "accounts.checkOk": "{label} connected.",
    "accounts.checkFailed": "{label} didn't connect: {error}",
    "accounts.configAt": "Config file:",

    "notify.dismiss": "Dismiss",

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
    "chat.stageRouting": "Viendo qué necesitas",
    "chat.stageWorking": "Haciéndolo",
    "chat.stageWriting": "Escribiendo",
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

    "voice.talk": "Hablar en voz alta",
    "voice.listening": "Escuchando: habla ahora, me detendré cuando hagas una pausa",
    "voice.heard": "oído",
    "voice.speakOn": "Respuestas en voz alta",
    "voice.speakOff": "Respuestas en silencio",
    "voiceBlocked.offline": "No puedo contactar con el servicio de voz.",
    "voiceBlocked.models": "Los modelos de voz no están descargados. Ábrelos en Ajustes.",
    "voiceBlocked.mic": "No se encontró ningún micrófono.",
    "voiceBlocked.off": "La voz está desactivada.",
    "voiceBlocked.inputOff": "La entrada de voz está desactivada. Actívala en Ajustes.",
    "voiceBlocked.failed": "Algo salió mal al escuchar.",

    "settings.voice": "Voz",
    "settings.voiceEnabled": "Hablar y escuchar",
    "settings.voiceInput": "Entrada de micrófono",
    "settings.voiceOutput": "Leer las respuestas en voz alta",
    "settings.voiceWake": "Palabra de activación",
    "settings.voiceWakeNote":
      "Deja el micrófono abierto todo el tiempo. Nada se transcribe antes de oír la frase.",
    "settings.voiceModels": "Modelos de voz",
    "settings.voiceDownload": "Descargar ({mb} MB)",
    "settings.voiceDownloading": "Descargando… tarda unos minutos",
    "settings.voiceReady": "Descargados y listos",
    "settings.voiceNoMic": "No se detectó micrófono",
    "settings.voiceLocal":
      "El reconocimiento y la síntesis de voz se ejecutan en este equipo. El audio nunca sale de él.",

    "accounts.title": "Cuentas de correo y calendario",
    "accounts.mail": "Correo",
    "accounts.calendar": "Calendarios",
    "accounts.noneMail": "No hay ninguna cuenta de correo conectada.",
    "accounts.noneCalendar": "No hay ningún calendario conectado.",
    "accounts.addMail": "Añadir cuenta de correo",
    "accounts.addCalendar": "Añadir calendario",
    "accounts.label": "Nombre (cómo la llamarás)",
    "accounts.imapHost": "Servidor IMAP",
    "accounts.port": "Puerto",
    "accounts.username": "Usuario",
    "accounts.smtpHost": "Servidor SMTP (para enviar)",
    "accounts.smtpPort": "Puerto SMTP",
    "accounts.caldavUrl": "URL del servidor CalDAV",
    "accounts.save": "Añadir cuenta",
    "accounts.remove": "Quitar",
    "accounts.removeConfirm": "¿Quitar {label}? La contraseña guardada permanece en el Administrador de credenciales.",
    "accounts.removed": "Se quitó {label}.",
    "accounts.added": "{label} añadida.",
    "accounts.noPassword": "sin contraseña",
    "accounts.noStore": "El Administrador de credenciales no está disponible, no se pueden guardar contraseñas.",
    "accounts.passwordNote":
      "Se guarda en el Administrador de credenciales de Windows, no en ningún archivo. Gmail y Outlook con doble factor necesitan una contraseña de aplicación.",
    "accounts.icsNote":
      "En Google Calendar: Configuración → tu calendario → \"Dirección secreta en formato iCal\". Trátala como una contraseña: quien la tenga puede leer todo tu calendario, por eso Kai la guarda en el Administrador de credenciales.",
    "accounts.password": "Contraseña",
    "accounts.icsUrl": "Dirección del calendario (URL iCal)",
    "accounts.calendarType": "Tipo de calendario",
    "accounts.typeIcs": "Enlace iCal de solo lectura (Google, Outlook)",
    "accounts.typeCaldav": "CalDAV bidireccional (Fastmail, Nextcloud, iCloud)",
    "accounts.needsSecret": "Sin conectar — quítala y añádela de nuevo con su contraseña.",
    "accounts.check": "Probar",
    "accounts.checking": "Probando…",
    "accounts.checkOk": "{label} conectada.",
    "accounts.checkFailed": "{label} no conectó: {error}",
    "accounts.configAt": "Archivo de configuración:",

    "notify.dismiss": "Descartar",

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
