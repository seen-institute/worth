import { useEffect, useState } from "react";

/** The palette tokens the charts need, read from CSS rather than duplicated. */
const TOKENS = [
  "--accent",
  "--accent-soft",
  "--coral",
  "--warm",
  "--body",
  "--muted",
  "--rule",
  "--rule-strong",
  "--surface",
  "--heading",
] as const;

export type Palette = Record<(typeof TOKENS)[number], string>;

function read(): Palette {
  const style = getComputedStyle(document.documentElement);
  return Object.fromEntries(
    TOKENS.map((name) => [name, style.getPropertyValue(name).trim()]),
  ) as Palette;
}

/**
 * Chart colours follow the stylesheet, including when the viewer's theme changes
 * under them. SVG fills cannot take a CSS custom property through Recharts, so
 * the tokens are resolved here and re-resolved when the colour scheme flips.
 */
export function usePalette(): Palette {
  const [palette, setPalette] = useState<Palette>(read);
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const sync = (): void => setPalette(read());
    media.addEventListener("change", sync);
    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => {
      media.removeEventListener("change", sync);
      observer.disconnect();
    };
  }, []);
  return palette;
}
