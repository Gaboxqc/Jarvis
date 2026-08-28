import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// jsdom implements neither of these, and Chat scrolls the log on every message.
// Left unstubbed, every render throws before a single assertion runs.
Element.prototype.scrollIntoView = vi.fn();
window.HTMLMediaElement.prototype.play = vi.fn(async () => undefined);
