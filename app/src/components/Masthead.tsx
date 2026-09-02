import { ROUTES, type Route } from "../useRoute";

const LABEL: Record<Route, string> = {
  status: "Current State",
  score: "Complexity score",
  data: "Data",
  run: "Run",
  price: "Price",
  downloads: "Downloads",
};

export function Masthead({
  route,
  onNavigate,
  version,
}: {
  route: Route;
  onNavigate: (route: Route) => void;
  version: string;
}) {
  return (
    <header className="masthead">
      <div className="wrap">
        <div className="brand">Worth Registry</div>
        <nav>
          {ROUTES.map((r) => (
            <button
              key={r}
              type="button"
              aria-current={r === route ? "page" : undefined}
              onClick={() => onNavigate(r)}
            >
              {LABEL[r]}
            </button>
          ))}
        </nav>
        <span className="version">{version}</span>
      </div>
    </header>
  );
}

/**
 * Every page carries this. A caveat in an opening paragraph gets scrolled past,
 * and it does not survive a screenshot. A figure cropped out of this app has to
 * still say what it is.
 */
export function SyntheticBar() {
  return (
    <div className="synthetic-bar">
      <div className="wrap">
        <span className="synthetic-tag">Synthetic</span>
        Every figure here is computed from a made-up dataset. None of it is
        evidence about what anyone was paid.
      </div>
    </div>
  );
}

/** The same mark, small enough to sit against one number. */
export function SyntheticTag({ title }: { title?: string }) {
  return (
    <span
      className="synthetic-tag inline"
      title={title ?? "computed from a made-up dataset"}
    >
      Synthetic
    </span>
  );
}
