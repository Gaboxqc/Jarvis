/**
 * The icon set — REQ-28.
 *
 * Until now every control in this app was text, and the microphone button was
 * a bullet character: `●`. It read as a decoration rather than a control, and
 * nobody looking at it would guess it started a recording.
 *
 * Inline SVG rather than an icon font or a package. A font brings a network
 * request or a binary blob for fifteen glyphs, and this app already refuses to
 * fetch anything it does not have to. These are a few hundred bytes of paths
 * that inherit `currentColor`, so they follow the theme — including the
 * high-contrast one — without any of them knowing a colour.
 *
 * `stroke-width` is 1.75 and every path is drawn on a 24-unit grid, so the set
 * looks like one family rather than fifteen decisions. Anything added later
 * should keep both.
 */

import type { ReactNode } from "react";

interface Props {
  name: IconName;
  /** In `em`, so an icon scales with the text it sits beside. */
  size?: number;
  className?: string;
}

export type IconName =
  | "mic"
  | "mic-off"
  | "send"
  | "speaker"
  | "speaker-off"
  | "chat"
  | "today"
  | "planner"
  | "documents"
  | "memory"
  | "history"
  | "settings"
  | "check"
  | "trash"
  | "plus"
  | "refresh"
  | "upload"
  | "meetings";

const PATHS: Record<IconName, ReactNode> = {
  mic: (
    <>
      <rect x="9" y="2" width="6" height="11" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0" />
      <path d="M12 17v4M8 21h8" />
    </>
  ),
  "mic-off": (
    <>
      <path d="M9 9v1a3 3 0 0 0 4.2 2.75" />
      <path d="M15 10.5V5a3 3 0 0 0-5.9-.7" />
      <path d="M5 10a7 7 0 0 0 10.9 5.8M19 10a7 7 0 0 1-.5 2.6" />
      <path d="M12 17v4M8 21h8M3 3l18 18" />
    </>
  ),
  send: <path d="M4 12l16-8-6 8 6 8-16-8z" />,
  speaker: (
    <>
      <path d="M4 9v6h4l5 4V5L8 9H4z" />
      <path d="M17 8.5a5 5 0 0 1 0 7M20 6a9 9 0 0 1 0 12" />
    </>
  ),
  "speaker-off": (
    <>
      <path d="M4 9v6h4l5 4V5L8 9H4z" />
      <path d="M17 9.5l4 5M21 9.5l-4 5" />
    </>
  ),
  chat: <path d="M4 5h16v11H9l-5 4V5z" />,
  today: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </>
  ),
  planner: (
    <>
      <path d="M4 6h16M4 12h16M4 18h10" />
      <path d="M2.5 6l.9.9L5 5.2" />
    </>
  ),
  documents: (
    <>
      <path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9l-6-6z" />
      <path d="M13 3v6h6" />
    </>
  ),
  memory: (
    <>
      <path d="M12 4a4 4 0 0 0-4 4 3.5 3.5 0 0 0-1 6.8V17a3 3 0 0 0 5 2.2" />
      <path d="M12 4a4 4 0 0 1 4 4 3.5 3.5 0 0 1 1 6.8V17a3 3 0 0 1-5 2.2" />
      <path d="M12 4v16" />
    </>
  ),
  history: (
    <>
      <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
      <path d="M3 4v4h4M12 7v5l3 2" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" />
    </>
  ),
  check: <path d="M4 12.5l5 5L20 6.5" />,
  trash: (
    <>
      <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />
      <path d="M10 11v6M14 11v6" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  refresh: (
    <>
      <path d="M20 12a8 8 0 1 1-2.3-5.6" />
      <path d="M20 4v5h-5" />
    </>
  ),
  meetings: (
    <>
      <rect x="9" y="2" width="6" height="11" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0M12 17v4" />
      <circle cx="18.5" cy="18.5" r="3.5" />
    </>
  ),
  upload: (
    <>
      <path d="M12 16V4M7 9l5-5 5 5" />
      <path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
    </>
  ),
};

export function Icon({ name, size = 1.15, className }: Props) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={`${size}em`}
      height={`${size}em`}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      // Decorative by default: every icon in this app sits next to a label or
      // a button with an accessible name, so announcing it again would just
      // make a screen reader repeat itself.
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
