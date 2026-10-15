/**
 * What the avatar does before the backend is listening.
 *
 * The packaged app starts its backend as a sidecar, so the frontend mounts and
 * asks for the Live2D licence several seconds before anything is bound to the
 * port. That first request is refused on every single launch. Treating the
 * refusal as the answer switched the avatar off for the rest of the session --
 * and because the gate never opened, the Cubism Core was never fetched, so the
 * panel reported a runtime failure for a runtime it had not tried to load.
 *
 * Everything else in the app recovered the moment the event stream opened.
 * These are about the avatar doing the same.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { api } from "../api";
import { translate } from "../i18n";

// The success path instantiates PIXI against a WebGL canvas jsdom does not
// have. Every assertion here is about the licence request and the panel, both
// of which happen before any of that is imported.
vi.mock("pixi.js", () => ({ Application: class {}, Ticker: class {} }));
vi.mock("@pixi/unsafe-eval", () => ({ install: vi.fn() }));
vi.mock("pixi-live2d-display/cubism4", () => ({ Live2DModel: class {} }));

let connection: ((status: string) => void) | null = null;
vi.mock("../events", () => ({
  subscribeConnection: (fn: (status: string) => void) => {
    connection = fn;
    return () => {
      connection = null;
    };
  },
}));

const { Avatar } = await import("./Avatar");

const t = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("en", key, vars);

const FAILED = translate("en", "avatar.coreFailed");

beforeEach(() => {
  connection = null;
});

describe("the avatar before the backend answers", () => {
  it("does not call a refused licence request a failure", async () => {
    vi.spyOn(api, "avatarLicence").mockRejectedValue(new TypeError("Failed to fetch"));

    render(<Avatar state="idle" t={t} />);

    await waitFor(() => expect(api.avatarLicence).toHaveBeenCalled());
    // A cold start, not a fault. Nothing has told this component the backend
    // is there, so there is nothing yet to report.
    expect(screen.queryByText(FAILED)).not.toBeInTheDocument();
  });

  it("asks again when the stream opens, without a reload", async () => {
    const licence = vi
      .spyOn(api, "avatarLicence")
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue({
        licence_accepted: false,
        licence_accepted_at: null,
        licence_summary: "Live2D Cubism Core is licensed separately.",
        licence_url: "https://www.live2d.com/en/sdk/license/",
      } as never);

    render(<Avatar state="idle" t={t} />);
    await waitFor(() => expect(licence).toHaveBeenCalledTimes(1));

    connection?.("open");

    await waitFor(() => expect(licence).toHaveBeenCalledTimes(2));
    // The second answer arrived, so the gate can finally be shown.
    await waitFor(() =>
      expect(screen.getByText(/licensed separately/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(FAILED)).not.toBeInTheDocument();
  });

  it("reports a refusal from a backend that is demonstrably there", async () => {
    vi.spyOn(api, "avatarLicence").mockRejectedValue(new TypeError("Failed to fetch"));

    render(<Avatar state="idle" t={t} />);
    await waitFor(() => expect(api.avatarLicence).toHaveBeenCalledTimes(1));

    // The stream is open and the request still fails: that is a real fault,
    // and silence here would be the blank rectangle this all exists to stop.
    connection?.("open");

    await waitFor(() => expect(screen.getByText(FAILED)).toBeInTheDocument());
    // And it says what went wrong, which the old panel never did.
    expect(screen.getByText(/Failed to fetch/)).toBeInTheDocument();
  });
});
