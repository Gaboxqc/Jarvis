/**
 * Mail and calendar accounts — REQ-13, REQ-26.
 *
 * There is no password field on this screen, and that is the whole design
 * rather than an omission. A password typed into a web form travels through a
 * request body, a validation layer and quite possibly a log line before it
 * reaches anywhere safe. So this collects the account *details*, and the
 * password is typed at an OS prompt by `/connect`, which puts it straight into
 * the Windows Credential Manager. The screen's job is to say so clearly enough
 * that nobody goes looking for the missing box.
 *
 * Calendars are CalDAV only, for the same reason. Google's "secret address in
 * iCal format" is a bearer credential — whoever holds the URL reads the whole
 * calendar — so it stays hand-edited in a gitignored file. The card says where.
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
    setForm({ ...BLANK, port: kind === "mail" ? "993" : "" });
    setNote(null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!adding || busy) return;

    const provider: AccountProvider = adding === "mail" ? "imap" : "caldav";
    setBusy(true);
    try {
      const added = await api.addAccount(adding, provider, {
        label: form.label.trim(),
        ...(adding === "mail"
          ? {
              host: form.host.trim(),
              port: Number(form.port) || 993,
              username: form.username.trim(),
              ...(form.smtp_host.trim() ? { smtp_host: form.smtp_host.trim() } : {}),
              ...(form.smtp_port.trim() ? { smtp_port: Number(form.smtp_port) } : {}),
            }
          : {
              url: form.url.trim(),
              username: form.username.trim(),
              writable: true,
            }),
      });
      setAdding(null);
      // The account exists but cannot sign in yet, and nothing else in the app
      // will mention it. Saying the exact command is the difference between a
      // working account and one that silently never connects.
      setNote(t("accounts.added", { command: added.next_step }));
      await load();
    } catch (caught) {
      setNote(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
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
              <div className="small">
                <code>/connect {account.kind} {account.label}</code>
              </div>
            )}
          </div>
          <button className="ghost" onClick={() => void remove(account)}>
            {t("accounts.remove")}
          </button>
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

          {adding === "mail" ? (
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
          ) : (
            <>
              {field("url", t("accounts.caldavUrl"), {
                required: true,
                placeholder: "https://caldav.fastmail.com/dav/",
              })}
              {field("username", t("accounts.username"), { required: true })}
              <p className="small muted">{t("accounts.icsNote")}</p>
            </>
          )}

          {/*
            Deliberately in place of a password field, not next to one. Someone
            filling this in will look for somewhere to type their password, and
            an unexplained absence reads as a broken form.
          */}
          <p className="small muted">{t("accounts.passwordNote")}</p>

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
