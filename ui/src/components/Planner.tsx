/**
 * Tasks and reminders — REQ-9, REQ-10.
 *
 * These worked before this screen existed; you just had to describe what you
 * wanted in a sentence and hope the router agreed. Ticking something off is not
 * a thing anyone should have to phrase.
 *
 * The two lists sit together because they answer one question — what is on my
 * plate — and separating them would mean checking two places to find out. They
 * come from different stores (reminders are scheduled and fire; tasks are a
 * list) which is why they are not merged into one list here: a reminder that
 * has fired disappears, and a task that is done stays and gets a line through
 * it. Presenting them identically would make that difference look like a bug.
 *
 * Nothing here goes through the Action Gate. The gate exists so the assistant
 * cannot act without the user knowing, and a person pressing "Done" on a row
 * they are looking at already knows. Deleting still asks, because that is the
 * one that loses something.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Reminder, type RoutineSummary, type Task } from "../api";
import type { Key } from "../i18n";

interface Props {
  t: (key: Key, vars?: Record<string, string | number>) => string;
}

export function Planner({ t }: Props) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showDone, setShowDone] = useState(false);
  const [routines, setRoutines] = useState<RoutineSummary[]>([]);
  const [routineNote, setRoutineNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [taskList, reminderList, routineList] = await Promise.all([
        api.tasks(),
        api.reminders(),
        api.routines().catch(() => ({ routines: [] })),
      ]);
      setRoutines(routineList.routines);
      setTasks(taskList.tasks);
      setReminders(reminderList.reminders);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  function add(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    void run(() => api.addTask(text));
  }

  function removeTask(task: Task) {
    // Completing is one click and reversible; deleting is neither.
    if (!window.confirm(t("planner.deleteConfirm", { text: task.text }))) return;
    void run(() => api.deleteTask(task.id));
  }

  function removeReminder(reminder: Reminder) {
    if (!window.confirm(t("planner.cancelConfirm", { label: reminder.label }))) return;
    void run(() => api.cancelReminder(reminder.id));
  }

  const open = tasks.filter((task) => !task.done);
  const done = tasks.filter((task) => task.done);
  const shown = showDone ? [...open, ...done] : open;

  /** "Thu 14 Aug, 17:00" — a date nobody has to decode. */
  function when(iso: string | null) {
    if (!iso) return "—";
    const date = new Date(iso);
    return date.toLocaleString(undefined, {
      weekday: "short", day: "numeric", month: "short",
      hour: "2-digit", minute: "2-digit",
    });
  }

  return (
    <div className="view">
      <h1>{t("planner.title")}</h1>
      {error && <div className="banner">{error}</div>}

      <section className="card">
        <h2 className="small muted">{t("planner.reminders")}</h2>
        {reminders.length === 0 ? (
          <p className="small muted">{t("planner.noReminders")}</p>
        ) : (
          <ul className="plain">
            {reminders.map((reminder) => (
              <li key={reminder.id}>
                <div className="spread">
                  <div>
                    <div>{reminder.label}</div>
                    <div className="small muted">
                      {when(reminder.next_fire_at)}
                      {reminder.recurring && ` · ${t("planner.repeats")}`}
                    </div>
                  </div>
                  <button
                    className="ghost"
                    disabled={busy}
                    onClick={() => removeReminder(reminder)}
                  >
                    {t("planner.cancel")}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card">
        <h2 className="small muted">{t("planner.routines")}</h2>
        {routines.length === 0 ? (
          <p className="small muted">{t("planner.noRoutines")}</p>
        ) : (
          <ul className="plain">
            {routines.map((routine) => (
              <li key={routine.id}>
                <div className="spread">
                  <div>
                    <div>{routine.label}</div>
                    <div className="small muted">
                      {when(routine.next_fire_at)}
                      {routine.recurring && ` · ${t("planner.repeats")}`}
                      {` · ${t("planner.routineSteps", { count: routine.steps.length })}`}
                    </div>
                    {/* REQ-12: an edited routine skips its gated steps until it
                        is approved again, and saying so is the whole point —
                        silently doing less is the failure mode here. */}
                    {routine.needs_approval && (
                      <div className="small" role="status">
                        {t("planner.routineNeedsApproval")}
                      </div>
                    )}
                  </div>
                  <div className="row">
                    {routine.needs_approval && (
                      <button
                        className="primary"
                        disabled={busy}
                        onClick={() => {
                          setRoutineNote(null);
                          void api.approveRoutine(routine.id).then(load).catch(() => undefined);
                        }}
                      >
                        {t("planner.routineApprove")}
                      </button>
                    )}
                    <button
                      disabled={busy}
                      onClick={() => {
                        setRoutineNote(null);
                        void api
                          .runRoutine(routine.id)
                          .then((r) =>
                            setRoutineNote(
                              t("planner.routineRan", { ran: r.ran, skipped: r.skipped }),
                            ),
                          )
                          .catch(() => undefined);
                      }}
                    >
                      {t("planner.routineRun")}
                    </button>
                    <button
                      className="ghost"
                      disabled={busy}
                      onClick={() => {
                        if (!window.confirm(t("planner.routineDeleteConfirm"))) return;
                        void api.deleteRoutine(routine.id).then(load).catch(() => undefined);
                      }}
                    >
                      {t("planner.cancel")}
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
        {routineNote && (
          <p className="small" role="status">
            {routineNote}
          </p>
        )}
      </section>

      <section className="card">
        <div className="spread">
          <h2 className="small muted">{t("planner.tasks")}</h2>
          {done.length > 0 && (
            <button className="ghost" onClick={() => setShowDone(!showDone)}>
              {showDone
                ? t("planner.hideDone")
                : t("planner.showDone", { count: done.length })}
            </button>
          )}
        </div>

        <form className="row" onSubmit={add} style={{ marginBottom: "0.6rem" }}>
          <label className="sr-only" htmlFor="task-input">
            {t("planner.addPlaceholder")}
          </label>
          <input
            id="task-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={t("planner.addPlaceholder")}
            style={{ flex: 1 }}
          />
          <button className="primary" type="submit" disabled={busy || !draft.trim()}>
            {t("planner.add")}
          </button>
        </form>

        {shown.length === 0 ? (
          <p className="small muted">{t("planner.noTasks")}</p>
        ) : (
          <ul className="plain">
            {shown.map((task) => (
              <li key={task.id}>
                <div className="spread">
                  <label className="row" style={{ gap: "0.5rem", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={task.done}
                      disabled={busy}
                      onChange={(event) =>
                        void run(() => api.setTaskDone(task.id, event.target.checked))
                      }
                    />
                    <span
                      style={{
                        textDecoration: task.done ? "line-through" : undefined,
                        opacity: task.done ? 0.6 : 1,
                      }}
                    >
                      {task.text}
                      {task.tags.map((tag) => (
                        <span key={tag} className="tag" style={{ marginLeft: "0.4rem" }}>
                          {tag}
                        </span>
                      ))}
                      {task.due && (
                        <span className="small muted"> · {t("planner.due")} {task.due}</span>
                      )}
                    </span>
                  </label>
                  <button className="ghost" disabled={busy} onClick={() => removeTask(task)}>
                    {t("planner.delete")}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
        <p className="small muted">{t("planner.mirrorNote")}</p>
      </section>
    </div>
  );
}
