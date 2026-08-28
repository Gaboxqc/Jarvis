/**
 * Missing-prerequisite banner — REQ-29, REQ-27.
 *
 * The installer bundles everything except the language model runtime, because
 * Ollama is its own installer and several gigabytes of model. T11.3 requires
 * that any prerequisite which cannot be bundled is stated clearly in the app,
 * so this is that statement.
 *
 * It names the actual remedy rather than reporting a failure. "Backend
 * unreachable" tells someone nothing; "install Ollama, then run `ollama pull
 * qwen2.5`" tells them what to do.
 */

import { useEffect, useRef, useState } from "react";
import { subscribe, subscribeConnection } from "../events";
import type { Key } from "../i18n";

type Problem = "none" | "starting" | "backend" | "model";

// How long to call a silent backend "starting" rather than "not running".
//
// This was 45s, on the assumption that a 550 MB bundle loading 48 skills takes
// tens of seconds to come up. Measured, it does not: the packaged backend
// accepts connections 1.65s after launch, and the source build in under a
// second. The 45s was really covering two other faults that have since been
// fixed -- the installed app was CORS-blocked from its own backend, and
// /health spent ~2s per call stalling on an IPv6 connection to Ollama that
// could never succeed.
//
// A grace window that is 27x too long is not harmless. It is the difference
// between "your backend is dead, here is what to do" and three quarters of a
// minute of a spinner that resolves into the same message anyway. 10s is
// generous against 1.65s and still covers a slow first launch while an
// antivirus scans the bundle.
const STARTUP_GRACE_MS = 10_000;

export function Prerequisite({ t }: { t: (key: Key) => string }) {
  const [problem, setProblem] = useState<Problem>("none");
  const [detail, setDetail] = useState("");
  const firstSeen = useRef(Date.now());

  // Two signals, from one connection rather than a ten-second poll (REQ-31).
  //
  // Whether the backend is there at all is now the stream itself: it either
  // stays open or it does not, which is both faster than a failing request and
  // one fewer request. Whether the *model* is there still has to be sampled,
  // because it is an outbound call to Ollama, so the backend does that on its
  // own cadence and pushes the answer only when it changes.
  //
  // The banner still clears itself once the user has followed the instructions,
  // for the same reason it always did: nobody should have to restart the app to
  // find out that they fixed it.
  useEffect(() => {
    const unwatch = subscribeConnection((status) => {
      if (status === "open") {
        setProblem((current) => (current === "backend" || current === "starting" ? "none" : current));
        return;
      }
      // Within the grace window a silent backend is almost certainly a cold
      // start rather than a dead one.
      const waited = Date.now() - firstSeen.current;
      setProblem(waited < STARTUP_GRACE_MS ? "starting" : "backend");
    });

    const unsubscribe = subscribe((event) => {
      if (event.type !== "health") return;
      if (event.ok) {
        setProblem("none");
      } else {
        setProblem("model");
        setDetail(event.error ?? "");
      }
    });

    return () => {
      unwatch();
      unsubscribe();
    };
  }, []);

  if (problem === "none") return null;

  return (
    <div className={problem === "starting" ? "card" : "banner"} role="status">
      <strong>
        {problem === "starting"
          ? t("prereq.startingTitle")
          : problem === "backend"
            ? t("prereq.backendTitle")
            : t("prereq.modelTitle")}
      </strong>
      <p className="small" style={{ margin: "0.35rem 0 0" }}>
        {problem === "starting"
          ? t("prereq.startingBody")
          : problem === "backend"
            ? t("prereq.backendBody")
            : t("prereq.modelBody")}
      </p>
      {detail && <p className="small muted" style={{ margin: "0.25rem 0 0" }}>{detail}</p>}
      {problem === "model" && (
        <p className="small" style={{ margin: "0.35rem 0 0" }}>
          <code>ollama pull qwen2.5</code> ·{" "}
          <a href="https://ollama.com" target="_blank" rel="noreferrer">
            ollama.com
          </a>
        </p>
      )}
    </div>
  );
}
