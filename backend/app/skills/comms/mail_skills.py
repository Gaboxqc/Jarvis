"""Mail skills — REQ-13, REQ-14, REQ-24.

Triage and drafting are cheap and reversible. Sending is not, and it is the one
action in the whole system that can never be waved through by a standing
pre-approval: REQ-14 requires confirmation *per message*, so `mail.send` sets
`allow_pre_approval = False` and the gate refuses to skip it.

Reading never marks anything as read. `BODY.PEEK` is used throughout, because
summarising someone's inbox should not quietly change its state.
"""

from __future__ import annotations

from typing import Any

from ...connectors import base as connectors
from ...connectors import mail
from ..base import Severity, Skill, SkillContext, SkillError, SkillParam, SkillResult

BULK_THRESHOLD = 3


class InboxSkill(Skill):
    name = "mail.inbox"
    description = (
        "Summarise unread mail, split into what looks like it needs a reply and what "
        "does not. Use for 'anything important in my inbox', 'what's new in mail'."
    )
    parameters = (
        SkillParam("account", "string", "Which account, if more than one.", required=False),
        SkillParam("limit", "integer", "How many messages to look at (default 25).",
                   required=False, default=25),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        config = connectors.find("mail", str(args.get("account", "") or ""))
        limit = max(1, min(int(args.get("limit", 25) or 25), 100))

        messages = mail.fetch_unread(config, limit=limit)
        if not messages:
            return SkillResult(ok=True, message="No unread mail.", data={"messages": []})

        needs_reply = [m for m in messages if m.probably_needs_reply]
        automated = [m for m in messages if m.looks_automated]
        other = [m for m in messages if m not in needs_reply and m not in automated]

        lines = [f"{len(messages)} unread in {config.label}."]
        if needs_reply:
            lines.append(f"\nLooks like it needs a reply ({len(needs_reply)}):")
            lines += [f"  {m.describe()}" for m in needs_reply]
        if other:
            lines.append(f"\nProbably just to read ({len(other)}):")
            lines += [f"  {m.describe()}" for m in other[:10]]
        if automated:
            lines.append(f"\n{len(automated)} automated/newsletters.")

        return SkillResult(
            ok=True,
            message="\n".join(lines),
            data={
                "messages": [m.to_dict() for m in messages],
                "needs_reply": [m.uid for m in needs_reply],
            },
        )


class ReadMailSkill(Skill):
    name = "mail.read"
    description = (
        "Find and summarise specific messages by sender, subject or content. Use for "
        "'what did Ana say about the invoice', 'find the email about the lease'."
    )
    parameters = (
        SkillParam("query", "string", "Words to search for."),
        SkillParam("account", "string", "Which account.", required=False),
        SkillParam("limit", "integer", "How many (default 5).", required=False, default=5),
        SkillParam("days", "integer", "How far back to look (default 365).",
                   required=False, default=365),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        config = connectors.find("mail", str(args.get("account", "") or ""))
        query = str(args["query"]).strip()
        limit = max(1, min(int(args.get("limit", 5) or 5), 20))
        days = max(1, int(args.get("days", 365) or 365))

        messages = mail.search_messages(config, query, limit=limit, days=days)
        if not messages:
            return SkillResult(ok=True, message=f"No mail matching '{query}'.",
                               data={"messages": []})

        blocks = [
            f"From {m.sender_name or m.sender} - {m.subject}\n{m.body[:1200]}"
            for m in messages
        ]
        return SkillResult(
            ok=True,
            message="\n\n".join(blocks),
            data={"messages": [m.to_dict() for m in messages]},
        )


class FlagMailSkill(Skill):
    name = "mail.mark"
    description = (
        "Mark messages as read or unread, or flag/unflag them. Matched by a search term."
    )
    parameters = (
        SkillParam("query", "string", "Which messages (sender, subject words)."),
        SkillParam("action", "string", "What to do.",
                   enum=("read", "unread", "flag", "unflag")),
        SkillParam("account", "string", "Which account.", required=False),
    )
    consequential = True
    reversible = True

    _FLAGS = {"read": ("\\Seen", True), "unread": ("\\Seen", False),
              "flag": ("\\Flagged", True), "unflag": ("\\Flagged", False)}

    def _targets(self, args: dict[str, Any]) -> tuple[Any, list[mail.Message]]:
        config = connectors.find("mail", str(args.get("account", "") or ""))
        found = mail.search_messages(config, str(args.get("query", "")), limit=50)
        return config, found

    def severity(self, args: dict[str, Any]) -> Severity:
        # Changing a couple of flags is trivially reversible and not worth a
        # prompt; doing it to forty messages is a bulk change to the user's mail.
        try:
            _, found = self._targets(args)
        except connectors.ConnectorError:
            return "consequential"
        return "consequential" if len(found) > BULK_THRESHOLD else "routine"

    def preview(self, args: dict[str, Any]) -> str:
        try:
            _, found = self._targets(args)
        except connectors.ConnectorError as exc:
            return f"Mark messages - but: {exc}"
        return (
            f"Mark {len(found)} message(s) matching '{args.get('query')}' "
            f"as {args.get('action')}."
        )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        action = str(args["action"])
        if action not in self._FLAGS:
            raise SkillError(f"'{action}' isn't something I can do to a message.")

        config, found = self._targets(args)
        if not found:
            raise SkillError(f"No messages match '{args.get('query')}'.")

        flag, add = self._FLAGS[action]
        uids = [m.uid for m in found]
        changed = mail.modify_flags(config, uids, flag, add=add)

        return SkillResult(
            ok=True,
            message=f"Marked {changed} message(s) as {action}.",
            data={"changed": changed},
            undo_payload={"account": config.label, "uids": uids, "flag": flag, "add": add},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        config = connectors.find("mail", str(undo_payload.get("account", "")))
        restored = mail.modify_flags(
            config,
            list(undo_payload.get("uids", [])),
            str(undo_payload.get("flag", "")),
            add=not bool(undo_payload.get("add", True)),
        )
        return SkillResult(ok=True, message=f"Put {restored} message(s) back as they were.")


class DraftMailSkill(Skill):
    name = "mail.draft"
    description = (
        "Write a draft reply or new message for the user to review. This only "
        "produces text - it never sends anything. Always use this before mail.send."
    )
    parameters = (
        SkillParam("to", "string", "Recipient address."),
        SkillParam("subject", "string", "Subject line."),
        SkillParam("body", "string", "The message text, written in the user's voice."),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        to = str(args["to"]).strip()
        subject = str(args["subject"]).strip()
        body = str(args["body"]).strip()
        if not body:
            raise SkillError("There was nothing to put in the message.")

        return SkillResult(
            ok=True,
            message=f"Draft to {to}\nSubject: {subject}\n\n{body}\n\n"
                    "(Nothing has been sent. Say send it if you want it to go.)",
            data={"to": to, "subject": subject, "body": body, "sent": False},
        )


class SendMailSkill(Skill):
    name = "mail.send"
    description = (
        "Send a message that the user has already seen and approved. Never call this "
        "without having shown them the draft first."
    )
    parameters = (
        SkillParam("to", "string", "Recipient address."),
        SkillParam("subject", "string", "Subject line."),
        SkillParam("body", "string", "The exact message text to send."),
        SkillParam("account", "string", "Which account to send from.", required=False),
    )
    consequential = True
    reversible = False
    # REQ-14: confirmation is per message. A standing approval must not be able
    # to satisfy it, so this skill opts out of the pre-approval mechanism
    # entirely -- there is no configuration that makes mail send silently.
    allow_pre_approval = False

    def preview(self, args: dict[str, Any]) -> str:
        body = str(args.get("body", ""))
        excerpt = body if len(body) <= 300 else body[:300] + "..."
        try:
            config = connectors.find("mail", str(args.get("account", "") or ""))
            sender = config.from_address or config.username
        except connectors.ConnectorError:
            sender = "your account"
        return (
            f"Send this email from {sender} to {args.get('to')}:\n"
            f"Subject: {args.get('subject')}\n\n{excerpt}\n\n"
            "Once sent it cannot be recalled."
        )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        config = connectors.find("mail", str(args.get("account", "") or ""))
        to = str(args["to"]).strip()
        if "@" not in to:
            raise SkillError(f"'{to}' doesn't look like an email address.")

        message = mail.build_draft(
            config, to=to, subject=str(args["subject"]), body=str(args["body"])
        )
        mail.send(config, message)
        return SkillResult(
            ok=True,
            message=f"Sent to {to}.",
            data={"to": to, "subject": str(args["subject"]), "sent": True},
        )
