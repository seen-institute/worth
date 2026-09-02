import { useEffect, useState } from "react";

/**
 * Hash routing, deliberately.
 *
 * The console is served as flat files. Path routing would need a rewrite rule on
 * the host to keep a deep link from 404ing, and a rule nobody remembers to add
 * is a broken link in somebody's message. A hash never reaches the server.
 */
export const ROUTES = [
  "status",
  "score",
  "data",
  "run",
  "price",
  "downloads",
] as const;

export type Route = (typeof ROUTES)[number];

function read(): Route {
  const hash = window.location.hash.replace(/^#\/?/, "");
  return (ROUTES as readonly string[]).includes(hash)
    ? (hash as Route)
    : "status";
}

export function useRoute(): [Route, (route: Route) => void] {
  const [route, setRoute] = useState<Route>(read);

  useEffect(() => {
    const sync = (): void => setRoute(read());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  return [
    route,
    (next: Route) => {
      window.location.hash = `#/${next}`;
      window.scrollTo(0, 0);
    },
  ];
}
