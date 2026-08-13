/**
 * Mail and calendar accounts — REQ-13, REQ-26.
 *
 * This screen takes the password, which it previously refused to do.
 *
 * The old design sent people to a terminal: a secret typed into a form travels
 * through a request body and a validation layer, where one typed at a getpass
 * prompt goes straight from the keyboard to the OS store. That reasoning is
 * sound and was still the wrong call — an assistant whose accounts can only be
 * set up by running commands is not configurable by the people it is for, and
 * "secure but unused" is not secure.
 *
 * So the secret crosses one loopback hop and everything else is held tight
 * around it. It is held in component state only until submit and cleared
 * immediately after; it is never read back from the server, because no endpoint
 * returns it; and it never reaches kai.config.yaml, which keeps only a
 * reference. The backend binds 127.0.0.1 with a CORS allow-list, so no page on
 * the internet can reach the endpoint that accepts it.
 *
 * An iCal calendar address goes through the same path. Google calls it a
 * "secret address" and that is exact — whoever holds the URL reads the whole
 * calendar — so it is treated as a password rather than as a setting.
 */

import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  type Account,
  type AccountKind,
  type AccountProvider,
  type Connectors,
} from "../api";
import type { Key } from "../i18n";

interface Props {
  t: (key: Key, vars?: Record<string, string | number>) => string;
  configFile: string | null;
}

const BLANK = {
  label: "",
  host: "",
  port: "",
  username: "",
  smtp_host: "",
  smtp_port: "",
  url: "",
};

