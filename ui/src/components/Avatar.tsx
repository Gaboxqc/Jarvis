/**
 * The avatar — REQ-32.
 *
 * A Live2D model standing in for the presence dot. It shows what the assistant
 * is doing: breathing when idle, attentive while the microphone is open,
 * looking away while it thinks, mouth moving while it speaks.
 *
 * Three things shape this file.
 *
 * **It is optional, and its absence must cost nothing.** Loading a .moc3
 * requires Live2D's Cubism Core, which is proprietary and downloaded separately
 * under a licence the operator accepts. Without it, this renders the small
 * status line the app had before and says why. Everything is imported lazily,
 * so a browser or a build without the model never pays for PIXI either.
 *
 * **The mouth is driven by amplitude, not phonemes.** Live2D's own approach,
 * and it sidesteps the problem that Piper does not expose phoneme timings. The
 * envelope comes from the same PCM the speaker gets, so the mouth cannot drift
 * out of sync with the audio -- there is only one clock.
 *
 * **State comes from the app, not from guesses.** `state` is the presence the
 * rest of the UI already tracks, so the avatar can never disagree with the
 * status text beside it.
 */

import { useEffect, useRef, useState } from "react";
import type { Key } from "../i18n";
import { subscribe } from "../speechLevel";

export type AvatarState = "idle" | "listening" | "thinking" | "speaking" | "offline";

interface Props {
  state: AvatarState;
  t: (key: Key, vars?: Record<string, string | number>) => string;
}

const WIDTH = 260;
const HEIGHT = 300;

const MODEL = "/live2d/Alexia.model3.json";
const CORE = "/live2d/live2dcubismcore.min.js";

/**
 * Which expression plays in which state. Names come from the model.
 *
 * The model ships sixteen, named `bbt`, `dyj`, `mj` and so on, and each one is
 * a single anonymous parameter nudged to 30 -- there is nothing in the files
 * that says what any of them looks like. These three were chosen by rendering
 * all sixteen and comparing: they are the ones that leave the eyes open, which
 * is the only property that actually matters here. An avatar with its eyes shut
 * while the microphone is live reads as asleep, not as listening.
 */
const EXPRESSIONS: Partial<Record<AvatarState, string>> = {
  listening: "yf",
  thinking: "mj",
  speaking: "wh",
};

/**
 * Where the avatar looks in each state: a direction, x and y in -1..1, with
 * +y up and +x to the model's right.
 *
 * Given to the focus controller directly rather than through `model.focus()`.
 * That wrapper takes a point on the canvas, and it keeps only the *angle* from
 * the model's centre to that point -- the distance is thrown away. The panel is
 * a tight crop of the head, so every pixel in it sits above and near the middle
 * of a 5167x9410 model, which collapses to almost one angle: four of these five
 * states came out as the same pose when routed through it.
 *
 * The controller damps toward the target over about a second, so these are
 * destinations rather than positions, and the avatar drifts between them
 * instead of snapping.
 *
 * Looking away while thinking is the one that carries real meaning. Holding eye
 * contact through a long pause reads as having frozen; breaking it reads as
 * working on something, which is what is actually happening.
 */
const GAZE: Record<AvatarState, [number, number]> = {
  idle: [0, 0],
  listening: [0, 0.18],
  thinking: [-0.75, 0.55],
  speaking: [0, 0],
  offline: [0, -0.55],
};

/**
 * Put `renderOrders` back where the Cubism framework looks for it.
 *
 * Core 6 moved it from `model.drawables.renderOrders` to `model.renderOrders`.
 * Every published PIXI Live2D binding bundles a Cubism 4 or 5 framework and
 * reads the old location, so with Core 6 the model loads, reports its 222
 * drawables, sits in the scene graph looking entirely healthy, and draws
 * nothing at all -- `doDrawModel` indexes an undefined array on its first line.
 *
 * The array itself is unchanged: 222 distinct values, one per drawable, the
 * same permutation the renderer has always sorted by. Only its address moved.
 * So this is an alias, not a reimplementation -- worth being clear about,
 * because an earlier attempt substituted `drawables.drawOrders`, which is a
 * different quantity (every entry 500 here) and rendered nothing while looking
 * like the same kind of fix.
 *
 * Written as a property on the instance rather than a patch to the Core, so a
 * Core that already has it in the old place is left alone.
 */
