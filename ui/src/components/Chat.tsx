/**
 * Chat — REQ-1, REQ-24, REQ-27.
 *
 * This is where the Action Gate's contract meets a user interface, and the
 * important part is what the buttons send. "Go ahead" calls `confirm(actionId)`
 * with the id the backend issued for the action being previewed. There is no
 * code path here that sends a bare yes, so a stale render, a double click or a
 * race cannot approve something other than what is on screen.
 */

import { useEffect, useRef, useState } from "react";
import { api, ApiError, type PendingAction } from "../api";
import type { Key, Lang } from "../i18n";
import type { Voice } from "../useVoice";
import { Icon } from "./Icon";
import { Avatar, type AvatarState } from "./Avatar";
import { playEnvelope, stop as stopSpeaking } from "../speechLevel";
import { ensurePermission } from "../desktopNotify";

interface Message {
  id: number;
  who: "user" | "kai";
  text: string;
  error?: boolean;
  heard?: boolean;
}

interface Props {
  lang: Lang;
  t: (key: Key, vars?: Record<string, string | number>) => string;
  onBusyChange: (busy: boolean) => void;
  voice: Voice;
}

const SESSION = "ui";

export function Chat({ t, onBusyChange, voice }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  // Which slow phase is running. Routing and running a skill cannot stream, so
  // without this the window sits blank through the longest part of the turn.
  const [stage, setStage] = useState<string | null>(null);
  // Shown only when speaking is slow enough to need explaining — see maybeSpeak.
  const [preparingSpeech, setPreparingSpeech] = useState(false);

  const nextId = useRef(1);
  const logEnd = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const askedToNotify = useRef(false);

  // Both of these need braces. An arrow with an expression body returns that
  // expression, React takes an effect's return value to be its cleanup, and it
  // calls whatever it was given. Edge -- which is the engine behind the
  // packaged Windows build -- returns a Promise from `scrollIntoView`, so the
  // shorter spelling crashed the whole view on the second message with
  // "TypeError: y is not a function".
  useEffect(() => {
    onBusyChange(busy);
  }, [busy, onBusyChange]);

  // Leaving mid-sentence should close the mouth. The audio is the backend's
  // and carries on, but the avatar must not be left mouthing at nothing.
  useEffect(() => stopSpeaking, []);

  useEffect(() => {
    logEnd.current?.scrollIntoView({ block: "end" });
  }, [messages, pending]);

  // Move focus to the confirmation when one appears: a keyboard user must not
  // have to hunt for the thing that is blocking their request (REQ-28).
  useEffect(() => {
    if (pending) confirmRef.current?.focus();
    else inputRef.current?.focus();
  }, [pending]);

  function push(
    who: Message["who"],
    text: string,
    error = false,
    heard = false,
  ) {
    setMessages((prior) => [...prior, { id: nextId.current++, who, text, error, heard }]);
  }

  /** Say a reply aloud, if speech is on and it wasn't spoken already. */
  async function maybeSpeak(text: string, alreadySpoken = false) {
    if (!text || alreadySpoken || !voice.speaks) return;

    // Only announced if it is actually slow. The built-in voice answers in
    // well under a second and a flicker of "preparing" would be noise; a cloned
    // voice takes around thirty seconds on the processor, and silence for that
    // long reads as the assistant having stopped working.
    const announce = window.setTimeout(() => setPreparingSpeech(true), 1200);
    try {
      const shape = await api.speak(text);
      // The backend starts playing as this returns, so the envelope starts now.
      if (shape.spoke) playEnvelope(shape.envelope);
    } catch {
      // Losing the audio is a degradation; the reply is already on screen.
    } finally {
      window.clearTimeout(announce);
      setPreparingSpeech(false);
    }
  }

  function apply(result: Awaited<ReturnType<typeof api.turn>>) {
    // When an action is parked, the reply *is* the preview — and the panel
    // below already shows it. Pushing both printed the same paragraph twice.
    if (result.reply && !result.pending) {
      push("kai", result.reply, Boolean(result.error));
    }
    setPending(result.pending ?? null);
    // A confirmation is spoken too: with the screen not being looked at, an
    // unspoken "go ahead?" is a turn that silently stalls.
    void maybeSpeak(result.reply);
  }

  async function listen() {
    if (listening || busy) return;

    if (!voice.canListen) {
      // Offer the fix rather than refusing: the usual reason is that voice is
      // simply switched off, and the button they just pressed says what they want.
      if (voice.status && !voice.status.enabled && voice.status.models_ready) {
        try {
          await voice.setEnabled(true);
        } catch (error) {
          push("kai", error instanceof ApiError ? error.message : t("common.error"), true);
          return;
        }
      } else {
        push("kai", t((voice.blockedBecause ?? "voiceBlocked.offline") as Key), true);
        return;
      }
    }

    setListening(true);
    try {
      const turn = await api.listen();
      if (turn.heard) push("user", turn.heard, false, true);

      if (turn.error && !turn.reply) {
        push("kai", turn.error, true);
      } else if (turn.reply) {
        push("kai", turn.reply, Boolean(turn.error));
        // listen() already spoke it when output is enabled; speaking again
        // would repeat the whole reply over itself.
        void maybeSpeak(turn.reply, turn.spoke);
      }
      setPending(null);
      void api.state().catch(() => undefined);
    } catch (error) {
      push("kai", error instanceof ApiError ? error.message : t("voiceBlocked.failed"), true);
    } finally {
      setListening(false);
    }
  }

  // The avatar shows what the app already knows it is doing, so the two can
  // never disagree.
  const avatarState: AvatarState = listening
    ? "listening"
    : busy && stage === "writing"
      ? "speaking"
      : busy
        ? "thinking"
        : "idle";

  async function send(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;

    push("user", text);
    setDraft("");
    setBusy(true);
    setStage(null);

    // The reply is written into one message that grows, rather than a new
    // message per piece.
    const streamId = nextId.current++;
    let streamed = "";
    let showing = false;

    try {
      // The pending id travels with the turn, so "yes" typed into the box is
      // answered against the action actually on screen — never a later one.
      const result = await api.streamTurn(
        text,
        SESSION,
        {
          onStage: setStage,
          onDelta: (piece) => {
            streamed += piece;
            if (!showing) {
              showing = true;
              // The stage line has done its job the moment real text appears.
              setStage(null);
              setMessages((prior) => [
                ...prior,
                { id: streamId, who: "kai", text: streamed },
              ]);
            } else {
              setMessages((prior) =>
                prior.map((m) => (m.id === streamId ? { ...m, text: streamed } : m)),
              );
            }
          },
        },
        pending?.action_id ?? null,
      );

      // Settle on the authoritative text. The backend revises what it streamed —
      // memory receipts get appended, and an ungrounded answer is replaced
      // outright — so the streamed version is provisional until now.
      setMessages((prior) => {
        const rest = prior.filter((m) => m.id !== streamId);
        // When an action is parked the reply *is* the preview, and the panel
        // below already shows it; printing both duplicated the paragraph.
        if (result.reply && !result.pending) {
          return [
            ...rest,
            { id: streamId, who: "kai", text: result.reply, error: Boolean(result.error) },
          ];
        }
        return rest;
      });
      setPending(result.pending ?? null);
      // A confirmation is spoken too: with the screen not being looked at, an
      // unspoken "go ahead?" is a turn that silently stalls.
      void maybeSpeak(result.reply);

      // Ask for notification permission after the first turn has worked, not on
      // startup. A permission prompt that appears before the app has shown its
      // worth is the one people dismiss reflexively, and on Windows a denied
      // notification permission is awkward to reverse.
      if (!askedToNotify.current) {
        askedToNotify.current = true;
        void ensurePermission();
      }
    } catch (error) {
      setMessages((prior) => prior.filter((m) => m.id !== streamId));
      push("kai", error instanceof ApiError ? error.message : t("common.error"), true);
    } finally {
      setBusy(false);
      setStage(null);
    }
  }

  async function respond(approve: boolean) {
    if (!pending || busy) return;
    const action = pending;
    setBusy(true);
    setPending(null);
    try {
      apply(
        approve
          ? await api.confirm(action.action_id, SESSION)
          : await api.decline(action.action_id, SESSION),
      );
    } catch (error) {
      push("kai", error instanceof ApiError ? error.message : t("common.error"), true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="view">
      <h1 className="sr-only">{t("nav.chat")}</h1>

      <Avatar state={avatarState} t={t} />

      <div className="log" role="log" aria-label={t("chat.log")} aria-live="polite">
        {messages.length === 0 && !pending && (
          <p className="muted">{t("chat.empty")}</p>
        )}

        {messages.map((message) => (
          <div
            key={message.id}
            className={`msg ${message.who}${message.error ? " error" : ""}`}
          >
            <div className="who">
              {message.who === "user" ? t("chat.you") : "Kai"}
              {message.heard && <span className="tag"> {t("voice.heard")}</span>}
            </div>
            <div className="body">{message.text}</div>
          </div>
        ))}

        {pending && (
          <div className="confirm" role="alertdialog" aria-labelledby="confirm-title">
            <h2 id="confirm-title">{t("confirm.title")}</h2>
            <div className="preview">{pending.preview}</div>
            <p className="small muted">
              {pending.reversible ? t("confirm.undoable") : t("confirm.permanent")}
            </p>
            <div className="row">
              <button
                ref={confirmRef}
                className="primary"
                onClick={() => respond(true)}
                disabled={busy}
              >
                {t("confirm.yes")}
              </button>
              <button className="ghost" onClick={() => respond(false)} disabled={busy}>
                {t("confirm.no")}
              </button>
            </div>
          </div>
        )}

        {listening && <p className="listening small">{t("voice.listening")}…</p>}
        {preparingSpeech && (
          <p className="muted small" role="status">
            {t("clone.preparing")}…
          </p>
        )}
        {busy && (
          <p className="muted small" role="status">
            {stage === "routing"
              ? t("chat.stageRouting")
              : stage === "working"
                ? t("chat.stageWorking")
                : stage === "writing"
                  ? t("chat.stageWriting")
                  : t("chat.thinking")}
            …
          </p>
        )}
        <div ref={logEnd} />
      </div>

      <form className="composer" onSubmit={send}>
        <label className="sr-only" htmlFor="composer-input">
          {t("chat.placeholder")}
        </label>
        <input
          id="composer-input"
          ref={inputRef}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={t("chat.placeholder")}
          autoComplete="off"
          disabled={busy || listening}
        />
        <button
          type="button"
          className={listening ? "mic recording" : "mic"}
          onClick={listen}
          disabled={busy}
          aria-pressed={listening}
          title={voice.canListen ? t("voice.talk") : t((voice.blockedBecause ?? "voice.talk") as Key)}
        >
          <Icon name={listening ? "mic-off" : "mic"} />
          <span className="sr-only">{t("voice.talk")}</span>
        </button>
        <button
          className="primary"
          type="submit"
          disabled={busy || listening || !draft.trim()}
        >
          <Icon name="send" />
          <span className="sr-only">{t("chat.send")}</span>
        </button>
      </form>
    </div>
  );
}
