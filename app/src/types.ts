/**
 * The shapes the console renders, by the names the components use.
 *
 * Every one is an alias onto `api/schema.d.ts`, which `npm run types` generates
 * from the API's OpenAPI document. Nothing is declared by hand here: if the
 * server's response models change, `just types` changes this file's meaning
 * and the compiler reports every component that has to follow.
 */

import type { components } from "./api/schema";

type S = components["schemas"];

export type RunMeta = S["RunMeta"];
export type Step = S["Step"];
export type MarkerRow = S["MarkerRow"];
export type SupersededMarker = S["SupersededMarker"];
export type DeniedLine = S["DeniedLine"];
export type Encounter = S["Encounter"];
export type ScheduleCurve = S["ScheduleCurve"];
export type MultiplierRow = S["MultiplierRow"];
export type PfsAmounts = S["PfsAmounts"];
export type Distribution = S["Distribution"];
export type SlopeStratum = S["SlopeStratum"];
export type IndexRecord = S["IndexRecord"];
export type RulePattern = S["RulePattern"];
export type MarkerRule = S["MarkerRule"];
export type RulePack = S["RulePack"];
export type NoteEngine = S["NoteEngine"];
export type Summary = S["Summary"];
export type RunResult = S["RunResult"];

export type TableFile = S["TableFile"];
export type NoteFile = S["NoteFile"];
export type NoteBundle = S["NoteBundle"];
export type EdiFile = S["EdiFile"];
export type EdiBundle = S["EdiBundle"];
export type DeliveredFile = S["DeliveredFile"];

export type TableSummary = S["TableSummary"];
export type BundleSummary = S["BundleSummary"];
export type FileSummary = S["FileSummary"];
export type Dataset = S["Dataset"];

export type Provenance = MarkerRow["provenance"];
