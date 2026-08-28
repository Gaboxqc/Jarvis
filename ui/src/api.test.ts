/**
 * What every request carries — REQ-24, REQ-26.
 *
 * The backend refuses calls without a bearer token, because loopback and CORS
 * are not access control (backend/app/security.py). This is the client half:
 * the header has to be on every request, including the two that do not go
 * through `request()` -- the SSE turn stream and the multipart upload, which
 * were exactly the two places it would have been easy to forget.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const TOKEN = "token-from-the-dev-server";

/** api.ts resolves the token once at module scope, so each test needs it fresh. */
async function freshApi() {
  vi.resetModules();
  vi.stubEnv("VITE_KAI_TOKEN", TOKEN);
  return import("./api");
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    json: async () => body,
  } as Response;
}

function headersOf(call: unknown[]) {
  return ((call[1] as RequestInit).headers ?? {}) as Record<string, string>;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async () => jsonResponse({ facts: [] }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("credentials", () => {
  it("go out on an ordinary request", async () => {
    const { api } = await freshApi();

    await api.memory();

    const headers = headersOf(fetchMock.mock.calls[0]);
    expect(headers.Authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("go out on the turn stream, which does not use request()", async () => {
    const { api } = await freshApi();
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () => ({ done: true, value: undefined }),
        }),
      },
    } as unknown as Response);

    await api.streamTurn("hello", "ui").catch(() => undefined);

    const headers = headersOf(fetchMock.mock.calls[0]);
    expect(headers.Authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("go out on the multipart upload, without a Content-Type of our own", async () => {
    const { api } = await freshApi();
    fetchMock.mockResolvedValue(jsonResponse({ seconds: 8 }));

    await api.uploadCloneReference(new File(["x"], "voice.wav"));

    const headers = headersOf(fetchMock.mock.calls[0]);
    expect(headers.Authorization).toBe(`Bearer ${TOKEN}`);
    // The browser has to supply the multipart boundary. Setting Content-Type by
    // hand here produces a body the server cannot parse.
    expect(headers["Content-Type"]).toBeUndefined();
  });

  it("are not sent as an empty header when there is no token", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_KAI_TOKEN", "");
    const { api } = await import("./api");

    await api.memory();

    expect(headersOf(fetchMock.mock.calls[0]).Authorization).toBeUndefined();
  });
});

describe("what the user is told", () => {
  it("distinguishes a refused window from a backend that is not running", async () => {
    const { api, ApiError } = await freshApi();
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Not authorised." }, 401));

    // "Is the backend running on port 8756?" is the wrong question when the
    // port answered, and sends people to restart something that is already up.
    await expect(api.memory()).rejects.toBeInstanceOf(ApiError);
    await expect(api.memory()).rejects.toThrow(/token/i);
  });

  it("still reports an unreachable backend as unreachable", async () => {
    const { api } = await freshApi();
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(api.memory()).rejects.toThrow(/backend running/i);
  });
});

describe("the confirmation contract", () => {
  it("puts the action id in the path, so there is no shape that sends a bare yes", async () => {
    const { api } = await freshApi();
    fetchMock.mockResolvedValue(jsonResponse({ reply: "done" }));

    await api.confirm("act-2f9c", "ui");

    expect(fetchMock.mock.calls[0][0]).toContain("/actions/act-2f9c/confirm");
    expect(JSON.stringify(fetchMock.mock.calls[0][1])).not.toMatch(/"yes"|true/);
  });
});
