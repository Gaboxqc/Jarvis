/**
 * Error boundary — REQ-27.
 *
 * Without one, any exception thrown while rendering unmounts the whole tree and
 * leaves an empty window. That is the worst possible failure report: it looks
 * identical to a hang, a crash and a blank page, and tells the user and the
 * developer exactly nothing.
 *
 * This turns that into a visible message carrying the actual error, which is
 * what should have been there the first time the packaged app went blank.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
  stack: string;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, stack: "" };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Also to the console, so it survives when a screenshot is all anyone has.
    console.error("Kai UI crashed:", error, info.componentStack);
    this.setState({ stack: info.componentStack ?? "" });
  }

  render() {
    const { error, stack } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="view" style={{ padding: "1rem" }} role="alert">
        <div className="banner">
          <strong>Kai hit an error and stopped drawing.</strong>
          <p className="small" style={{ margin: "0.4rem 0 0" }}>
            This is a bug. The details below are what to report.
          </p>
        </div>

        <pre
          className="small"
          style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
        >
          {error.name}: {error.message}
          {stack ? `\n${stack}` : ""}
        </pre>

        <button className="primary" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}

/**
 * Catch what the boundary cannot: rejected promises and errors thrown outside
 * React's render path. Neither blanks the window on its own, but both otherwise
 * vanish silently in a packaged build with no console attached.
 */
export function installGlobalHandlers() {
  window.addEventListener("unhandledrejection", (event) => {
    console.error("Kai: unhandled rejection", event.reason);
  });
  window.addEventListener("error", (event) => {
    console.error("Kai: uncaught error", event.error ?? event.message);
  });
}
