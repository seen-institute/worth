# worth-console

The front end over `worth-api`. The dataset, the rule pack and every run come
from the server; nothing is compiled into the bundle. In development Vite proxies
`/api` to the backend on port 8000, and in production the host does the same
with one rewrite rule, so the app never learns a host name.

```bash
just api-dev      # the backend, http://localhost:8000/api/docs
just app-dev      # the console, http://localhost:5173
just types        # after a change to the API's response models
```

## The six tabs

Ordered for someone deciding what to do next, not for someone reading a pipeline.

**Current State**, progress against the methodology's own starting point, marked
built, assumed, missing, or waiting on somebody who is not an engineer.

**Complexity score**, how a score is made: the eleven markers, their weights and
anchors, and the actual expressions the note reader runs. Rendered from the same
rule pack the scorer loads, so it cannot drift from what executes.

**Data**, what the dataset is, where each stream would come from at a partner,
what the generator puts in, and what the system cannot do yet.

**Run**, the delivered files, the pipeline run on the server on request, and the
full output, for whoever wants the machinery.

**Price**, one code in one Medicare locality on one date: the Physician Fee
Schedule allowed amount, the arithmetic behind it, and the hash of every CMS file
it was read from. The one page that is not synthetic.

**Downloads**, the dataset as the pipeline reads it, as one archive with a
checksum list inside and as individual tables, so a clinician can read the
inputs from first principles, change them, and hand back a scenario.

Every page carries a synthetic marker, and every computed figure carries its own,
because a caveat in an opening paragraph does not survive a screenshot.

Routing is hash-based (`#/decisions`), so a deep link needs no rewrite rule on the
host and cannot 404.

## What the Run tab shows

The page opens with the files as a partner would deliver them, five delimited
Epic Clarity extracts, the clinical note dataset, and the X12 835 remittances.
The cards are drawn from the manifest; a card's contents are fetched from the
server the first time it is opened. The 835s render as segment streams with
`CLP`, `SVC`, `AMT` and `CAS` highlighted; the notes render with the sections
the Layer A rules read marked off from the history and plan sections every rule
excludes.

Ingest asks the server to run the pipeline. The result comes back in under a
second, and the steps then replay paced to the server's own per-stage timings,
so the sequence is a record of what happened rather than an animation. The
replay is provenance, not a gate: **Skip to results** jumps straight to the
numbers, and once the run has finished the same button becomes the way back
down to them. A run the server already had when the page opened is shown
finished, because replaying it would be theatre.

The output arrives in two tiers.

**Defensible from the first validated extract**, the empirical reference
distribution per code, and Method 0 as a forest of slope intervals per blinded
payer, where an interval crossing the zero line means the code has not been shown
to pay differently for harder work.

**Research stage**, everything that depends on the cross-specialty curve: the
per-code adequacy index with its bootstrap interval, the fitted curve with study
encounters plotted against it, adequacy against complexity, and a case-by-case
table that expands to the marker derivation and the adequacy arithmetic.

The case table shows twenty-five encounters with the rest one click away, three
hundred expanded rows put ten thousand pixels between a reader and everything
below them.

Payers are blinded throughout the computed output. The raw 835 segments still
show `N1*PR*` with the real payer, because that is the partner's own remittance
shown back to them as received. Each marker in that derivation is
dotted by lane, and the narrative ones quote the sentence they fired on with the
character range in the note it came from.

## Deploying to Amplify

The build spec is [`amplify.yml`](../amplify.yml) at the repository root. Amplify
builds and serves `app/dist`, and one rewrite rule, `/api/<*>` to the App Runner
service with status 200, proxies every API request through the same origin. The
`WorthConsole` stack in [`infra/`](../infra) creates the app with that rule; set
up by hand, add it under App settings, Rewrites and redirects, from the API
stack's `ApiUrl` output. `VITE_API_BASE` defaults to `/api`, so no build
variable is needed.

## Keeping it honest

`src/api/schema.d.ts` is generated from the API's OpenAPI document by
`just types` and committed. CI regenerates it and fails on a diff, so the
console's types cannot drift from what the server sends. `src/types.ts` is
nothing but aliases onto it. The serializer's own invariants (payers blinded in
every computed value, comparator derivations not shipped, delivered files served
whole) are tested in `worth-api`.

The masthead carries the rule pack's digest and status on every view. While that
status reads `provisional`, nothing the console displays is publishable.

## Stack

React 19, TypeScript in strict mode, Vite, and Recharts.

IBM Plex Sans for the interface, IBM Plex Mono for every filename, segment and
derived number. The palette is a token system in one stylesheet, ground
`#F4F7F6`, headings `#1A3644`, `#0D9488` for primary actions and highlights, a
muted rust for encounters paid below parity. Light and dark are both defined at
token level, so the page follows the viewer's theme rather than picking one.

Charts draw without entry animation. Recharts grows each scatter symbol from
zero on mount, and if the tab is not painting when a chart mounts the animation
never runs, leaving every point at `d="M0,0"` and a chart that looks
deliberately empty.
