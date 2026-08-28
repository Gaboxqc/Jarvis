/**
 * The Action Gate, from the client side — REQ-24.
 *
 * The backend's half of this contract has been tested since Phase 3: approval
 * binds to one action id, the id is consumed, and no call shape can approve
 * something the user was not shown. None of that helps if the button sends the
 * wrong id, sends a bare yes, or leaves a stale confirmation on screen that a
 * second click can approve after the first one already ran.
 *
 * That half lived in ~5,600 lines of TypeScript with no tests at all. These are
 * about what the buttons send, and nothing else.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Chat } from "./Chat";
import { api } from "../api";
import { translate } from "../i18n";

// The avatar pulls in PIXI and a WebGL canvas, and this file has nothing to say
// about either.
vi.mock("./Avatar", () => ({ Avatar: () => null }));
vi.mock("../speechLevel", () => ({ playEnvelope: vi.fn(), stop: vi.fn() }));
vi.mock("../desktopNotify", () => ({ ensurePermission: vi.fn(async () => false) }));

const PENDING = {
  action_id: "act-2f9c",
  skill: "system.organize_folder",
  preview: "Move 12 files in Downloads into folders by type",
  reversible: true,
};

const t = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("en", key, vars);

const voice = {
  speaks: false,
  busy: false,
  listening: false,
  setSpeaks: vi.fn(async () => undefined),
  listen: vi.fn(),
  speak: vi.fn(async () => undefined),
} as never;

function renderChat() {
  return render(<Chat lang="en" t={t} onBusyChange={() => {}} voice={voice} />);
}

/** Drive one turn that comes back needing approval, and wait for the prompt. */
async function askUntilPending(user: ReturnType<typeof userEvent.setup>) {
  vi.spyOn(api, "streamTurn").mockResolvedValue({
    reply: "That needs your say-so.",
    needs_confirmation: true,
    pending: PENDING,
    skill_calls: [],
    error: null,
  } as never);

  await user.type(screen.getByRole("textbox"), "tidy up my downloads");
  await user.keyboard("{Enter}");
  await screen.findByText(PENDING.preview);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("the confirmation prompt", () => {
  it("sends the action id the backend issued, never a bare yes", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(api, "confirm").mockResolvedValue({
      reply: "Moved 12 files.",
      needs_confirmation: false,
      pending: null,
      skill_calls: [],
      error: null,
    } as never);

    renderChat();
    await askUntilPending(user);
    await user.click(screen.getByRole("button", { name: t("confirm.yes") }));

    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(confirm).toHaveBeenCalledWith(PENDING.action_id, expect.any(String));
    // The whole point of the contract: the first argument is an id, not a
    // boolean and not the word the user clicked.
    expect(confirm.mock.calls[0][0]).toBe("act-2f9c");
    await screen.findByText("Moved 12 files.");
  });

  it("declines through the same id, and executes nothing", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(api, "confirm");
    const decline = vi.spyOn(api, "decline").mockResolvedValue({
      reply: "Cancelled. I didn't move anything.",
      needs_confirmation: false,
      pending: null,
      skill_calls: [],
      error: null,
    } as never);

    renderChat();
    await askUntilPending(user);
    await user.click(screen.getByRole("button", { name: t("confirm.no") }));

    await waitFor(() => expect(decline).toHaveBeenCalledWith(PENDING.action_id, expect.any(String)));
    expect(confirm).not.toHaveBeenCalled();
    await screen.findByText("Cancelled. I didn't move anything.");
  });

  it("cannot be approved twice by clicking twice", async () => {
    const user = userEvent.setup();
    let release: (value: unknown) => void = () => {};
    const confirm = vi.spyOn(api, "confirm").mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }) as never,
    );

    renderChat();
    await askUntilPending(user);
    const yes = screen.getByRole("button", { name: t("confirm.yes") });
    await user.click(yes);
    await user.click(yes);   // the impatient second click, mid-flight

    expect(confirm).toHaveBeenCalledTimes(1);
    release({
      reply: "Moved 12 files.",
      needs_confirmation: false,
      pending: null,
      skill_calls: [],
      error: null,
    });
    await screen.findByText("Moved 12 files.");
  });

  it("disappears once answered, so nothing stale is left to approve", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "decline").mockResolvedValue({
      reply: "Cancelled.",
      needs_confirmation: false,
      pending: null,
      skill_calls: [],
      error: null,
    } as never);

    renderChat();
    await askUntilPending(user);
    await user.click(screen.getByRole("button", { name: t("confirm.no") }));

    await waitFor(() =>
      expect(screen.queryByText(PENDING.preview)).not.toBeInTheDocument(),
    );
    await screen.findByText("Cancelled.");
  });

  it("says whether the action can be undone, before it is approved", async () => {
    const user = userEvent.setup();
    renderChat();
    await askUntilPending(user);

    // REQ-25: reversibility is stated at the confirmation step, not discovered
    // afterwards. PENDING is reversible, so it must not read as permanent.
    expect(screen.getByText(t("confirm.undoable"))).toBeInTheDocument();
    expect(screen.queryByText(t("confirm.permanent"))).not.toBeInTheDocument();
  });

  it("carries the pending id when the user types yes instead of clicking", async () => {
    const user = userEvent.setup();
    renderChat();
    await askUntilPending(user);

    const stream = vi.spyOn(api, "streamTurn").mockResolvedValue({
      reply: "Moved 12 files.",
      needs_confirmation: false,
      pending: null,
      skill_calls: [],
      error: null,
    } as never);

    await user.type(screen.getByRole("textbox"), "yes");
    await user.keyboard("{Enter}");

    // A bare "yes" is just conversation to the backend. It only approves
    // anything because the id travels with the turn.
    await waitFor(() => expect(stream).toHaveBeenCalled());
    const call = stream.mock.calls[0];
    expect(call[call.length - 1]).toBe(PENDING.action_id);
    await screen.findByText("Moved 12 files.");
  });
});
