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
import { api, type Notification } from "../api";
import { notifyDesktop } from "../desktopNotify";
import type { Key } from "../i18n";

const POLL_MS = 5000;

export function Notifications({ t }: { t: (key: Key) => string }) {
  const [items, setItems] = useState<Notification[]>([]);

  useEffect(() => {
    let alive = true;

    async function drain() {
      try {
        const { notifications } = await api.notifications();
        if (alive && notifications.length) {
          setItems((prior) => [...prior, ...notifications]);

          // Raise the OS notification too, unless the window is right here in
          // front of the user — in which case the toast has already done the
          // job and a system banner on top of it is just noise.
          //
          // The queue is drained destructively, so this is the only chance to
          // do it: there is no second read of the same reminder.
          if (!document.hasFocus()) {
            for (const item of notifications) {
              void notifyDesktop(item.title, item.body);
            }
          }
        }
      } catch {
        // Backend down: the prerequisite banner already says so.
      }
    }

    void drain();
    const timer = setInterval(drain, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

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
