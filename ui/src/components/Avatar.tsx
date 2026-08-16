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

/** Which expression plays in which state. Names come from the model. */
const EXPRESSIONS: Partial<Record<AvatarState, string>> = {
  listening: "yf",
  thinking: "mj",
  speaking: "wh",
};

/** The newest Core the bundled Cubism framework can actually drive. */
const MAX_CORE_MAJOR = 5;

/**
 * Whether this Cubism Core is one the renderer can work with.
 *
 * Core 6.0.1 removed `drawables.renderOrders`, which every published PIXI
 * Live2D binding calls -- they all bundle a Cubism 4 or 5 framework. The model
 * loads, reports its 222 drawables, sits in the scene graph looking healthy,
 * and draws nothing. Restoring that one array is not enough either; more of the
 * API moved with it.
 *
 * So this is checked up front and reported, rather than being discovered as a
 * blank rectangle.
 */
function coreIsUsable(): { ok: boolean; version: string } {
  const core = (window as any).Live2DCubismCore;
  try {
    const packed = core.Version.csmGetVersion();
    const major = (packed >> 24) & 0xff;
    const minor = (packed >> 16) & 0xff;
    const version = `${major}.${minor}.${packed & 0xffff}`;
    return { ok: major <= MAX_CORE_MAJOR, version };
  } catch {
    // An unreadable version is not a reason to refuse; let it try.
    return { ok: true, version: "unknown" };
  }
}

async function loadCore(): Promise<boolean> {
  if ("Live2DCubismCore" in window) return true;
  try {
    // A HEAD first: injecting a <script> for a missing file gives an error
    // event with no detail, and "it didn't work" is not a useful thing to show.
    const probe = await fetch(CORE, { method: "HEAD" });
    if (!probe.ok) return false;
  } catch {
    return false;
  }

  return new Promise((resolve) => {
    const tag = document.createElement("script");
    tag.src = CORE;
    tag.onload = () => resolve("Live2DCubismCore" in window);
    tag.onerror = () => resolve(false);
    document.head.appendChild(tag);
  });
}

export function Avatar({ state, t }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const model = useRef<any>(null);
  const app = useRef<any>(null);
  const levelRef = useRef(0);
  const [ready, setReady] = useState<boolean | null>(null);
  const [coreVersion, setCoreVersion] = useState<string | null>(null);

  // Straight into a ref: the ticker runs outside React's render cycle, and
  // re-rendering this component thirty times a second to move a mouth would be
  // absurd.
  useEffect(() => subscribe((value) => { levelRef.current = value; }), []);

  useEffect(() => {
    let cancelled = false;
    let ticker: (() => void) | null = null;

    void (async () => {
      if (!(await loadCore())) {
        if (!cancelled) setReady(false);
        return;
      }

      const core = coreIsUsable();
      if (!core.ok) {
        if (!cancelled) {
          setCoreVersion(core.version);
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

        // pixi-live2d-display 0.4 is typed against PIXI 6; the runtime shape is
        // right, DisplayObject just gained a method since.
        application.stage.addChild(loaded as any);
        app.current = application;
        model.current = loaded;
        fit(application, loaded);

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
        if (!cancelled) setReady(false);
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

  // Expression follows state.
  useEffect(() => {
    const loaded = model.current;
    if (!loaded) return;
    const expression = EXPRESSIONS[state];
    try {
      if (expression) loaded.expression(expression);
      else loaded.internalModel.motionManager.expressionManager?.resetExpression();
    } catch {
      // The names come from the model and may not all exist in a replacement.
      // A missing expression should not take the avatar down.
    }
  }, [state]);

  function fit(application: any, loaded: any) {
    const width = application.renderer.width;
    const height = application.renderer.height;
    // Framed on the head and shoulders: the model is full-body, and shrinking
    // all of it into a 260px panel makes the face too small to read.
    const scale = (height / loaded.internalModel.originalHeight) * 2.1;
    loaded.scale.set(scale);
    loaded.x = width / 2;
    loaded.y = height * 0.12;
    loaded.anchor.set(0.5, 0);
  }

  if (ready === false) {
    return (
      <div className="avatar avatar-missing">
        <p className="small muted">
          {coreVersion
            ? t("avatar.coreTooNew", { version: coreVersion, max: MAX_CORE_MAJOR })
            : t("avatar.needsCore")}
        </p>
        <code className="small">live2dcubismcore.min.js</code>
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
