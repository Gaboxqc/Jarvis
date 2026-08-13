/**
 * Voice state, shared by the composer and the settings screen — REQ-1, REQ-4.
 *
 * One source of truth for "can I talk to it, and will it talk back", so the mic
 * button and the settings card can never disagree about whether voice is on.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type VoiceStatus } from "./api";

export interface Voice {
  status: VoiceStatus | null;
  /** Ready to capture: enabled, input on, a microphone, and models present. */
  canListen: boolean;
  /** Will speak replies aloud. */
  speaks: boolean;
  /** Why the mic is unavailable, in words the user can act on. */
  blockedBecause: string | null;
  busy: boolean;
  refresh: () => Promise<void>;
  setEnabled: (on: boolean) => Promise<void>;
  setSpeaks: (on: boolean) => Promise<void>;
}

export function useVoice(): Voice {
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.voiceStatus());
    } catch {
      // A backend that isn't up yet is reported by the prerequisite banner;
      // the mic simply stays unavailable rather than throwing here.
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const patch = useCallback(
    async (changes: Record<string, unknown>) => {
      setBusy(true);
      try {
        await api.saveSettings({ voice: changes });
        await refresh();
      } catch (error) {
        if (error instanceof ApiError) throw error;
        throw new ApiError("Couldn't save that setting.");
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const canListen = Boolean(
    status?.enabled && status.input_enabled && status.microphone && status.models_ready,
  );

  let blockedBecause: string | null = null;
  if (!status) blockedBecause = "voiceBlocked.offline";
  else if (!status.models_ready) blockedBecause = "voiceBlocked.models";
  else if (!status.microphone) blockedBecause = "voiceBlocked.mic";
  else if (!status.enabled) blockedBecause = "voiceBlocked.off";
  else if (!status.input_enabled) blockedBecause = "voiceBlocked.inputOff";

  return {
    status,
    canListen,
    speaks: Boolean(status?.enabled && status.output_enabled),
    blockedBecause,
    busy,
    refresh,
    // Turning voice on from the mic button should actually turn it on, not send
    // the user to a settings screen to flip a second switch.
    setEnabled: (on: boolean) => patch({ enabled: on, input_enabled: true }),
    setSpeaks: (on: boolean) => patch({ enabled: true, output_enabled: on }),
  };
}