function bridgeRenderOrders(coreModel: any): void {
  const raw = coreModel?._model;
  if (!raw?.drawables) return;
  if (raw.drawables.renderOrders) return;      // Core 5 and earlier: nothing to do
  if (!raw.renderOrders) return;               // neither location: not ours to fix
  raw.drawables.renderOrders = raw.renderOrders;
}

/**
 * Where the head is, in the model's own canvas pixels.
 *
 * There is nothing to read this off: the drawables are named `ArtMesh0` up to
 * `ArtMesh201`, so no amount of string matching finds a face. But the head is
 * definable without names -- it is whatever moves when the head turns. Tilting
 * `ParamAngleZ` and diffing the vertices identifies it exactly, hair and all,
 * and the box those meshes occupy at rest is the head.
 *
 * Measured rather than hard-coded because the numbers are a property of the
 * model, not of this app: drop in a different character and the framing follows
 * it instead of cropping its face off. Costs one extra pose evaluation, once.
 *
 * Returns null when the model has no head-angle parameter, which is the honest
 * answer for a model this cannot reason about -- the caller falls back to
 * showing the whole thing.
 */
function headBox(coreModel: any): { x: number; y: number; width: number; height: number } | null {
  const raw = coreModel?._model;
  const info = raw?.canvasinfo;
  if (!info || typeof coreModel.getDrawableCount !== "function") return null;

  const count = coreModel.getDrawableCount();
  const update = () => (raw.update ? raw.update() : coreModel.update?.());
  const snapshot = () => {
    const frames: (Float32Array | null)[] = [];
    for (let i = 0; i < count; i++) {
      const vertices = coreModel.getDrawableVertices(i);
      frames.push(vertices ? Float32Array.from(vertices) : null);
    }
    return frames;
  };

  let neutral: (Float32Array | null)[];
  let tilted: (Float32Array | null)[];
  try {
    coreModel.setParameterValueById("ParamAngleZ", 0);
    update();
    neutral = snapshot();
    coreModel.setParameterValueById("ParamAngleZ", 30);
    update();
    tilted = snapshot();
  } finally {
    // Whatever happened, the model must not be left holding a pose.
    coreModel.setParameterValueById("ParamAngleZ", 0);
    update();
  }

  let left = Infinity, right = -Infinity, bottom = Infinity, top = -Infinity;
  let found = 0;
  for (let i = 0; i < count; i++) {
    const before = neutral[i];
    const after = tilted[i];
    if (!before || !after || before.length !== after.length) continue;

    let moved = 0;
    for (let k = 0; k < before.length; k++) {
      moved = Math.max(moved, Math.abs(before[k] - after[k]));
    }
    // A threshold, not a strict inequality: meshes far down the body pick up a
    // rounding-level wobble from the deformers above them.
    if (moved <= 0.002) continue;
    if (coreModel.getDrawableOpacity?.(i) <= 0.01) continue;

    found++;
    for (let k = 0; k < before.length; k += 2) {
      const x = before[k];
      const y = before[k + 1];
      if (x < left) left = x;
      if (x > right) right = x;
      if (y < bottom) bottom = y;
      if (y > top) top = y;
    }
  }
  if (!found) return null;

  // Vertices are in model units with the origin at the canvas centre and y
  // pointing up; everything downstream wants canvas pixels with y down.
  const { CanvasOriginX, CanvasOriginY, PixelsPerUnit } = info;
  return {
    x: CanvasOriginX + left * PixelsPerUnit,
    y: CanvasOriginY - top * PixelsPerUnit,
    width: (right - left) * PixelsPerUnit,
    height: (top - bottom) * PixelsPerUnit,
  };
}

/**
 * Why the avatar is not showing, when it is not showing.
 *
 * One message for every cause is what let a broken release out: the packaged
 * app said "needs Cubism Core" while the Core was sitting inside the binary,
 * because the real fault was the content security policy refusing to compile
 * its WebAssembly. Those need different answers from whoever reads them, so
 * they are different values here.
 */