export function Accounts({ t, configFile }: Props) {
  const [connectors, setConnectors] = useState<Connectors | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [adding, setAdding] = useState<AccountKind | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ ...BLANK });
  // Held only until submit, then cleared. Never read back: no endpoint returns it.
  const [secret, setSecret] = useState("");
  const [provider, setProvider] = useState<AccountProvider>("imap");
  const [checking, setChecking] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setConnectors(await api.connectors());
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  function begin(kind: AccountKind) {
    setAdding(kind);
    setProvider(kind === "mail" ? "imap" : "ics");
    setForm({ ...BLANK, port: kind === "mail" ? "993" : "" });
    setSecret("");
    setNote(null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!adding || busy) return;

    setBusy(true);
    const label = form.label.trim();
    try {
      await api.addAccount(adding, provider, {
        label,
        ...(provider === "imap"
          ? {
              host: form.host.trim(),
              port: Number(form.port) || 993,
              username: form.username.trim(),
              ...(form.smtp_host.trim() ? { smtp_host: form.smtp_host.trim() } : {}),
              ...(form.smtp_port.trim() ? { smtp_port: Number(form.smtp_port) } : {}),
            }
          : provider === "caldav"
            ? { url: form.url.trim(), username: form.username.trim(), writable: true }
            : {}),
      });

      // Two calls rather than one: the account has to exist before it can have
      // a secret, and if the second fails the first is still worth keeping --
      // the account shows up as "needs a password" instead of vanishing.
      if (secret) {
        await api.setCredential(adding, label, secret);
      }
      setSecret("");
      setAdding(null);
      setNote(t("accounts.added", { label }));
      await load();
      // Say whether it actually works, now, rather than letting a wrong
      // password surface days later as an error about something else.
      void verify(adding, label);
    } catch (caught) {
      setNote(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  async function verify(kind: AccountKind, label: string) {
    setChecking(`${kind}:${label}`);
    try {
      const result = await api.checkAccount(kind, label);
      setNote(
        result.ok
          ? t("accounts.checkOk", { label })
          : t("accounts.checkFailed", { label, error: result.error ?? "" }),
      );
    } catch (caught) {
      setNote(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setChecking(null);
    }
  }

  async function remove(account: Account) {
    if (!window.confirm(t("accounts.removeConfirm", { label: account.label }))) return;
    try {
      await api.removeAccount(account.kind as AccountKind, account.label);
      setNote(t("accounts.removed", { label: account.label }));
      await load();
    } catch (caught) {
      setNote(caught instanceof ApiError ? caught.message : t("common.error"));
    }
  }

  function row(account: Account) {
    return (
      <li key={`${account.kind}-${account.label}`}>
        <div className="spread">
          <div>
            <strong>{account.label}</strong>{" "}
            <span className="tag">{account.provider}</span>{" "}
            {!account.credential_stored && (
              <span className="tag failed">{t("accounts.noPassword")}</span>
            )}
            <div className="small muted">
              {account.target}
              {account.username ? ` · ${account.username}` : ""}
            </div>
            {!account.credential_stored && (
              <div className="small">{t("accounts.needsSecret")}</div>
            )}
          </div>
          <div className="row">
            <button
              className="ghost"
              disabled={checking === `${account.kind}:${account.label}`}
              onClick={() => void verify(account.kind as AccountKind, account.label)}
            >
              {checking === `${account.kind}:${account.label}`
                ? t("accounts.checking")
                : t("accounts.check")}
            </button>
            <button className="ghost" onClick={() => void remove(account)}>
              {t("accounts.remove")}
            </button>
          </div>
        </div>
      </li>
    );
  }

  const field = (name: keyof typeof BLANK, label: string, extra: Record<string, unknown> = {}) => (
    <label className="stack">
      <span className="small">{label}</span>
      <input
        value={form[name]}
        onChange={(event) => setForm({ ...form, [name]: event.target.value })}
        {...extra}
      />
    </label>
  );

  return (
    <section className="card">
      <h2 className="small muted">{t("accounts.title")}</h2>
      {error && <div className="banner">{error}</div>}

      {connectors && !connectors.credential_store.available && (
        <div className="banner">{t("accounts.noStore")}</div>
      )}

      <h3 className="small">{t("accounts.mail")}</h3>
      {connectors?.mail.length ? (
        <ul className="plain">{connectors.mail.map(row)}</ul>
      ) : (
        <p className="small muted">{t("accounts.noneMail")}</p>
      )}

      <h3 className="small">{t("accounts.calendar")}</h3>
      {connectors?.calendar.length ? (
        <ul className="plain">{connectors.calendar.map(row)}</ul>
      ) : (
        <p className="small muted">{t("accounts.noneCalendar")}</p>
      )}

      {!adding && (
        <div className="row" style={{ marginTop: "0.75rem" }}>
          <button className="primary" onClick={() => begin("mail")}>
            {t("accounts.addMail")}
          </button>
          <button className="primary" onClick={() => begin("calendar")}>
            {t("accounts.addCalendar")}
          </button>
        </div>
      )}

      {adding && (
        <form onSubmit={submit} className="stack" style={{ marginTop: "0.75rem" }}>
          {field("label", t("accounts.label"), { required: true, autoFocus: true })}

          {adding === "calendar" && (
            <label className="stack">
              <span className="small">{t("accounts.calendarType")}</span>
              <select
                value={provider}
                onChange={(event) => setProvider(event.target.value as AccountProvider)}
              >
                <option value="ics">{t("accounts.typeIcs")}</option>
                <option value="caldav">{t("accounts.typeCaldav")}</option>
              </select>
            </label>
          )}

          {provider === "imap" && (
            <>
              {field("host", t("accounts.imapHost"), {
                required: true,
                placeholder: "imap.gmail.com",
              })}
              {field("port", t("accounts.port"), { inputMode: "numeric" })}
              {field("username", t("accounts.username"), {
                required: true,
                placeholder: "you@gmail.com",
              })}
              {field("smtp_host", t("accounts.smtpHost"), { placeholder: "smtp.gmail.com" })}
              {field("smtp_port", t("accounts.smtpPort"), { inputMode: "numeric" })}
            </>
          )}

          {provider === "caldav" && (
            <>
              {field("url", t("accounts.caldavUrl"), {
                required: true,
                placeholder: "https://caldav.fastmail.com/dav/",
              })}
              {field("username", t("accounts.username"), { required: true })}
            </>
          )}

          {/*
            The secret. Held in state only until submit, then cleared, and never
            read back -- no endpoint returns it. `type=password` for the
            password case; an iCal address is equally a credential but is long
            and pasted, so masking it would only make mistakes harder to see.
          */}
          <label className="stack">
            <span className="small">
              {provider === "ics" ? t("accounts.icsUrl") : t("accounts.password")}
            </span>
            <input
              type={provider === "ics" ? "text" : "password"}
              value={secret}
              onChange={(event) => setSecret(event.target.value)}
              required
              autoComplete="off"
              spellCheck={false}
              placeholder={
                provider === "ics"
                  ? "https://calendar.google.com/calendar/ical/.../basic.ics"
                  : ""
              }
            />
          </label>
          <p className="small muted">
            {provider === "ics" ? t("accounts.icsNote") : t("accounts.passwordNote")}
          </p>

          <div className="row">
            <button className="primary" type="submit" disabled={busy}>
              {busy ? t("common.loading") : t("accounts.save")}
            </button>
            <button className="ghost" type="button" onClick={() => setAdding(null)}>
              {t("confirm.no")}
            </button>
          </div>
        </form>
      )}

      {note && (
        <p className="small" role="status" style={{ marginTop: "0.5rem" }}>
          {note}
        </p>
      )}
      {configFile && (
        <p className="small muted" style={{ marginTop: "0.5rem" }}>
          {t("accounts.configAt")} <code>{configFile}</code>
        </p>
      )}
    </section>
  );
}
