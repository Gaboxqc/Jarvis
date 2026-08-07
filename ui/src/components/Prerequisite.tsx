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
 * llama3`" tells them what to do.
 */

import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Key } from "../i18n";

type Problem = "none" | "starting" | "backend" | "model";

// The packaged backend loads 48 skills from a 550 MB bundle; a cold start
// takes tens of seconds. Calling that "not running" is wrong and sends the
// user chasing a fault that is about to resolve itself.
const STARTUP_GRACE_MS = 45_000;

export function Prerequisite({ t }: { t: (key: Key) => string }) {
  const [problem, setProblem] = useState<Problem>("none");
  const [detail, setDetail] = useState("");
  const firstSeen = useRef(Date.now());

  useEffect(() => {
    let alive = true;

    async function check() {
      try {
        const health = await api.health();
        if (!alive) return;
        if (!health.brain.ok) {
          setProblem("model");
          setDetail(health.brain.error ?? "");
        } else {
          setProblem("none");
        }
      } catch {
        if (!alive) return;
        // Within the grace window this is almost certainly a cold start.
        const waited = Date.now() - firstSeen.current;
        setProblem(waited < STARTUP_GRACE_MS ? "starting" : "backend");
      }
    }

    check();
    // Re-check periodically so the banner clears itself once the user has
    // followed the instructions, without needing a restart.
    const timer = setInterval(check, 10_000);
    return () => {
      alive = false;
      clearInterval(timer);
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
          <code>ollama pull llama3</code> ·{" "}
          <a href="https://ollama.com" target="_blank" rel="noreferrer">
            ollama.com
          </a>
        </p>
      )}
    </div>
  );
}
