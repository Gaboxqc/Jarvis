/**
 * Every path this client calls exists on the backend.
 *
 * The drift this catches is specific and was entirely unguarded: a path
 * renamed or removed in Python is a 404 the UI discovers when a user opens the
 * screen that needs it. `tsc` cannot see it, because the paths are template
 * strings assembled at runtime, and the backend's own tests cannot see it
 * because they test the backend.
 *
 * Read out of the source rather than listed here on purpose. A hand-kept list
 * of "paths we use" is a list that falls behind the code and then passes
 * anyway, which is worse than no list -- it is the same failure mode as the
 * hand-written response types, one level up. This walks the api/ modules and
 * events.ts, pulls out every path literal, and checks each against the
 * committed OpenAPI snapshot.
 *
 * The snapshot is the right thing to check against: a separate test in the
 * backend asserts it matches the live FastAPI app, so agreeing with it is
 * agreeing with the app.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import schema from "../../../backend/tests/openapi.json";

const SOURCE_DIRS = ["src/api", "src"];

/** `/memory/${id}` and `/memory/{fact_id}` are the same route. */
function template(path: string): string {
  return path.replace(/\$\{[^}]*\}/g, "{}");
}

function served(): Set<string> {
  return new Set(Object.keys(schema.paths).map((path) => path.replace(/\{[^}]*\}/g, "{}")));
}

/**
 * Strip comments, so prose is not mistaken for a call.
 *
 * Not theoretical: the first run of this failed on `/connect`, which is the
 * name of a CLI command mentioned in a doc comment. A test that reads
 * documentation and reports it as a broken endpoint teaches people to ignore
 * the test.
 *
 * `://` is left alone, or `"http://127.0.0.1:8756"` would lose its tail.
 */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
}

/** Path literals in `request(...)`, `fetch(...)` and template strings. */
function pathsUsedIn(file: string): string[] {
  const source = code(readFileSync(file, "utf8"));
  const found = new Set<string>();

  // Quoted or backticked strings that start with a slash, minus any query.
  for (const match of source.matchAll(/["'`](\/[A-Za-z0-9/_${}.-]*)["'`]/g)) {
    const raw = match[1].split("?")[0].replace(/\/$/, "");
    if (!raw || raw === "/") continue;
    found.add(template(raw));
  }
  return [...found];
}

function clientFiles(): string[] {
  const files: string[] = [];
  for (const directory of SOURCE_DIRS) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (!entry.isFile() || !entry.name.endsWith(".ts")) continue;
      if (entry.name.endsWith(".test.ts") || entry.name.endsWith(".gen.ts")) continue;
      files.push(join(directory, entry.name));
    }
  }
  return files;
}

describe("the paths this client calls", () => {
  const endpoints = served();

  it("finds the client source to read", () => {
    // Guard on the guard: a test that silently reads nothing passes forever.
    const files = clientFiles();
    expect(files.length).toBeGreaterThan(10);
    expect(endpoints.size).toBeGreaterThan(50);
  });

  it.each(clientFiles())("%s calls nothing the backend does not serve", (file) => {
    const unknown = pathsUsedIn(file).filter((path) => !endpoints.has(path));
    expect(unknown).toEqual([]);
  });
});
