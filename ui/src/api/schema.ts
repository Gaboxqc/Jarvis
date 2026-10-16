/**
 * The backend's own description of itself, as types.
 *
 * `api-types.gen.ts` is generated from `backend/tests/openapi.json` by
 * `npm run api:types`, and CI regenerates it and fails on a diff. That
 * snapshot is in turn checked against the live FastAPI app by
 * `backend/tests/test_openapi_contract.py`, so the chain closes: the schema
 * matches the app, the types match the schema, and this file is where the
 * client picks them up.
 *
 * What this covers, and what it does not
 * --------------------------------------
 *
 * Covered: every path, and the body of every request that has one. A path
 * renamed in Python now fails `tsc` here rather than 404-ing at runtime, and a
 * renamed field in a Pydantic request model fails at the call site that still
 * sends the old name.
 *
 * Not covered: response bodies. Every handler is annotated `dict[str, Any]`,
 * so the schema describes each 200 as `{"type": "object",
 * "additionalProperties": true}` and the generated response type is
 * `{[key: string]: unknown}` -- true, and useless. The hand-written
 * interfaces in types.ts are still the only description of what comes back,
 * and a renamed *response* field remains the one drift this cannot catch.
 *
 * Closing that needs `response_model=` on the Python side, which is a change
 * to seventy handlers and a large deliberate move of the snapshot. It is
 * written up as its own item in docs/NEXT-LEVEL-PLAN.md rather than
 * half-started here, and this comment exists so nobody reads the generated
 * types and concludes the responses are checked.
 */

import type { components, paths } from "../api-types.gen";

/** Every path the backend serves. */
export type ApiPath = keyof paths;

/** A request body, by the name of its Pydantic model. */
export type Body<Name extends keyof components["schemas"]> =
  components["schemas"][Name];

/**
 * Serialise a request body against the schema.
 *
 * `JSON.stringify` takes `any`, which is why a renamed field could be sent
 * for as long as it liked. Going through here means the object is checked
 * against the model the backend will parse it with.
 */
export function body<Name extends keyof components["schemas"]>(
  value: Body<Name>,
): string {
  return JSON.stringify(value);
}