type CoreProblem = "missing" | "blocked" | "failed";

/**
 * Whether this page is allowed to compile WebAssembly.
 *
 * Eight bytes: the WebAssembly magic number and version, which is a complete
 * and valid empty module. Compiling it does nothing and costs nothing, but a
 * page whose CSP lacks 'wasm-unsafe-eval' throws here rather than returning
 * false -- which is exactly the signal wanted, and cannot be obtained by
 * looking for the API, since `WebAssembly` is defined either way.
 *
 * Cubism Core 6 reaches WebAssembly through instantiateStreaming, so this is
 * a precondition for the avatar rather than a detail of it.
 */
function webAssemblyAllowed(): boolean {
  try {
    new WebAssembly.Module(new Uint8Array([0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00]));
    return true;
  } catch {
    return false;
  }
}

async function loadCore(): Promise<CoreProblem | null> {
  if ("Live2DCubismCore" in window) return null;

  try {
    // A HEAD first: injecting a <script> for a missing file gives an error
    // event with no detail, and "it didn't work" is not a useful thing to show.
    const probe = await fetch(CORE, { method: "HEAD" });
    if (!probe.ok) return "missing";
  } catch {
    return "missing";
  }

  // Checked after the file, because "you are missing a file" is the more
  // actionable of the two when both are true.
  if (!webAssemblyAllowed()) return "blocked";

  return new Promise((resolve) => {
    const tag = document.createElement("script");
    tag.src = CORE;
    tag.onload = () => resolve("Live2DCubismCore" in window ? null : "failed");
    tag.onerror = () => resolve("failed");
    document.head.appendChild(tag);
  });
}

