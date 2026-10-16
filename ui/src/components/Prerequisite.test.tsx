/**
 * The prerequisite banner, and the one state that is not a failure.
 *
 * When the configured model is not installed, the backend now answers with one
 * that is. `ok` is therefore true -- the assistant works -- so nothing in the
 * old logic would have said a word, and the user would be left with an
 * assistant that is quietly worse at picking actions for a reason they cannot
 * see. Routing accuracy is model-specific: this project measured 88% against
 * 62% between two of the candidates.
 *
 * So the substitution gets a card, not a banner, and it is said once.
 */

import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Prerequisite } from "./Prerequisite";
import { translate, type Key } from "../i18n";

type Health = {
  type: "health";
  ok: boolean;
  error: string | null;
  substituted?: boolean;
};

let listener: (event: Health) => void;

/**
 * Deliver a health event the way the stream does.
 *
 * Wrapped in `act` because the component sets state from it. Without that the
 * update is never flushed and the component renders nothing -- which makes
 * "says nothing when the model is fine" pass for entirely the wrong reason,
 * and that is how it first passed here.
 */
function emit(event: Health) {
  act(() => listener(event));
}

vi.mock("../events", () => ({
  subscribe: (handler: (event: Health) => void) => {
    listener = handler;
    return () => undefined;
  },
  subscribeConnection: (listener: (status: string) => void) => {
    listener("open");
    return () => undefined;
  },
}));

const t = (key: Key) => translate("en", key);

beforeEach(() => {
  listener = () => undefined;
});

describe("the prerequisite banner", () => {
  it("says nothing when the configured model is the one answering", () => {
    const { container } = render(<Prerequisite t={t} />);

    emit({ type: "health", ok: true, error: null, substituted: false });

    expect(container).toBeEmptyDOMElement();
  });

  it("warns when no model can answer at all", () => {
    render(<Prerequisite t={t} />);

    emit({ type: "health", ok: false, error: "model 'qwen2.5' not installed" });

    expect(screen.getByText(t("prereq.modelTitle"))).toBeInTheDocument();
    expect(screen.getByText(/not installed/)).toBeInTheDocument();
  });

  it("says so when answering from a model the user did not choose", () => {
    render(<Prerequisite t={t} />);

    emit({
      type: "health",
      ok: true,
      error: "'qwen2.5' isn't installed, so I'm using 'llama3:latest'.",
      substituted: true,
    });

    expect(screen.getByText(t("prereq.substitutedTitle"))).toBeInTheDocument();
    // The detail names both models, so "a different model" is checkable
    // rather than a thing the user has to take on trust.
    expect(screen.getByText(/llama3:latest/)).toBeInTheDocument();
  });

  it("is a card rather than a banner, because it is not a failure", () => {
    const { container } = render(<Prerequisite t={t} />);

    emit({ type: "health", ok: true, error: "using llama3", substituted: true });

    expect(container.firstElementChild).toHaveClass("card");
    expect(container.firstElementChild).not.toHaveClass("banner");
  });

  it("stays dismissed, rather than returning with the next health sample", async () => {
    render(<Prerequisite t={t} />);
    const substituted: Health = {
      type: "health",
      ok: true,
      error: "using llama3",
      substituted: true,
    };

    emit(substituted);
    await userEvent.click(screen.getByText(t("prereq.substitutedDismiss")));

    // The stream samples health every ten seconds. Without the latch this
    // card would come back forever, which is how a notice becomes noise.
    emit(substituted);

    expect(screen.queryByText(t("prereq.substitutedTitle"))).not.toBeInTheDocument();
  });

  it("still reports a real failure after the notice was dismissed", async () => {
    render(<Prerequisite t={t} />);

    emit({ type: "health", ok: true, error: "using llama3", substituted: true });
    await userEvent.click(screen.getByText(t("prereq.substitutedDismiss")));

    // Dismissing "I swapped your model" must not dismiss "nothing can answer".
    emit({ type: "health", ok: false, error: "model 'qwen2.5' not installed" });

    expect(screen.getByText(t("prereq.modelTitle"))).toBeInTheDocument();
  });
});
