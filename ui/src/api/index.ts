/**
 * Backend client — REQ-1, REQ-24.
 *
 * Every call goes to the same FastAPI the CLI drives, so the UI cannot acquire
 * capabilities the terminal doesn't have, and the Action Gate sits in front of
 * exactly the same actions.
 *
 * The confirmation contract is visible in the types on purpose: `confirm` takes
 * an action id, never a boolean and never a "yes". There is no shape in this
 * module that could approve something the user was not shown.
 *
 * One file until it was 921 lines, now one module per area -- the same areas the
 * backend's own app/api/ uses, and the same names, so a reader following a call
 * from a component to a handler does not have to translate between two
 * vocabularies on the way. `api` is assembled here, so every call site keeps
 * reading `api.turn(...)` and nothing outside this folder changed.
 */

import { actions } from "./actions";
import { avatar } from "./avatar";
import { capture } from "./capture";
import { connectors } from "./connectors";
import { conversation } from "./conversation";
import { documents } from "./documents";
import { memory } from "./memory";
import { privacy } from "./privacy";
import { scheduling } from "./scheduling";
import { system } from "./system";
import { voice } from "./voice";

export { ApiError, BASE, authHeaders } from "./client";
export type * from "./types";

export const api = {
  ...conversation,
  ...actions,
  ...memory,
  ...scheduling,
  ...documents,
  ...connectors,
  ...voice,
  ...avatar,
  ...capture,
  ...system,
  ...privacy,
};
