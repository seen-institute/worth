import { useCallback, useEffect, useState } from "react";

import { describe } from "./api/client";

export type Remote<T> =
  | { status: "loading" }
  | { status: "ok"; data: T }
  | { status: "error"; error: string };

/**
 * One request, its three states, and a way to try again.
 *
 * `load` has to be referentially stable (a module-level function), or the
 * effect re-fires on every render.
 */
export function useRemote<T>(load: () => Promise<T>): [Remote<T>, () => void] {
  const [state, setState] = useState<Remote<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    load().then(
      (data) => live && setState({ status: "ok", data }),
      (error: unknown) =>
        live && setState({ status: "error", error: describe(error) }),
    );
    return () => {
      live = false;
    };
  }, [load, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return [state, retry];
}

export function RemoteState({
  state,
  what,
  onRetry,
}: {
  state: Remote<unknown>;
  what: string;
  onRetry?: () => void;
}) {
  if (state.status === "loading") {
    return <p className="remote">Loading {what}…</p>;
  }
  if (state.status === "error") {
    return (
      <p className="remote error">
        Could not load {what}. {state.error}
        {onRetry && (
          <>
            {" "}
            <button type="button" className="btn-quiet" onClick={onRetry}>
              Try again
            </button>
          </>
        )}
      </p>
    );
  }
  return null;
}
