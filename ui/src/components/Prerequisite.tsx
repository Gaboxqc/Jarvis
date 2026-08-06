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

import { useEffect, useState } from "react";
import { api } from "../api";
import type { Key } from "../i18n";

type Problem = "none" | "backend" | "model";

export function Prerequisite({ t }: { t: (key: Key) => string }) {
  const [problem, setProblem] = useState<Problem>("none");
  const [detail, setDetail] = useState("");

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
        if (alive) setProblem("backend");
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
    <div className="banner" role="status">
      <strong>
        {problem === "backend" ? t("prereq.backendTitle") : t("prereq.modelTitle")}
      </strong>
      <p className="small" style={{ margin: "0.35rem 0 0" }}>
        {problem === "backend" ? t("prereq.backendBody") : t("prereq.modelBody")}
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
