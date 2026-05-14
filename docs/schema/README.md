# `docs/schema/` — KB concept & lookup-flow diagrams

Two complementary views of how the Arknights KB is organized, both as static HTML pages served via GitHub Pages. The point is to make agent-lookup pain points visible **before** they bite — not to render real data.

| Page | What it answers |
|---|---|
| [`index.html`](./index.html) — **Concept map** | *What kinds of things exist in the KB and how do they relate?* High-level network: Operator · NPC · Event · Stage · Appearance · Co-occurrence · Relation · Summary · Resolve · Narrow · Read · Audit. |
| [`lookup-flow.html`](./lookup-flow.html) — **Lookup flow** | *What happens when an agent gets a character-name question?* Top-down decision tree (char resolve → entity resolve → grep), then the fan-out of what unlocks once you have an id, then the cheap-first reading order. Includes 5 worked examples (`阿米娅`, `特蕾西娅`, `暮落`, `年`, `玛嘉烈`) showing which branch each name takes. |

## How to view it

This is plain HTML that loads Cytoscape.js from a CDN. **You cannot view it directly on github.com** — GitHub's file viewer doesn't execute JavaScript, so the page would only show its source.

Three ways to actually render it:

### 1. GitHub Pages (canonical)

In the lib repo: **Settings → Pages → Source: Deploy from a branch → branch `main`, folder `/docs`**. Wait ~1 minute. The viewer is then live at:

```
https://<owner>.github.io/<repo>/schema/
```

(no `index.html` suffix needed — Pages serves `index.html` by default).

Pages serves anything under `docs/` once enabled. The cost: the rest of `docs/` (REQUIREMENTS.md etc.) also becomes publicly accessible. If that's a concern, move the viewer under a sibling folder configured as the Pages root instead.

### 2. htmlpreview.github.io (no setup)

Paste this URL into a browser — it fetches the raw HTML and renders it in a sandboxed iframe:

```
https://htmlpreview.github.io/?https://raw.githubusercontent.com/<owner>/<repo>/<branch>/docs/schema/index.html
```

A third-party service runs by Vlad Petriaev; useful before Pages is enabled. Slightly slower than Pages and depends on someone else's uptime.

### 3. Locally

```
cd docs/schema && python3 -m http.server 8765
# open http://127.0.0.1:8765/
```

Or just `open index.html` in a browser — `fetch('schema.json')` works fine over `file://` in Chrome/Safari/Firefox as long as the page is served from disk (no CORS issue because same origin).

## What the diagram contains

`schema.json` is the only data file (53 nodes / 80 edges / 9 layers as of the last edit). Each node has:

| Field | Meaning |
|---|---|
| `id`, `label` | identity |
| `parent` | one of nine layers (see `layers[]`): gamedata · curation · raw · derived · summary · concepts · query · wiki · agent |
| `kind` | `file` / `concept` / `command` / `actor` — drives shape and color |
| `path` | where the artifact lives in the lib/wiki/gamedata repo (when applicable) |
| `module` | the script or libs/kb file that produces it |
| `summary`, `details` | the side-panel content |
| `weakness` | non-null if this node represents a known agent-lookup gap |
| `built` | `false` for skeleton-only nodes, `"wip"` for branches not in main |

Each edge has `kind` (parses / cleans / enriches / derives / merges / bakes / consumes / audits / instance_of) — colored per `edge_kinds[]` in the JSON.

## Features in the viewer

- **Click** any node or edge → right side panel shows summary / details / file path / build module / weakness note.
- **Filter chips** (left): toggle entire layers off, or hide an edge kind to see only one type of flow (e.g. just the LLM-baked pipeline).
- **Highlight weaknesses** (top): fades everything except nodes tagged `weakness` + their neighborhood.
- **Dim WIP**: fades nodes that are not yet built (currently `der-relations`) or work-in-progress on another branch (currently `der-cooccur`).
- **Search**: substring match across label / id / summary / path; halo on hits.
- **Layout**: fcose (organic, default), dagre LR / TB (data-flow), concentric, circle. Drag freely after layout settles.

## Maintenance

The diagram is hand-curated, **not auto-generated** from code. Keep it current by editing `schema.json` whenever the KB shape changes. Pages will redeploy on push automatically.

When to touch this file:

- Adding a new `libs/kb/*.py` module that produces an artifact → add a node + edges from its inputs.
- Adding a new `kb_query` subcommand → add a node under `lyr-query` + edges from the indexes/files it consumes.
- Adding a new index in `data/kb/indexes/` → add a node under `lyr-derived`.
- Changing how edges are derived (e.g. a new tier in `participant`, or a new `match_class` rule) → update the relevant concept node's `details` + `weakness`.
- Promoting an item from `built: false` / `"wip"` to merged-in-main → drop the `built` field.

Tagged `weakness` fields surface in the "Known weakness" callout in the side panel and in the highlight-weaknesses overlay. Use them sparingly — they're for *agent-facing retrieval gaps*, not for general TODOs. The current set:

| Node | Weakness it surfaces |
|---|---|
| `cu-alias` | ~90% of curated aliases aren't in raw game data (NPC/title/group/civilian names). |
| `raw-char-manifest` | 9 ambiguous operator display names; auto-attach refused. |
| `idx-char-alias` | Without this file, resolver only sees `name + appellation`. |
| `idx-cte-part` | Single-char zh operator names — recall vs precision; default `--min-tier named` requires corroboration. |
| `idx-cte-summ` | `stage_idx` is null when only the event summary is baked; invisible to `stage_chars` until `--stages` runs. |
| `der-entities` | NPC/org/group only become typed via curation; otherwise `entity_type=unknown` placeholder. |
| `der-cooccur`, `der-relations` | wip / not-yet-built; cross-char questions can only use cooccurrence + manual reading. |
| `c-amb` | resolver returns `Ambiguous(candidates)` — caller (agent) must disambiguate. |
| `c-npc`, `c-unknown` | gap that the curation file is closing piecemeal. |
| `c-edge-sum` | mixed-granularity (stage_idx may be null) — query semantics differ. |

When you spot a new agent-lookup pain point during a Q/A or audit, tag the relevant node here instead of (or in addition to) burying it in DESIGN.md — having it surface visually beats text alone.

## Files

```
docs/schema/
  index.html         Concept map (Cytoscape + fcose + dagre via CDN)
  schema.json        Concept-map data source — edit this
  lookup-flow.html   Char-name lookup workflow (Mermaid via CDN; chart + examples inline)
  README.md          this file
```

Total install footprint: zero — both pages are pure HTML, no build step, no dependencies committed to the repo. The CDN libraries are pinned (`cytoscape@3.30.0`, `fcose@2.2.0`, `dagre@0.8.5`, `cytoscape-dagre@2.5.0`, `mermaid@11.4.0`) so the pages are reproducible.

## When to update which page

- New entity type / new edge layer / new index / new query subcommand → update `schema.json` (concept map).
- New resolver branch / changed fallback order / new tier rule / new worked-example name → update `lookup-flow.html` (the Mermaid blocks and the `EXAMPLES` JS array are both inline; edit in place).
- The lookup-flow page is **hand-written reasoning about the agent's decision tree**, not auto-derived from the concept-map data. Keep them mutually consistent — the README's maintenance rule applies to both.
