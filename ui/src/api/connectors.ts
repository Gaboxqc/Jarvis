/** Mail and calendar accounts, and the briefing over them. */

import { request } from "./client";
import { body } from "./schema";
import type { AccountFields, AccountKind, AccountProvider, AddedAccount, BriefingSection, Connectors } from "./types";

export const connectors = {
  connectors: () => request<Connectors>("/connectors"),

  /**
   * Add a mail or calendar account.
   *
   * `fields` carries account details only. There is no password parameter and
   * there must never be one: the backend refuses any field whose name looks
   * like a secret, and the password is typed at an OS prompt by `/connect`,
   * which writes it to the Windows Credential Manager. The response says so in
   * `next_step`.
   */
  addAccount: (kind: AccountKind, provider: AccountProvider, fields: AccountFields) =>
    request<AddedAccount>("/connectors/accounts", {
      method: "POST",
      // `fields` spreads into a plain object because the schema types it as
      // `Record<string, unknown>` while AccountFields is a precise shape.
      // Widening is the safe direction -- the client is stricter than the API
      // about what may be sent, which is the way round it should be.
      body: body<"AccountRequest">({ kind, provider, fields: { ...fields } }),
    }),

  /**
   * Store an account's secret.
   *
   * It leaves this process once, over loopback, and the backend writes it to
   * the OS credential store. No endpoint returns it afterwards and it never
   * reaches kai.config.yaml. Callers should not keep it in state longer than
   * the submit.
   */
  setCredential: (kind: AccountKind, label: string, secret: string) =>
    request<{ stored: boolean; credential_ref: string }>(
      `/connectors/${kind}/${encodeURIComponent(label)}/credential`,
      { method: "PUT", body: body<"CredentialRequest">({ secret }) },
    ),

  /** Try the account and report whether it actually works. */
  checkAccount: (kind: AccountKind, label: string) =>
    request<{ ok: boolean; error?: string; unread?: number; events?: number }>(
      `/connectors/${kind}/${encodeURIComponent(label)}/check`,
      { method: "POST", timeoutMs: 60_000 },
    ),

  removeAccount: (kind: AccountKind, label: string) =>
    request<{ removed: string }>(
      `/connectors/accounts/${kind}/${encodeURIComponent(label)}`,
      { method: "DELETE" },
    ),

  briefing: () => request<{ sections: BriefingSection[] }>("/briefing", { timeoutMs: 60_000 }),
};
