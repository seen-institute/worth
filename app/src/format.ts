export const dollars = (v: number): string =>
  v.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });

export const money = (v: number | null): string =>
  v == null
    ? "n/a"
    : v.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });

export const kb = (b: number): string =>
  b < 1024 ? `${b} B` : `${Math.round(b / 1024)} KB`;

export const ratioBand = (r: number | null): "good" | "mid" | "low" | "none" =>
  r == null ? "none" : r >= 0.95 ? "good" : r >= 0.8 ? "mid" : "low";
