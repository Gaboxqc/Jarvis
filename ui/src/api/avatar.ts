/** The avatar's runtime licence. */

import { request } from "./client";
import { body } from "./schema";
import type { AvatarLicence } from "./types";

export const avatar = {
  avatarLicence: () => request<AvatarLicence>("/avatar"),

  acceptAvatarLicence: (accepted: boolean) =>
    request<AvatarLicence>("/avatar/licence", {
      method: "POST",
      body: body<"AvatarLicenceRequest">({ accepted }),
    }),
};
