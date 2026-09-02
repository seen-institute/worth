/**
 * The only place the console talks to the network.
 *
 * Every function here corresponds to one route in worth-api, and every shape
 * comes from `schema.d.ts`, which is generated from the API's own OpenAPI
 * document. If the server changes a field, the type changes with it and the
 * compiler finds every place that cared.
 */

import type { components } from "./schema";

type Schemas = components["schemas"];

export type Health = Schemas["Health"];
export type Dataset = Schemas["Dataset"];
export type DeliveredFile = Schemas["DeliveredFile"];
export type Scoring = Schemas["Scoring"];
export type RunResult = Schemas["RunResult"];
export type PriceRequest = Schemas["PriceRequest"];
export type Derivation = Schemas["Derivation"];
export type Locality = Schemas["Locality"];
export type VintageInfo = Schemas["VintageInfo"];

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** A one-line account of what went wrong, for a page to show. */
export function describe(error: unknown): string {
  if (error instanceof ApiError) return `${error.status}: ${error.message}`;
  if (error instanceof Error) return error.message;
  return String(error);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { accept: "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let detail = response.statusText || `HTTP ${response.status}`;
    try {
      const body: unknown = await response.json();
      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body &&
        typeof body.detail === "string"
      ) {
        detail = body.detail;
      }
    } catch {
      // not JSON; the status line will do
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const getHealth = (): Promise<Health> => request("/health");

/** A file as it sits in the dataset directory, e.g. `clinical/or_log.txt`. */
export const rawUrl = (path: string, download = false): string =>
  `${BASE}/dataset/raw/${path.split("/").map(encodeURIComponent).join("/")}${download ? "?download=1" : ""}`;

/** The whole dataset as one zip, with a SHA256SUMS list inside. */
export const archiveUrl = (): string => `${BASE}/dataset/archive`;

export const getDataset = (): Promise<Dataset> => request("/dataset");

export const getFile = (name: string): Promise<DeliveredFile> =>
  request(`/dataset/files/${encodeURIComponent(name.replace(/\/$/, ""))}`);

export const getScoring = (): Promise<Scoring> => request("/rulepack");

export const runPipeline = (): Promise<RunResult> =>
  request("/runs", { method: "POST" });

/** The server's last run, or null when it has not run since it started. */
export async function getLatestRun(): Promise<RunResult | null> {
  try {
    return await request<RunResult>("/runs/latest");
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export const getVintages = (): Promise<VintageInfo[]> => request("/fees/vintages");

export const getLocalities = (date: string): Promise<Locality[]> =>
  request(`/fees/localities?date=${encodeURIComponent(date)}`);

/** One allowed amount with its arithmetic and receipts; a refusal arrives as a 422. */
export const price = (body: PriceRequest): Promise<Derivation> =>
  request("/fees/price", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
