/**
 * How loud the assistant is right now, 0–1 — REQ-32.
 *
 * The backend plays the audio and hands back an amplitude envelope with it, so
 * this walks that envelope in step with playback and publishes the current
 * value. The avatar's mouth reads it.
 *
 * A module-level store rather than React state, for two reasons. The value
 * changes thirty times a second, and putting that through a context would
 * re-render the whole chat view on every frame. And the two ends are far apart
 * in the tree — Chat asks for speech, the Avatar renders it — so passing it
 * down as a prop would mean threading it through components that have no
 * interest in it.
 *
 * There is no attempt to sync to the audio device. The envelope starts when the
 * speak call returns, which is when the backend starts playing, and both run on
 * the same machine. That is close enough for a mouth; anything tighter would
 * mean streaming the audio to the browser and playing it there, which is a much
 * larger change for a difference nobody would see.
 */

type Listener = (level: number) => void;

const listeners = new Set<Listener>();
let current = 0;
let frame: number | null = null;

function publish(level: number) {
  current = level;
  for (const listener of listeners) listener(level);
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  listener(current);
  return () => {
    listeners.delete(listener);
  };
}

export function level(): number {
  return current;
}

/** Stop any envelope in flight and close the mouth. */
export function stop() {
  if (frame !== null) cancelAnimationFrame(frame);
  frame = null;
  publish(0);
}

/**
 * Walk an envelope in real time.
 *
 * Driven by the clock rather than by frame count: requestAnimationFrame is not
 * guaranteed 60Hz, and a dropped frame would otherwise leave the mouth running
 * behind the voice for the rest of the sentence.
 */
export function playEnvelope(envelope: number[], fps = 30) {
  stop();
  if (!envelope.length) return;

  const started = performance.now();
  const step = () => {
    const elapsed = (performance.now() - started) / 1000;
    const index = Math.floor(elapsed * fps);
    if (index >= envelope.length) {
      publish(0);
      frame = null;
      return;
    }
    publish(envelope[index] ?? 0);
    frame = requestAnimationFrame(step);
  };
  frame = requestAnimationFrame(step);
}
