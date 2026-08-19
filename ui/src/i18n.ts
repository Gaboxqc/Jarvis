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
    "nav.documents": "Documents",
    "documents.title": "Your documents",
    "documents.placeholder": "Ask about something in your files — “how much was the deposit”",
    "documents.search": "Search",
    "documents.searching": "Searching…",
    "documents.matches": "{count} passage(s) found",
    "documents.noMatches": "Nothing matched.",
    "documents.nothingIndexed":
      "No documents are indexed yet, so a search will find nothing. Add a folder in Settings, then rescan below.",
    "documents.index": "Index",
    "documents.indexed": "Indexed",
    "documents.counts": "{documents} file(s), {chunks} passage(s)",
    "documents.folders": "Folders",
    "documents.noFolders": "none set",
    "documents.rescan": "Rescan now",
    "documents.scanning": "Scanning…",
    "documents.scanningNow": "Scanning your folders",
    "documents.deferred": "Waiting: {reason}",
    "documents.failed": "{count} file(s) couldn't be read",
    "documents.showFiles": "Show indexed files",
    "documents.hideFiles": "Hide indexed files",
    "documents.clear": "Clear index",
    "documents.clearConfirm":
      "Remove everything from the document index? Your files are not touched — only what Kai has read from them.",
    "documents.localNote":
      "Indexing happens on this machine and the contents never leave it. Clearing the index does not delete any file.",
    "nav.today": "Today",
    "today.title": "Today",
    "today.refresh": "Refresh",
    "today.empty": "Nothing to report.",
    "today.sectionEmpty": "Nothing here.",
    "today.sectionUnconfigured": "No account connected — add one in Settings.",
    "today.sectionFailed": "Couldn't check this one: {error}",
    "today.section.calendar": "Calendar",
    "today.section.reminders": "Reminders",
    "today.section.tasks": "Tasks",
    "today.section.mail": "Mail",
    "today.focus": "Focus",
    "today.focusStart": "{minutes} min",
    "today.focusNote":
      "Holds reminders, pauses background indexing, and closes the apps listed as distracting in your config.",
    "today.focusWillClose": "Starting now would close: {apps}. Unsaved work in them would be lost.",
    "today.focusConfirm":
      "Start focus and close {count} running app(s): {apps}? Unsaved work in them will be lost.",
    "today.focusActive": "Focus session running — {minutes} minutes left",
    "today.focusClosed": "Closed: {apps}",
    "today.focusEnd": "End session",
    "nav.meetings": "Meetings",
    "meetings.title": "Meetings",
    "meetings.defaultLabel": "Meeting",
    "meetings.labelPlaceholder": "What is this? — “standup”, “call with Ana”",
    "meetings.start": "Start recording",
    "meetings.stop": "Stop and transcribe",
    "meetings.stopping": "Transcribing…",
    "meetings.recordingNow": "Recording: {label}",
    "meetings.progress": "{time} · {words} words so far",
    "meetings.micOnlyNote":
      "Records this machine's microphone only — not the other side of a call. Everything is transcribed here and never uploaded. Tell people in the room that you are recording.",
    "meetings.past": "Past recordings",
    "meetings.none": "Nothing recorded yet.",
    "meetings.length": "{minutes} min · {words} words",
    "meetings.decisions": "Decisions",
    "meetings.actions": "Action items",
    "meetings.truncated": "The recording was long, so the summary covers part of it.",
    "meetings.summaryFailed": "Couldn't summarise this one: {error}",
    "meetings.showText": "Show the full transcript",
    "meetings.hideText": "Hide the transcript",
    "meetings.noText": "No transcript was saved.",
    "meetings.delete": "Delete",
    "meetings.deleteConfirm":
      "Delete “{label}” and its transcript? This is the only copy.",
    "meetings.localNote":
      "Transcription runs on this machine. Recordings and transcripts stay in your data folder until you delete them.",
    "nav.planner": "Planner",
    "planner.title": "Reminders and tasks",
    "planner.reminders": "Reminders",
    "planner.noReminders": "Nothing scheduled.",
    "planner.repeats": "repeats",
    "planner.cancel": "Cancel",
    "planner.cancelConfirm": "Cancel the reminder “{label}”?",
    "planner.tasks": "Tasks",
    "planner.noTasks": "Nothing on the list.",
    "planner.add": "Add",
    "planner.addPlaceholder": "Something to do — add #tags if you like",
    "planner.delete": "Delete",
    "planner.deleteConfirm": "Delete “{text}”? Completing it instead keeps the record.",
    "planner.due": "due",
    "planner.showDone": "Show {count} done",
    "planner.hideDone": "Hide done",
    "planner.mirrorNote":
      "Also written to tasks.md in your data folder, so the list is still readable if Kai is ever uninstalled.",

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

    "avatar.needsCore":
      "The avatar needs Live2D's Cubism Core, which is licensed separately and isn't included. Drop it into the app's live2d folder to switch it on — everything else works without it.",
    "avatar.coreBlocked":
      "The avatar is installed but can't start: this app's security policy is blocking WebAssembly, which Cubism Core needs. That's a bug in the app, not something you can fix here — everything else works.",
    "avatar.coreFailed":
      "The avatar failed to start. Everything else works without it.",
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
    "settings.brainMissingOption": "{model} (not installed)",
    "settings.brainMismatch":
      "Kai is set to use {model}, which isn't installed. Installed: {installed}. Pick one above — nothing else will work until the model matches.",
    "settings.skills": "Skills loaded",
    "settings.config": "Config file",
    "settings.data": "Your data lives in",
    "settings.egress": "What leaves this machine",
    "settings.webSearch": "Web search",
    "settings.liveData": "Live data (rates, weather)",
    "settings.cloudLlm": "Cloud language model",
    "settings.egressNote":
      "Everything not listed here runs locally. Turning one on is recorded in the log with the time and what it was before.",
    "settings.files": "Files Kai can reach",
    "settings.allowedRoots": "Folders Kai may read and organise",
    "settings.allowedRootsNote":
      "Every file skill is limited to these. Nothing outside them can be read, moved or renamed.",
    "settings.indexedFolders": "Folders searched for documents",
    "settings.indexedFoldersNote":
      "Contents are indexed locally so questions about your paperwork can be answered. Indexing runs in the background.",
    "folders.none": "None set.",
    "folders.add": "Add",
    "folders.remove": "Remove",
    "folders.lastOne": "At least one folder is required.",
    "folders.placeholder": "C:/Users/you/Documents",
    "updates.title": "Updates",
    "updates.installed_version": "Installed",
    "updates.check": "Check now",
    "updates.checking": "Checking…",
    "updates.upToDate": "You're on the latest version.",
    "updates.available": "Version {version} is available",
    "updates.install": "Download and install",
    "updates.installing": "Downloading… Kai will restart when it's done.",
    "updates.installed": "Installed. Restart Kai to finish.",
    "updates.checkFailed": "Couldn't check: {error}",
    "updates.installFailed": "Couldn't install: {error}",
    "updates.desktopOnly": "Updates are only available in the installed app.",
    "updates.note":
      "Checks github.com/Gaboxqc/Jarvis, and only when you press the button — Kai never phones home on its own. Updates are signed, and anything that doesn't verify is refused.",
    "startup.title": "Startup",
    "startup.enable": "Start Kai when Windows starts",
    "startup.note":
      "Opens minimised to the tray. Without this, the Ctrl+Alt+K hotkey does nothing until you launch Kai yourself.",
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

    "clone.title": "Your own voice",
    "clone.notInstalled":
      "Speaking in a cloned voice needs the XTTS engine, which isn't installed. It's about 2GB and optional — everything else works without it.",
    "clone.consent": "Let Kai speak in a cloned voice",
    "clone.consentNote":
      "A copy of a voice can be used to say things that voice never said. Only clone your own, or someone who is here and agrees. Unticking this switches back to the built-in voice straight away.",
    "clone.noReference": "No sample yet — about {seconds} seconds of speech is enough.",
    "clone.haveReference": "Recorded: {seconds} seconds.",
    "clone.choose": "Choose a .wav file",
    "clone.replace": "Choose a different file",
    "clone.uploading": "Uploading…",
    "clone.uploaded": "Saved {seconds} seconds. Replies will use this voice.",
    "clone.uploadNote":
      "A 16-bit .wav of someone speaking clearly, ten seconds or more. Stored on this machine and deletable at any time.",
    "clone.forget": "Delete recording",
    "clone.forgetConfirm":
      "Delete the voice recording? Replies go back to the built-in voice.",
    "settings.voice": "Voice",
    "settings.voiceEnabled": "Talk and listen",
    "settings.voiceInput": "Microphone input",
    "settings.voiceOutput": "Speak replies aloud",
    "settings.voiceWake": "Wake word",
    "settings.voiceWakeDownloading": "Fetching the wake word model… this takes a moment.",
    "settings.voiceWakeFailed":
      "Couldn't fetch the wake word model, so it hasn't been turned on. Check your connection and try again.",
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
    "nav.documents": "Documentos",
    "documents.title": "Tus documentos",
    "documents.placeholder": "Pregunta por algo en tus archivos — “de cuánto era el depósito”",
    "documents.search": "Buscar",
    "documents.searching": "Buscando…",
    "documents.matches": "{count} pasaje(s) encontrados",
    "documents.noMatches": "No hubo coincidencias.",
    "documents.nothingIndexed":
      "Aún no hay documentos indexados, así que la búsqueda no encontrará nada. Añade una carpeta en Ajustes y vuelve a escanear.",
    "documents.index": "Índice",
    "documents.indexed": "Indexado",
    "documents.counts": "{documents} archivo(s), {chunks} pasaje(s)",
    "documents.folders": "Carpetas",
    "documents.noFolders": "ninguna",
    "documents.rescan": "Escanear ahora",
    "documents.scanning": "Escaneando…",
    "documents.scanningNow": "Escaneando tus carpetas",
    "documents.deferred": "En espera: {reason}",
    "documents.failed": "{count} archivo(s) no se pudieron leer",
    "documents.showFiles": "Ver archivos indexados",
    "documents.hideFiles": "Ocultar archivos indexados",
    "documents.clear": "Vaciar índice",
    "documents.clearConfirm":
      "¿Quitar todo del índice de documentos? Tus archivos no se tocan, solo lo que Kai ha leído de ellos.",
    "documents.localNote":
      "La indexación ocurre en este equipo y el contenido nunca sale. Vaciar el índice no borra ningún archivo.",
    "nav.today": "Hoy",
    "today.title": "Hoy",
    "today.refresh": "Actualizar",
    "today.empty": "Nada que informar.",
    "today.sectionEmpty": "Nada por aquí.",
    "today.sectionUnconfigured": "Sin cuenta conectada — añade una en Ajustes.",
    "today.sectionFailed": "No pude consultar esto: {error}",
    "today.section.calendar": "Calendario",
    "today.section.reminders": "Recordatorios",
    "today.section.tasks": "Tareas",
    "today.section.mail": "Correo",
    "today.focus": "Concentración",
    "today.focusStart": "{minutes} min",
    "today.focusNote":
      "Retiene los recordatorios, pausa la indexación y cierra las apps marcadas como distracciones en tu configuración.",
    "today.focusWillClose": "Empezar ahora cerraría: {apps}. Se perdería lo no guardado.",
    "today.focusConfirm":
      "¿Empezar concentración y cerrar {count} app(s) abiertas: {apps}? Se perderá lo que no esté guardado.",
    "today.focusActive": "Sesión activa — quedan {minutes} minutos",
    "today.focusClosed": "Se cerraron: {apps}",
    "today.focusEnd": "Terminar sesión",
    "nav.meetings": "Reuniones",
    "meetings.title": "Reuniones",
    "meetings.defaultLabel": "Reunión",
    "meetings.labelPlaceholder": "¿Qué es esto? — “daily”, “llamada con Ana”",
    "meetings.start": "Empezar a grabar",
    "meetings.stop": "Parar y transcribir",
    "meetings.stopping": "Transcribiendo…",
    "meetings.recordingNow": "Grabando: {label}",
    "meetings.progress": "{time} · {words} palabras hasta ahora",
    "meetings.micOnlyNote":
      "Graba solo el micrófono de este equipo, no el otro lado de una llamada. Todo se transcribe aquí y nunca se sube. Avisa a quien esté en la sala de que estás grabando.",
    "meetings.past": "Grabaciones anteriores",
    "meetings.none": "Todavía no hay grabaciones.",
    "meetings.length": "{minutes} min · {words} palabras",
    "meetings.decisions": "Decisiones",
    "meetings.actions": "Tareas acordadas",
    "meetings.truncated": "La grabación era larga, el resumen cubre solo una parte.",
    "meetings.summaryFailed": "No se pudo resumir: {error}",
    "meetings.showText": "Ver la transcripción completa",
    "meetings.hideText": "Ocultar la transcripción",
    "meetings.noText": "No se guardó ninguna transcripción.",
    "meetings.delete": "Borrar",
    "meetings.deleteConfirm":
      "¿Borrar “{label}” y su transcripción? Es la única copia.",
    "meetings.localNote":
      "La transcripción se hace en este equipo. Las grabaciones se quedan en tu carpeta de datos hasta que las borres.",
    "nav.planner": "Agenda",
    "planner.title": "Recordatorios y tareas",
    "planner.reminders": "Recordatorios",
    "planner.noReminders": "No hay nada programado.",
    "planner.repeats": "se repite",
    "planner.cancel": "Cancelar",
    "planner.cancelConfirm": "¿Cancelar el recordatorio “{label}”?",
    "planner.tasks": "Tareas",
    "planner.noTasks": "No hay nada en la lista.",
    "planner.add": "Añadir",
    "planner.addPlaceholder": "Algo que hacer — puedes usar #etiquetas",
    "planner.delete": "Borrar",
    "planner.deleteConfirm": "¿Borrar “{text}”? Completarla conserva el registro.",
    "planner.due": "para",
    "planner.showDone": "Ver {count} hechas",
    "planner.hideDone": "Ocultar hechas",
    "planner.mirrorNote":
      "También se escribe en tasks.md en tu carpeta de datos, para que la lista siga siendo legible si desinstalas Kai.",

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

    "avatar.needsCore":
      "El avatar necesita Cubism Core de Live2D, que tiene su propia licencia y no viene incluido. Colócalo en la carpeta live2d de la app para activarlo; todo lo demás funciona sin él.",
    "avatar.coreBlocked":
      "El avatar está instalado pero no puede arrancar: la política de seguridad de la app bloquea WebAssembly, que Cubism Core necesita. Es un fallo de la app, no algo que puedas arreglar aquí; todo lo demás funciona.",
    "avatar.coreFailed":
      "El avatar no pudo arrancar. Todo lo demás funciona sin él.",
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
    "settings.brainMissingOption": "{model} (no instalado)",
    "settings.brainMismatch":
      "Kai está configurado para usar {model}, que no está instalado. Instalados: {installed}. Elige uno arriba: nada más funcionará hasta que el modelo coincida.",
    "settings.skills": "Habilidades cargadas",
    "settings.config": "Archivo de configuración",
    "settings.data": "Tus datos están en",
    "settings.egress": "Lo que sale de este equipo",
    "settings.webSearch": "Búsqueda web",
    "settings.liveData": "Datos en vivo (divisas, clima)",
    "settings.cloudLlm": "Modelo en la nube",
    "settings.egressNote":
      "Todo lo que no aparece aquí se ejecuta localmente. Activar uno queda registrado en el log con la hora y el valor anterior.",
    "settings.files": "Archivos a los que Kai llega",
    "settings.allowedRoots": "Carpetas que Kai puede leer y organizar",
    "settings.allowedRootsNote":
      "Todas las habilidades de archivos se limitan a estas. Nada fuera de ellas se puede leer, mover ni renombrar.",
    "settings.indexedFolders": "Carpetas donde buscar documentos",
    "settings.indexedFoldersNote":
      "El contenido se indexa localmente para poder responder sobre tus documentos. La indexación corre en segundo plano.",
    "folders.none": "Ninguna.",
    "folders.add": "Añadir",
    "folders.remove": "Quitar",
    "folders.lastOne": "Hace falta al menos una carpeta.",
    "folders.placeholder": "C:/Users/tu/Documents",
    "updates.title": "Actualizaciones",
    "updates.installed_version": "Instalada",
    "updates.check": "Comprobar ahora",
    "updates.checking": "Comprobando…",
    "updates.upToDate": "Tienes la última versión.",
    "updates.available": "La versión {version} está disponible",
    "updates.install": "Descargar e instalar",
    "updates.installing": "Descargando… Kai se reiniciará al terminar.",
    "updates.installed": "Instalada. Reinicia Kai para terminar.",
    "updates.checkFailed": "No se pudo comprobar: {error}",
    "updates.installFailed": "No se pudo instalar: {error}",
    "updates.desktopOnly": "Las actualizaciones solo están en la app instalada.",
    "updates.note":
      "Consulta github.com/Gaboxqc/Jarvis, y solo cuando pulsas el botón: Kai nunca se conecta por su cuenta. Las actualizaciones van firmadas y se rechaza cualquiera que no verifique.",
    "startup.title": "Inicio",
    "startup.enable": "Abrir Kai al iniciar Windows",
    "startup.note":
      "Se abre minimizado en la bandeja. Sin esto, el atajo Ctrl+Alt+K no hace nada hasta que abras Kai tú mismo.",
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

    "clone.title": "Tu propia voz",
    "clone.notInstalled":
      "Hablar con una voz clonada necesita el motor XTTS, que no está instalado. Ocupa unos 2GB y es opcional: todo lo demás funciona sin él.",
    "clone.consent": "Permitir que Kai hable con una voz clonada",
    "clone.consentNote":
      "Una copia de una voz puede usarse para decir cosas que esa voz nunca dijo. Clona solo la tuya, o la de alguien presente que acceda. Al desmarcarlo se vuelve a la voz incluida de inmediato.",
    "clone.noReference": "Aún no hay grabación: con unos {seconds} segundos basta.",
    "clone.haveReference": "Grabado: {seconds} segundos.",
    "clone.choose": "Elegir un archivo .wav",
    "clone.replace": "Elegir otro archivo",
    "clone.uploading": "Subiendo…",
    "clone.uploaded": "Guardados {seconds} segundos. Las respuestas usarán esta voz.",
    "clone.uploadNote":
      "Un .wav de 16 bits con alguien hablando claro, diez segundos o más. Se guarda en este equipo y puedes borrarlo cuando quieras.",
    "clone.forget": "Borrar grabación",
    "clone.forgetConfirm":
      "¿Borrar la grabación de voz? Las respuestas volverán a la voz incluida.",
    "settings.voice": "Voz",
    "settings.voiceEnabled": "Hablar y escuchar",
    "settings.voiceInput": "Entrada de micrófono",
    "settings.voiceOutput": "Leer las respuestas en voz alta",
    "settings.voiceWake": "Palabra de activación",
    "settings.voiceWakeDownloading": "Descargando el modelo de palabra de activación… tarda un momento.",
    "settings.voiceWakeFailed":
      "No se pudo descargar el modelo, así que no se ha activado. Revisa la conexión e inténtalo de nuevo.",
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
