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

interface Message {
  id: number;
  who: "user" | "kai";
  text: string;
  error?: boolean;
}

interface Props {
  lang: Lang;
  t: (key: Key, vars?: Record<string, string | number>) => string;
  onBusyChange: (busy: boolean) => void;
}

const SESSION = "ui";

export function Chat({ t, onBusyChange }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const nextId = useRef(1);
  const logEnd = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => onBusyChange(busy), [busy, onBusyChange]);
  useEffect(() => logEnd.current?.scrollIntoView({ block: "end" }), [messages, pending]);

  // Move focus to the confirmation when one appears: a keyboard user must not
  // have to hunt for the thing that is blocking their request (REQ-28).
  useEffect(() => {
    if (pending) confirmRef.current?.focus();
    else inputRef.current?.focus();
  }, [pending]);

  function push(who: Message["who"], text: string, error = false) {
    setMessages((prior) => [...prior, { id: nextId.current++, who, text, error }]);
  }

  function apply(result: Awaited<ReturnType<typeof api.turn>>) {
    // When an action is parked, the reply *is* the preview — and the panel
    // below already shows it. Pushing both printed the same paragraph twice.
    if (result.reply && !result.pending) {
      push("kai", result.reply, Boolean(result.error));
    }
    setPending(result.pending ?? null);
  }

  async function send(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;

    push("user", text);
    setDraft("");
    setBusy(true);
    try {
      // The pending id travels with the turn, so "yes" typed into the box is
      // answered against the action actually on screen — never a later one.
      apply(await api.turn(text, SESSION, pending?.action_id ?? null));
    } catch (error) {
      push("kai", error instanceof ApiError ? error.message : t("common.error"), true);
    } finally {
      setBusy(false);
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

      <div className="log" role="log" aria-label={t("chat.log")} aria-live="polite">
        {messages.length === 0 && !pending && (
          <p className="muted">{t("chat.empty")}</p>
        )}

        {messages.map((message) => (
          <div
            key={message.id}
            className={`msg ${message.who}${message.error ? " error" : ""}`}
          >
            <div className="who">{message.who === "user" ? t("chat.you") : "Kai"}</div>
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

        {busy && <p className="muted small">{t("chat.thinking")}…</p>}
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
          disabled={busy}
        />
        <button className="primary" type="submit" disabled={busy || !draft.trim()}>
          {t("chat.send")}
        </button>
      </form>
    </div>
  );
}