export function Avatar({ state, t }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const model = useRef<any>(null);
  const app = useRef<any>(null);
  const levelRef = useRef(0);
  const [ready, setReady] = useState<boolean | null>(null);
  const [problem, setProblem] = useState<CoreProblem | null>(null);

  // Straight into a ref: the ticker runs outside React's render cycle, and
  // re-rendering this component thirty times a second to move a mouth would be
  // absurd.
  useEffect(() => subscribe((value) => { levelRef.current = value; }), []);

  useEffect(() => {
    let cancelled = false;
    let ticker: (() => void) | null = null;

    void (async () => {
      const blocker = await loadCore();
      if (blocker) {
        if (!cancelled) {
          setProblem(blocker);
          setReady(false);
        }
        return;
      }

      try {
        const PIXI = await import("pixi.js");
        const { Live2DModel } = await import("pixi-live2d-display/cubism4");
        // pixi-live2d-display reaches for PIXI.Ticker on the window rather than
        // taking it as an argument.
        (window as any).PIXI = PIXI;

        if (cancelled || !canvas.current) return;

        const application = new PIXI.Application({
          view: canvas.current,
          // Given explicitly. PIXI ignores width/height set on the canvas
          // element and falls back to its own 800x600 default, which is three
          // times the panel and pushes the conversation off the screen.
          width: WIDTH,
          height: HEIGHT,
          backgroundAlpha: 0,
          antialias: true,
          autoDensity: true,
          resolution: window.devicePixelRatio || 1,
        });

        // autoInteract: false is not an optimisation, it is what makes this
        // work at all. pixi-live2d-display 0.4 registers a pointer handler
        // against PIXI 6's InteractionManager; PIXI 7 replaced that with
        // EventSystem, so registerInteraction() gets an object with no `.on`
        // and throws -- inside _render, on every frame. The model sits in the
        // scene graph looking perfectly healthy and never draws a pixel.
        // Nothing here needs hit-testing: the avatar is decoration and is
        // aria-hidden.
        const loaded = await Live2DModel.from(MODEL, { autoInteract: false });
        if (cancelled) {
          application.destroy();
          return;
        }

        // Before anything renders.
        bridgeRenderOrders(loaded.internalModel.coreModel);

        // The binding is typed against PIXI 6; the runtime shape is right,
        // DisplayObject just gained a method since.
        application.stage.addChild(loaded as any);
        app.current = application;
        model.current = loaded;
        fit(application, loaded);

        // Dev only. The avatar renders on requestAnimationFrame, which a headless
        // or backgrounded window never fires, so "is it drawing?" cannot be
        // answered from outside without a handle to force a frame. Stripped from
        // the production bundle by the constant folding on import.meta.env.DEV.
        if (import.meta.env.DEV) {
          (window as any).__kaiAvatar = { application, loaded };
        }

        // One ticker for the mouth. Interpolated rather than set directly:
        // amplitude jumps frame to frame, and a mouth that snaps between values
        // reads as a glitch rather than as speech.
        let mouth = 0;
        ticker = () => {
          const target = Math.min(1, levelRef.current * 1.6);
          mouth += (target - mouth) * 0.35;
          (loaded.internalModel.coreModel as any)
            .setParameterValueById("ParamMouthOpenY", mouth);
        };
        application.ticker.add(ticker);

        setReady(true);
      } catch {
        if (!cancelled) {
          setProblem("failed");
          setReady(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      if (ticker && app.current) app.current.ticker.remove(ticker);
      model.current = null;
      // destroy(true) takes the textures with it; without it, navigating away
      // and back leaks a 2048px texture per visit.
      app.current?.destroy(true, { children: true, texture: true, baseTexture: true });
      app.current = null;
    };
  }, []);

  // Gaze and expression follow state. `ready` is a dependency because loading is
  // asynchronous: without it, whatever state the avatar was in while the model
  // was still loading is simply never applied, and it sits neutral until the
  // next change.
  useEffect(() => {
    const loaded = model.current;
    if (!loaded) return;

    const [x, y] = GAZE[state];
    loaded.internalModel.focusController?.focus(x, y);

    const expression = EXPRESSIONS[state];
    try {
      if (expression) loaded.expression(expression);
      else loaded.internalModel.motionManager.expressionManager?.resetExpression();
    } catch {
      // The names come from the model and may not all exist in a replacement.
      // A missing expression should not take the avatar down.
    }
  }, [state, ready]);

  function fit(application: any, loaded: any) {
    const width = application.renderer.width;
    const height = application.renderer.height;
    const canvasWidth = loaded.internalModel.originalWidth;
    const canvasHeight = loaded.internalModel.originalHeight;

    // Framed on the head and shoulders. The model is full-body and nearly twice
    // as tall as it is wide; fitting all of it into a 260px panel leaves a face
    // about forty pixels across, which reads as a smudge rather than as someone
    // looking at you.
    const head = headBox(loaded.internalModel.coreModel);
    if (!head) {
      // No head-angle parameter to measure with. Show the whole model rather
      // than crop a guess -- an unflattering framing beats a decapitation.
      loaded.scale.set(Math.min(width / canvasWidth, height / canvasHeight));
      loaded.anchor.set(0.5, 0.5);
      loaded.x = width / 2;
      loaded.y = height / 2;
      return;
    }

    // A tenth of a head of air above the hair, and down to roughly mid-chest.
    const top = head.y - head.height * 0.1;
    const band = head.height * 1.65;

    loaded.scale.set(height / band);
    loaded.anchor.set(
      (head.x + head.width / 2) / canvasWidth,
      (top + band / 2) / canvasHeight,
    );
    loaded.x = width / 2;
    loaded.y = height / 2;
  }

  if (ready === false) {
    // Each cause gets its own answer. They are not interchangeable: one is a
    // missing download, one is this app's own security policy, and one is a
    // bug. Showing the first message for all three is what shipped a release
    // whose avatar could never have worked.
    const message: Record<CoreProblem, Key> = {
      missing: "avatar.needsCore",
      blocked: "avatar.coreBlocked",
      failed: "avatar.coreFailed",
    };
    return (
      <div className="avatar avatar-missing">
        <p className="small muted">{t(message[problem ?? "missing"])}</p>
        {problem === "missing" && <code className="small">live2dcubismcore.min.js</code>}
      </div>
    );
  }

  return (
    <div className={`avatar avatar-${state}`}>
      <canvas ref={canvas} aria-hidden="true" />
      {/* The avatar is decoration; this is what a screen reader is told. */}
      <span className="sr-only">{t(`state.${state}` as Key)}</span>
    </div>
  );
}
