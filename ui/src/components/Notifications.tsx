/**
 * Reminder toasts — REQ-9.
 *
 * The backend queues a notification when a scheduled item comes due; this
 * drains it. Without a consumer the API process fired reminders into nothing —
 * the item was marked delivered and the user was never told, which loses the
 * reminder rather than delaying it.
 *
 * Toasts stay until dismissed. A reminder that scrolls away on a timer is a
 * reminder you can miss by looking somewhere else for ten seconds.
 */

import { useEffect, useState } from "react";
import type { Notification } from "../api";
import { notifyDesktop } from "../desktopNotify";
import { subscribe } from "../events";
import type { Key } from "../i18n";

export function Notifications({ t }: { t: (key: Key) => string }) {
  const [items, setItems] = useState<Notification[]>([]);

  // The backend pushes these now rather than being asked every five seconds
  // (REQ-31). Draining is still destructive on the server, so this remains the
  // only chance to act on each one: there is no second read of a reminder.
  useEffect(
    () =>
      subscribe((event) => {
        if (event.type !== "notifications" || !event.items.length) return;
        setItems((prior) => [...prior, ...event.items]);

        // Raise the OS notification too, unless the window is right here in
        // front of the user — in which case the toast has already done the job
        // and a system banner on top of it is just noise.
        if (!document.hasFocus()) {
          for (const item of event.items) {
            void notifyDesktop(item.title, item.body);
          }
        }
      }),
    [],
  );

  if (!items.length) return null;

  return (
    <div className="toasts">
      {items.map((item) => (
        <div key={item.id} className="toast" role="alert">
          <div>
            <strong>{item.title}</strong>
            <div className="small">{item.body}</div>
          </div>
          <button
            className="ghost"
            onClick={() => setItems((prior) => prior.filter((n) => n.id !== item.id))}
          >
            {t("notify.dismiss")}
          </button>
        </div>
      ))}
    </div>
  );
}
