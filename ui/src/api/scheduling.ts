/** Reminders, routines, shortcuts and tasks. */

import { request } from "./client";
import { body } from "./schema";
import type { Reminder, RoutineSummary, ShortcutSummary, Task } from "./types";

export const scheduling = {
  routines: () => request<{ routines: RoutineSummary[] }>("/routines"),

  /** Re-approve a routine whose steps changed — the second half of REQ-12. */
  approveRoutine: (id: string) =>
    request<{ approved: boolean }>(`/routines/${encodeURIComponent(id)}/approve`, {
      method: "POST",
    }),

  runRoutine: (id: string) =>
    request<{ ran: number; skipped: number }>(`/routines/${encodeURIComponent(id)}/run`, {
      method: "POST",
    }),

  deleteRoutine: (id: string) =>
    request<{ deleted: string }>(`/routines/${encodeURIComponent(id)}`, { method: "DELETE" }),

  shortcuts: () => request<{ shortcuts: ShortcutSummary[] }>("/shortcuts"),

  runShortcut: (id: string) =>
    request<{ ran: number; skipped: number }>(`/shortcuts/${encodeURIComponent(id)}/run`, {
      method: "POST",
    }),

  /** Re-approve a shortcut whose steps changed — REQ-22, REQ-24. */
  approveShortcut: (id: string) =>
    request<{ approved: boolean }>(`/shortcuts/${encodeURIComponent(id)}/approve`, {
      method: "POST",
    }),

  deleteShortcut: (id: string) =>
    request<{ deleted: string }>(`/shortcuts/${encodeURIComponent(id)}`, { method: "DELETE" }),

  tasks: (includeDone = true) =>
    request<{ tasks: Task[] }>(`/tasks?include_done=${includeDone}`),

  addTask: (text: string) =>
    request<Task>("/tasks", { method: "POST", body: body<"TaskRequest">({ text }) }),

  setTaskDone: (id: string, done: boolean) =>
    request<Task>(`/tasks/${encodeURIComponent(id)}?done=${done}`, { method: "PATCH" }),

  deleteTask: (id: string) =>
    request<{ id: string }>(`/tasks/${encodeURIComponent(id)}`, { method: "DELETE" }),

  reminders: () => request<{ reminders: Reminder[] }>("/reminders"),

  cancelReminder: (id: string) =>
    request<{ cancelled: string }>(`/reminders/${encodeURIComponent(id)}`, { method: "DELETE" }),
};
