/**
 * Withdrawing the Live2D runtime licence — REQ-32.
 *
 * The avatar panel asks for acceptance; this is the other half. Consent that
 * cannot be taken back is not consent, and a decision recorded in a config file
 * the app says you should never have to open needs somewhere in the app to
 * undo it.
 *
 * Shows the date rather than a tick. "Accepted on the 3rd" is checkable against
 * what someone remembers doing; "accepted: yes" is not.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type AvatarLicence } from "../api";
import type { Key } from "../i18n";

interface Props {
  t: (key: Key, vars?: Record<string, string | number>) => string;
}

export function AvatarLicenceCard({ t }: Props) {
  const [licence, setLicence] = useState<AvatarLicence | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setLicence(await api.avatarLicence());
    } catch {
      // Optional feature: a card that cannot load its own state stays quiet
      // rather than pushing an error in front of unrelated settings.
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function set(accepted: boolean) {
    setBusy(true);
    setNote(null);
    try {
      setLicence(await api.acceptAvatarLicence(accepted));
    } catch (caught) {
      setNote(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  if (!licence) return null;

  // Dates are stored as UTC ISO; shown in the reader's own locale, because the
  // question this answers is "did I do this?", not "what did the server think".
  const when = licence.licence_accepted_at
    ? new Date(licence.licence_accepted_at).toLocaleDateString()
    : "";

  return (
    <section className="card">
      <h2 className="small muted">{t("settings.avatarLicence")}</h2>
      {note && (
        <p className="small" role="alert" style={{ color: "var(--danger)" }}>
          {note}
        </p>
      )}
      {licence.licence_accepted ? (
        <>
          <p className="small">{t("avatar.licenceAccepted", { date: when })}</p>
          <button className="ghost" onClick={() => void set(false)} disabled={busy}>
            {t("avatar.licenceWithdraw")}
          </button>
        </>
      ) : (
        <>
          <p className="small muted">{licence.licence_summary}</p>
          <button className="primary" onClick={() => void set(true)} disabled={busy}>
            {t("avatar.licenceAccept")}
          </button>
        </>
      )}
    </section>
  );
}
