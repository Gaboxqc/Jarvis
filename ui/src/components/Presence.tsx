/**
 * Presence indicator — REQ-32.
 *
 * The isolation boundary REQ-32 asks for is visible in this file's imports: it
 * takes a state name and an optional emotion tag, and knows nothing about the
 * brain, the skills or the actions. Replacing this with a full animated
 * character means replacing this file and nothing else.
 */

import { useEffect, useState } from "react";
import type { PresenceState } from "../api";
import { subscribe, subscribeConnection } from "../events";
import type { Key, Lang } from "../i18n";

interface Props {
  busy: boolean;
  lang: Lang;
  t: (key: Key) => string;
}

export function Presence({ busy, t }: Props) {
  const [state, setState] = useState<PresenceState | null>(null);
  const [offline, setOffline] = useState(false);

  // Pushed, not polled. This asked the backend every two seconds what it was
  // doing, which for an assistant that is idle most of the day is 30 questions
  // a minute with the same answer (REQ-31).
  useEffect(() => {
    const unsubscribe = subscribe((event) => {
      if (event.type === "state") {
        const { type: _type, ...rest } = event;
        setState(rest as PresenceState);
      }
    });
    const unwatch = subscribeConnection((status) => setOffline(status === "down"));
    return () => {
      unsubscribe();
      unwatch();
    };
  }, []);

  // A turn in flight is the truest "thinking" signal the UI has, and it needs
  // no round trip to know it.
  const name = offline ? "offline" : busy ? "thinking" : (state?.state ?? "idle");
  const label = offline
    ? t("state.offline")
    : t(`state.${name}` as Key);

  return (
    <div className="presence">
      <span className={`dot ${name}`} aria-hidden="true" />
      <span className="small muted" role="status" aria-live="polite">
        {label}
        {state?.focus && !offline ? ` · ${t("state.focus")}` : ""}
      </span>
    </div>
  );
}
