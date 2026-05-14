# Knowledge-Base Design

> A response to `REQUIREMENTS.md`. This doc is reviewable as a standalone artifact — another agent reading just this file (plus the existing `CLAUDE.md`/`AGENTS.md` it references) should be able to critique the plan without re-reading the codebase.

## TL;DR

Build a `libs/kb/` Python package that parses `ArknightsGameData` into per-stage and per-character-section chunks, organized by **four source families** (`mainline`, `activity`, `mini_activity`, `operator_record`) derived deterministically from `storyTxt` prefix and `entryType`. Raw chunks live under `data/kb/` (gitignored). LLM-derived navigation aids live under `kb_summaries/` (in git). Cross-references between chars and events are split into **deterministic edges** (372 handbook storyset links, exact match) and **inferred edges** (name-grep across mainline/activity scripts) — same map, two confidence layers. Three command-line scripts: `kb_build` (no LLM), `kb_summarize` (LLM, optional), and `kb_query` (no LLM, retrieval API as a CLI). A new `libs/llm_clients.py` adds Claude CLI alongside the existing Gemini CLI / Gemini API backends. Everything has unit tests under `tests/`.

## Entity model and v1 scope

**v1 is operator-centric.** The KB has first-class data structures for entities that exist in `character_table.json` (operators + reserve operators + Lancet-2 etc. — 444 entries today). For everything else — NPCs, titles, groups, alternate-body identities — v1 supports them only as **grep targets** on stage text. They have no `manifest.json`, no sectional files, no resolver entry.

**Why this scope:** raw `ArknightsGameData` has no NPC table. The curated alias file at `arknights_lore_wiki/data/char_alias.txt` *does* track 265 canonical entities, but **93 / 265 of those are not present anywhere in `character_table` `name` or `appellation`** — they are NPCs (`特蕾西娅`, `霜星`, `鼠王`), titles (`皇帝的利刃`), groups (`整合运动`), and alternate-body identities. A KB that promises broad lore-entity resolution from raw data alone would be a lie about what the data supports.

**v1 contract:**
- `chars/<char_id>/...` exists only for operator-table-backed entities.
- `resolve_operator_name(name)` (formerly `resolve_char`) resolves only operator display names + `appellation` codenames + (optionally) curated aliases that point to an operator.
- Anything else: agents fall back to `grep_text`.

**v2 future work (not designed yet):** a parallel `entities/<entity_id>/` layer with `manifest.json` + appearance grep results for non-operator canonical names. Triggered only when `arknights_lore_wiki/data/char_alias.txt` is present (which is what carries the curated NPC list). Re-uses `extended_<slug>` form (matching the existing `get_char_file_name` convention in `libs/game_data.py`). Out of scope for v1 — explicitly listed as future work.

## Aliases: where they come from and what they cover

The KB's "alias" concept has three potential sources, in increasing strength:

| Source | Coverage on current corpus | Type | Available in raw-only mode? |
|---|---|---|---|
| `character_table.name` (canonical display name) | 444 / 444 operators (some duplicates) | Canonical | yes |
| `character_table.appellation` (English/codename form) | 444 / 444 non-empty; **3 / 348** of curated alias entries match | Codename | yes |
| `arknights_lore_wiki/data/char_alias.txt` | 265 canonicals, 348 aliases hand-curated | Aliases (zh civilian names, titles, etc.) | **only when wiki repo present** |

`manifest.json.aliases` content per mode:
- **Raw-only:** `[name, appellation]` (de-duped, non-empty entries).
- **Enriched (alias file present):** raw-only plus all alias-file entries whose canonical maps to this operator's `name`.

This is honest about what the data supports. Agents must not assume an arbitrary zh civilian name resolves — see the resolver contract below.

## Source families (Arknights-specific)

The corpus has 461 events / 1937 stages. `entryType` from `story_review_table.json` collapses 372 of those events into a single `NONE` bucket — useless for browsing — but the `storyTxt` prefix recovers them cleanly. The KB therefore exposes **four content families** as a first-class browse axis, derived deterministically:

| Family            | Identifier rule                                      | Event count | Notes |
|-------------------|------------------------------------------------------|-------------|-------|
| `mainline`        | `storyTxt` starts with `obt/main/`                   | ~17         | also matches `entryType=MAINLINE` |
| `activity`        | `storyTxt` starts with `activities/` and `entryType=ACTIVITY`     | ~51         | side stories |
| `mini_activity`   | `storyTxt` starts with `activities/` and `entryType=MINI_ACTIVITY`| ~20         | story collections |
| `operator_record` | `storyTxt` starts with `obt/memory/`                 | 372         | operator-record / memory content; was `entryType=NONE` |
| `other`           | anything else (`obt/guide`, `activities/a001`, etc.) | ~1          | residual catch-all, kept addressable |

`source_family` is stored in every event's `event.json` and on every row of `stage_table.json`. The CLI's `--family` filter (replacing the old `--type`) browses by this. `entryType` is preserved as a secondary field but not used for primary navigation.

**Why this matters for retrieval:** operator-record content (372 events) is the bulk of the corpus and is heavily relevant for char Q/A. Treating it as a first-class family gives the agent direct access; lumping it into `NONE` would force every char-related query through a 372-element opaque bucket.

## How the KB layers cooperate

Three layers, separated for copyright and reproducibility reasons:

```
                                   ┌──────────────────────────────┐
                ArknightsGameData  │ untouched, read-only          │
                                   └───────────┬──────────────────┘
                                               │  parsed (no LLM)
                                               ▼
                              ┌──────────────────────────────────┐
                  data/kb/    │ raw chunks + structured indexes  │  gitignored
                              │ deterministic, regen in minutes  │
                              └───────────┬──────────────────────┘
                                          │  LLM (offloaded to Gemini/Claude CLI)
                                          ▼
                          ┌──────────────────────────────────────┐
            kb_summaries/ │ per-event short summaries +          │  IN GIT
                          │ key-character extractions per event  │
                          │ navigation aids — small, processed   │
                          │ (v1: events only, no char summaries) │
                          └──────────────────────────────────────┘
```

Ad-hoc Q/A and audit tasks may use any layer. Building the KB never requires `kb_summaries/`; agents *prefer* it when present because it's smaller and faster to scan.

## On-disk layout

```
arknights_lore_wiki_lib/
  data/                                 # already gitignored
    kb/
      manifest.json                     # version, build timestamp, source data_version, clean_script hash
      events/
        <event_id>/                     # flat by event_id; family is a metadata field, not a path segment
          event.json                    # event metadata: id, name, entryType, source_family, storyTxt_prefixes, stage list
          stage_<NN>_<slug>.txt         # raw cleaned text, per-stage chunk
      chars/
        <char_id>/                      # one directory per character — sectional, not monolithic
          manifest.json                 # char_id, name, aliases, nationId, sections present, storyset summary
          profile.txt                   # 招聘文本 + nationId (small)
          voice.txt                     # 干员语音
          archive.txt                   # 干员档案 (handbook stories text)
          skins.txt                     # 干员皮肤
          modules.txt                   # 干员模组
          storysets.json                # deterministic links to story-review event_ids via handbook storyTxt
      indexes/
        events_by_family.json           # mainline / activity / mini_activity / operator_record / other
        char_alias.json                 # OPTIONAL — built only if arknights_lore_wiki/data/char_alias.txt exists
        char_to_events_deterministic.json   # char_id → [event_id, …] from handbook storysets (high confidence)
        char_to_events_inferred.json    # char_id → [event_id, …] from grep over stage text (recall floor)
        event_to_chars.json             # flat: list of {char_id, event_id, stage_idx, source, count?, match_class?} — one row per (char, stage)
        stage_table.json                # flat: (event_id, stage_idx, name, avgTag, source_family, storyTxt_prefix, file_path, length)
        char_table.json                 # flat: char_id, name, nationId, sections present, has_handbook, has_voice, …

  kb_summaries/                          # IN GIT (new top-level folder, tracked)
    AGENTS.md                           # entry point for agents using summaries
    events/
      <event_id>.md                     # ~300-word zh summary + 关键人物 list
    manifest.json                       # which events have summaries; source-chunk hashes for staleness detection
    # NOTE: no chars/ subfolder in v1 — per-char summaries deferred (see summarize.py rationale).

  libs/
    kb/                                 # NEW — KB construction & retrieval package
      __init__.py
      build.py                          # write data/kb/ from ArknightsGameData (no LLM)
      chunker.py                        # per-stage and per-char chunking logic
      indexer.py                        # build the indexes/*.json files
      query.py                          # retrieval API; pure functions
      summarize.py                      # write kb_summaries/ (LLM)
      paths.py                          # central path helpers
    llm_clients.py                      # NEW — unified backend dispatch (cli/gai/claude)
    bases.py                            # existing — keep as-is, may move llm dispatch into llm_clients.py
    game_data.py                        # existing — reused unchanged (or with minor extractions)
    ui.py                               # existing — untouched

  scripts/
    kb_build.py                         # NEW — top-level "build the whole KB"
    kb_summarize.py                     # NEW — top-level "bake LLM summaries"
    kb_query.py                         # NEW — CLI wrapper around libs.kb.query
    kb_audit_wiki.py                    # NEW — audit helper (existing wiki vs KB raw)
    # existing scripts kept untouched

  tests/
    __init__.py
    test_chunker.py
    test_indexer.py
    test_query.py
    test_paths.py
    test_llm_clients.py                 # mock-based; no real LLM calls
    fixtures/
      mini_gamedata/                    # tiny synthetic gamedata snapshot for tests

  docs/                                  # IN GIT
    REQUIREMENTS.md                      # already written
    DESIGN.md                            # this file
    DECISIONS.md                         # decision log (project convention)
    PROMPTS.md                           # the Chinese prompts used by kb_summarize
    AGENTS_GUIDE.md                      # how an agent should use the KB end-to-end
```

`.gitignore` updates: `data/` already excludes everything in `data/kb/`. We need to **add** `!kb_summaries/` is not necessary because `kb_summaries/` is at the repo root and isn't matched by `data/`. But we should add a guard rail:

```
# .gitignore additions
data/kb/                       # explicit (already covered by data/, but explicit is kinder)
kb_summaries/manifest.json     # commit-worthy; just to confirm intent — actually we want this in git, no rule needed
```

Net `.gitignore` change is zero rules; `data/` already protects raw chunks, and `kb_summaries/` is outside `data/`.

## Module: `libs/kb/`

### `paths.py`
Central helpers so every module agrees on where things live. Reads `keys.json` once. Exposes:
- `KB_ROOT = data/kb/`
- `SUMMARIES_ROOT = kb_summaries/`
- `event_dir(event_id)`, `stage_path(event_id, stage_idx, slug)`, `char_dir(char_id)`, `char_section_path(char_id, section)`, `char_storysets_path(char_id)`, `index_path(name)`, `event_summary_path(event_id)`. (No `char_summary_path` — per-char summaries are not in v1.)
- `safe_slug(s)` — wraps `bases.get_simple_filename`.
- `source_family(story_txt: str, entry_type: str) -> Family` — pure function; the single source of truth for family classification (used by both build and indexer).

### `chunker.py`
**Per-stage chunk** (the unit of an LLM-readable narrative slice):
- Source: `extract_data_from_story_review_table` (existing) + `get_raw_story_txt` (existing, reuses `clean_script`).
- Filename: `stage_<NN>_<slug>.txt` where `NN` is zero-padded `storySort`-derived index, `slug` from `storyName + avgTag`.
- Body: a small frontmatter block followed by the cleaned dialogue:
  ```
  <章节>
  <活动名称>...</活动名称>
  <活动ID>...</活动ID>
  <章节序号>03</章节序号>
  <章节名称>... (avgTag)</章节名称>
  <章节简介>... (storyInfoTxt)</章节简介>
  <正文>
  [cleaned script]
  </正文>
  </章节>
  ```
- Size targets: median ~5K chars, p95 ~10K, max ~21K (verified against current data). Comfortably fits any model.
- A 6-char SHA suffix (from `bases.get_simple_filename`) guards against zh-CN names with filesystem-unfriendly chars.

**Per-event "manifest" chunk** (`event.json`):
```json
{
  "event_id": "act46side",
  "name": "...",
  "entryType": "ACTIVITY",
  "source_family": "activity",
  "storyTxt_prefixes": ["activities/act46side"],
  "stages": [
    {"idx": 0, "name": "...", "avgTag": "行动前", "file": "stage_00_....txt", "length": 5023, "story_txt": "activities/act46side/level_a046_01_beg"}
  ],
  "total_length": 56789,
  "source_data_version": "v25-04-28-09-04-00"
}
```

`storyTxt_prefixes` is a sorted list because two events in the live corpus
span more than one prefix: `main_0` covers `obt/guide` + `obt/main` (the
prologue's first stage is the welcome tutorial), and `act3d0` covers its
own `activities/act3d0` plus a one-stage detour into `activities/act11d7`.
A scalar field would silently lose the second subtree.

**Per-character data** is **sectional, not monolithic** — one directory per operator with separate files per logical section. (Reminder: v1 `chars/` is operator-only; non-operator entities are out of scope per "Entity model and v1 scope" above.) The existing `get_char_info_text_prompt` flattens everything into one blob; we keep that helper for backward compat but the KB stores sections separately so an agent reading "what does the voice say about X" doesn't have to drag in 招聘文本 / 模组描述 / 皮肤 旁白.

Layout per `chars/<char_id>/`:

| File | Source | Body |
|---|---|---|
| `manifest.json` | `extract_data_from_character_table` (extended to keep `appellation`) + this design | char_id, name, **appellation**, aliases (see below), nationId, sections present, storyset summary |
| `profile.txt` | `character_table.itemUsage` + `itemDesc` | `<干员招聘文本>...</干员招聘文本>` + nationId |
| `voice.txt` | `extract_data_from_charword_table` | `<干员语音>...</干员语音>` |
| `archive.txt` | `extract_data_from_handbook_info_table.stories` | `<干员档案>...</干员档案>` |
| `skins.txt` | `extract_data_from_skin_table` | `<干员皮肤>...</干员皮肤>` |
| `modules.txt` | `extract_data_from_uniequip_table` | `<干员模组>...</干员模组>` |
| `storysets.json` | `extract_data_from_handbook_info_table.storysets` | list of `{storySetName, storyTxt, linked_event_id, linked_stage_idx}` |

`extract_data_from_character_table` currently retains only `name / itemUsage / itemDesc / nationId` (`libs/game_data.py:68-75`). It must be extended to also keep `appellation` — a small change, low blast radius, but **load-bearing for `manifest.aliases`**.

Each section file is small (typically <2 KB; entire char dossier <12 KB at p95, max ~11 KB measured). Files only exist if the section has content. `manifest.json` records which sections are populated so a query layer doesn't need to stat each file.

`manifest.json`:
```json
{
  "char_id": "char_002_amiya",
  "name": "阿米娅",
  "appellation": "Amiya",
  "aliases": ["阿米娅", "Amiya"],
  "nationId": "rhodes",
  "sections": ["profile", "voice", "archive", "skins", "modules"],
  "storyset_count": 3
}
```

`aliases` content depends on whether the curated alias file is present at index time:

- **Raw-only mode** (no `arknights_lore_wiki/data/char_alias.txt`): `aliases = dedupe([name, appellation])`. Typically zh canonical + English codename. **No civilian names, titles, or fan labels.**
- **Enriched mode** (alias file present): the above, plus every alias-file line whose canonical equals `name` **and that canonical is unique among operators**. This is where civilian names like `玛嘉烈` for `临光`, `劳伦缇娅` for `幽灵鲨`, `AMa-10` for `凯尔希` enter the index.

**Ambiguous canonicals are not auto-attached.** The display-name → `char_id` join is not unique: there are 9 duplicate operator display names (`暮落`, `郁金香`, `Sharp`, `Stormeye`, `Pith`, `Touch`, plus three `预备干员-*`), and at least one already collides with the curated alias data (`暮落;沉渊` is in `char_alias.txt`, but `暮落` maps to both `char_512_aprot` and `char_4025_aprot2`). Attaching `沉渊` to either operator alone would be arbitrary; attaching it to both would silently broaden the alias's scope. Per Codex review 06 finding 3, the rule is: **if `canonical` collides with a duplicate display name, the line is loaded into a separate `ambiguous_aliases` map keyed by canonical**, not into any operator's `manifest.aliases`. `resolve_operator_name(name)` consults this map and returns `Ambiguous(candidates)` when the input matches an ambiguous-canonical alias — the caller (agent) disambiguates from there.

Coverage reality check (measured 2026-05-08): of 348 entries in the curated alias file, only 33 match `name` and 3 match `appellation`. Roughly **90% of curated aliases are simply not in the game data** — they are zh civilian names, titles, fan/community labels. Without the curated file, alias resolution covers display name + English codename only.

`storysets.json` is **the deterministic char→event index, scoped per char**. It's pre-resolved during build using `storyTxt` lookup against the parsed story-review table. Verified: 372 storysets, 372 linked, 0 unlinked, 0 ambiguous (see Measurements below).

### `indexer.py`
Pure-code (no LLM) builders for the JSON indexes. Two passes: deterministic edges first (cheap, exact), then inferred edges (grep, recall floor).

1. **`events_by_family.json`** — group by `source_family` derived in `paths.source_family(story_txt, entry_type)`. One section per family with an ordered event list (sort key: `event_id` for now; mainline could later be sorted by chapter number). This replaces the previous `events_by_type.json`.

2. **`char_to_events_deterministic.json`** — built from each char's `storysets.json`. Format:
   ```json
   {
     "char_002_amiya": [
       {"event_id": "obt_memory_amiya_1", "stage_idx": 0, "story_set_name": "..."}
     ]
   }
   ```
   This is **the high-confidence layer** — every link is a direct game-data pointer (handbook → story-review). 372 links across 372 events, all verified deterministic.

3. **`char_to_events_inferred.json`** — for each canonical char name (and aliases, when available), substring-search per-stage chunks. Records per-hit `{event_id, stage_idx, count}` so callers can rank. Recall floor for chars *not* in the deterministic map (and additional event mentions for chars who are). The matcher splits inputs into three classes with different rules — earlier "2-char minimum across the board" was wrong (it dropped 23 single-zh-char operators, including `陈`, `年`, `夕`, `黑`, `令`, per Codex review 06 finding 1):
   - **Class A — canonical operator names (`name`, `appellation`):** **no length floor**. Every operator display name is matched literally even if it's a single character. False positives are accepted as the cost of recall; the deterministic layer remains the precision floor and the blocklist filters obvious common-noun overlaps. Single-char names get a confidence demerit (see below) but are never silently dropped.
   - **Class B — curated aliases (when alias file is present):** 2-char minimum kept. These are user-curated zh nicknames / civilian names where short strings are more likely to be commentary noise than identity references.
   - **Class C — fuzzy / ambient strings:** 2-char minimum, blocklist applied. Same rationale as Class B.
   - Hardcoded blocklist for common-noun-ish aliases (`博士`, `罗德岛`, etc. — full list in `PROMPTS.md`). Applies across all classes.
   - **Subtraction rule:** if a `(char_id, event_id)` pair already appears in the deterministic index, the inferred index does *not* duplicate it. Inferred is purely *additional* edges.
   - **Per-edge `match_class`** (`canonical_short | canonical | curated | fuzzy`) is recorded in the JSON so consumers can downweight `canonical_short` hits when they want higher precision. The CLI never auto-filters — surfacing the signal is enough.

4. **`event_to_chars.json`** — combined map. **Flat one-row-per-(char, stage) shape** (per Codex review 07-consistency finding 2). Earlier drafts mixed two shapes (deterministic = single stage, inferred = aggregate `stage_hits[]`); that was internally inconsistent with the `Appearance` API type. Now all rows are flat:
   ```json
   {
     "act46side": [
       {"char_id": "char_xxx_skadi", "source": "deterministic", "stage_idx": 2, "story_set_name": "..."},
       {"char_id": "char_yyy_grani", "source": "inferred", "stage_idx": 0, "count": 5,  "match_class": "canonical"},
       {"char_id": "char_yyy_grani", "source": "inferred", "stage_idx": 3, "count": 9,  "match_class": "canonical"},
       {"char_id": "char_010_chen",  "source": "inferred", "stage_idx": 1, "count": 2,  "match_class": "canonical_short"}
     ]
   }
   ```
   - One row per `(char_id, stage_idx)`. A char appearing in 4 stages = 4 rows, not 1.
   - Deterministic rows omit `count` and `match_class` (those are inferred-only concepts).
   - Event-level aggregation (e.g. "all stages where char_yyy_grani appears in act46side") is the caller's responsibility — `query.py` provides a helper.
   - Stage precision is what makes "guaranteed in this stage" claims honest — keeping it lets `event_chars` and `stage_chars` queries return tight scopes (Codex review 04 finding 4).

5. **`char_alias.json`** — **OPTIONAL enrichment**, not a build prerequisite. If `arknights_lore_wiki/data/char_alias.txt` exists, the indexer ingests it and uses it to expand the inferred-edge grep pass. If absent, the build proceeds with canonical names only and emits a notice in the build log. This honors the "raw game data only" KB contract: the KB **builds without the wiki repo**; aliases are an enrichment that improves recall when available.

6. **`stage_table.json`** — flat list of every stage with metadata, including `source_family` and `storyTxt_prefix`. Sortable / filterable on any field.

7. **`char_table.json`** — flat per-char metadata: `char_id`, `name`, `nationId`, `sections` (list of populated section files), `storyset_count`, `has_participant_appearances`.

> **Superseded by WS-0 (plan phase P-B) — `libs/kb/participants.py`.** The "inferred edge" pass (§3–4 above: `char_to_events_inferred.json`, flat `count` + `match_class`, event-level deterministic subtraction) is replaced by a **tiered participant extractor**. Three edge layers now:
> 1. `char_to_events_deterministic.json` — unchanged (§264 above). Source label on rows: `"deterministic"`. Ground truth; always passes any `--min-tier`.
> 2. `char_to_events_participant.json` — per-stage rows `{event_id, stage_idx, source:"participant", tier, spoke_lines, mention_count, matched_aliases}`. `tier ∈ {speaker, named, mentioned}` (see `participants.py` docstring for the rules — speaker-line parsing off `clean_script`'s `名字:台词`; ASCII names use a real word boundary; single-zh-char names need ≥2 hits / a speaker line / a summary hit to clear `mentioned`). Deterministic subtraction is now **per `(char_id, event_id, stage_idx)`** (not per event-pair), so a char's other stages in a storyset-linked event still surface.
> 3. `char_to_events_summary.json` — rows `{event_id, stage_idx, source:"summary", tier:"named", matched_aliases}` from the `<关键人物>` tags of the baked `kb_summaries`, each name resolved through the alias index, hash-gated free (no LLM call). Two layers (P-C): `kb_summaries/stages/<event_id>/<NN>.md` → *stage-scoped* rows (`stage_idx` = the chapter index); `kb_summaries/events/<event_id>.md` → an *event-scoped* row (`stage_idx: null`), but only for `(char, event)` pairs not already covered by a stage-scoped row (the stage breakdown subsumes it). A stage-scoped row is suppressed when a deterministic edge already links `(char_id, event_id, stage_idx)`; an event-scoped row, when one links `(char_id, event_id)`. Unresolved/ambiguous surface names are reported in the build manifest (`unresolved_summary_names`).
>
> `event_to_chars.json` merges all three (each row carries `source` + the source-specific extras; an event-scoped `summary` row has `stage_idx: null`, a stage-scoped one a real index). `kb_query event chars|stage_chars|char appearances` take `--source {deterministic,participant,summary,all}` (default `all`) and `--min-tier {speaker,named,mentioned}` (default `named`). `Appearance` gains `tier`, `spoke_lines`, `mention_count`, `matched_aliases`, and `stage_idx` becomes `int | None`; `count` / `match_class` are gone from the public type (`matched_aliases` carries the surfaces instead). `match_class` is still an internal alias-classification concept inside the indexer / `participants` (blocklist + per-class length floor in `classify_alias`).

### `query.py`
Pure-function retrieval API. Every function takes a `KB` object (loaded indexes) and returns Python values. CLI wrappers in `scripts/kb_query.py` print JSON.

```python
def load_kb(kb_root: Path) -> KB                            # mmap-cheap; reads indexes only

# Event browsing — family-aware, not entryType-aware
def list_events(kb: KB, family: Family | None = None) -> list[EventMeta]
def list_families(kb: KB) -> dict[Family, int]               # {family: event_count}
def get_event(kb: KB, event_id: str) -> EventMeta            # incl. stage list, source_family
def get_stage_text(kb: KB, event_id: str, stage_idx: int) -> str

# Character data — sectional access (operator-only; see "Entity model and v1 scope")
def list_chars(kb: KB, nation: str | None = None) -> list[CharMeta]
def resolve_operator_name(kb: KB, name_or_alias: str) -> Resolution
                                                              # Resolution = Resolved(char_id) | Ambiguous([char_id, …]) | Missing
                                                              # Searches name + appellation + (when present) curated alias map.
                                                              # Does NOT match NPCs / titles / groups (out of v1 scope).
def get_char_section(kb: KB, char_id: str, section: Section) -> str
                                                              # Section in {profile, voice, archive, skins, modules, all}
def char_storysets(kb: KB, char_id: str) -> list[StorySetLink]   # deterministic, from chars/<id>/storysets.json

# Cross-references — confidence-tagged AND stage-precise where known
def char_appearances(
    kb: KB, char_id: str, source: Literal['deterministic','inferred','both'] = 'both'
) -> list[Appearance]
def event_chars(
    kb: KB, event_id: str, source: Literal['deterministic','inferred','both'] = 'both'
) -> list[Appearance]                                          # returns Appearance, not CharMeta — preserves stage_idx + source
def stage_chars(
    kb: KB, event_id: str, stage_idx: int,
    source: Literal['deterministic','inferred','both'] = 'both'
) -> list[Appearance]                                          # tight scope: chars whose edge points at this exact stage

# Free-text search and summaries
def grep_text(
    kb: KB, pattern: str, scope: str = "all", *, regex: bool = False
) -> list[Match]                                                       # literal substring by default
def get_event_summary(kb: KB, event_id: str) -> str | None             # v1: events only
```

**`grep_text` is literal-by-default.** The fallback path is hit hardest by exactly the names that break naive regex: NPC names with parentheses (`AUS (群体)`, `真龙 (当今)`), hyphens (`Ishar-mla`), or smart quotes (`"桥夹"克里夫`). Pass `regex=True` only when regex semantics are wanted; the CLI mirrors this with `--regex` (default off). Per Codex review 05 finding 3.

`Appearance` carries `(event_id, stage_idx, source, count_or_none, match_class_or_none, story_set_name_or_none)`. Every row is stage-level — no aggregate shape. `source` is `'deterministic' | 'inferred'`; for inferred edges, `match_class` is `'canonical_short' | 'canonical' | 'curated' | 'fuzzy'` (per the indexer rule above). `count` and `match_class` are populated for inferred rows only; `story_set_name` is populated for deterministic rows only. Callers wanting event-level rollup use `query.py`'s `group_by_event(appearances)` helper rather than expecting the index or the API to do it.

`kb_query` JSON output preserves `match_class` so an agent can downweight `canonical_short` hits without making a second call (per Codex review 07-independent finding 2).

`Resolution` is a tagged union (Python: small dataclass / `Literal['kind']` discriminator). The resolver must surface ambiguity rather than picking arbitrarily — measured 2026-05-08, the corpus has 9 duplicate display names (`暮落`, `郁金香`, `Sharp`, `Stormeye`, `Pith`, `Touch`, plus three `预备干员-*`). When `Ambiguous`, callers print all candidates and either ask the user or use a secondary signal (nation, sections present) to disambiguate.

`Match` returns `(event_id, stage_idx_or_none, char_id_or_none, line, line_no, snippet)`.

### `summarize.py`
The only LLM-using module. For each event:
1. Read all stages of the event from `data/kb/events/<id>/`.
2. **Multi-pass trigger** (M5-derived): if `total_length > 80,000` chars OR `stage_count > 10`, summarize stage-by-stage and merge. Otherwise, one prompt. With these cutoffs, ~70-90 events go multi-pass (the union of the two conditions); the rest fit comfortably in a single prompt.
3. Use Chinese prompts (see `docs/PROMPTS.md`).
4. Write to `kb_summaries/events/<event_id>.md`.
5. Record source hashes in `kb_summaries/manifest.json` so we can detect staleness when game data updates.

**No per-character summaries in v1.** Char dossiers are already sectional and small (M4: median 5484, max 11460 chars), `manifest.json` already carries the structured navigation aids, and a one-line LLM abstraction would duplicate information that's cheaper and more accurate to read directly. Per-char summarization is deferred to a possible v2 if a use case emerges; until then, the agent reads `chars/<id>/manifest.json` for navigation and the section files for content. (Reasoning per Codex review 04 finding 3.)

`summarize.py` uses `libs/llm_clients.py`, defaults to `gemini` CLI, supports `--llm gai|cli|claude`.

### `libs/llm_clients.py` (new module)

Refactor of the existing `bases.query_llm*` so all three backends sit behind one interface:

```python
class LLMClient(Protocol):
    def query(self, system: str, prompt: str, *, model: str | None = None) -> str: ...

def make_client(backend: str = "cli", **kwargs) -> LLMClient
```

Implementations:
- `GeminiCLIClient` — wraps existing `query_llm_cli`; default `gemini-3.1-flash`.
- `GeminiSDKClient` — wraps `query_llm_gai`; default `gemini-2.5-flash`.
- `ClaudeCLIClient` — new. Shells out to the local Claude CLI in print mode, passes the user prompt on stdin, requests JSON output, and checks the returned `is_error` flag so rate-limit / blocked / model-not-found responses that exit 0 still surface as failures. Defaults: read from `keys.json` `claude_cli_path` and `claude_model` (e.g. `claude-haiku-4-5`).
- All clients honor `RETRY_LIMIT` / `RETRY_SLEEP_TIME` and surface `LLMError` on persistent failure (consistent with existing behavior).

`bases.query_llm` and `query_llm_validated` keep their signatures; internally delegate to `llm_clients`. No existing scripts change.

## Measurements (reproducible)

Every size / count number elsewhere in this doc was measured with one of the snippets below, run from the lib repo root. They depend only on `libs/` + `keys.json`; both sit in `tmp/measure_*.py` once Phase 1 lands but are short enough to inline here for review.

**M1 — event / stage counts and sizes** (used for "461 events / 1937 stages / median 5K / max 21K"):
```bash
.venv/bin/python -c "
from libs.game_data import extract_data_from_story_review_table, get_raw_story_txt
from libs.bases import get_value
gp = get_value('game_data_path')
sr = extract_data_from_story_review_table(gp)
sizes = []
for eid, ev in sr.items():
    for s in ev['stages']:
        sizes.append(len(get_raw_story_txt(gp, s['storyTxt'])))
sizes.sort()
print('events:', len(sr), 'stages:', len(sizes))
print('stage size: median', sizes[len(sizes)//2], 'p95', sizes[int(len(sizes)*0.95)], 'max', sizes[-1])
"
```

**M2 — `entryType` collapse and `storyTxt` prefix recovery** (basis for the source-family decision):
```bash
.venv/bin/python -c "
from libs.game_data import extract_data_from_story_review_table
from libs.bases import get_value
from collections import Counter
sr = extract_data_from_story_review_table(get_value('game_data_path'))
print('entryType counts:', Counter(ev['entryType'] for ev in sr.values()))
print('storyTxt first-stage prefix counts:')
for k, v in Counter('/'.join(ev['stages'][0]['storyTxt'].split('/')[:2]) for ev in sr.values()).most_common():
    print(f'  {k}: {v}')
"
```

Result observed 2026-05-08: `entryType` = `{NONE: 372, ACTIVITY: 51, MINI_ACTIVITY: 20, MAINLINE: 18}`. `storyTxt` prefix = `{obt/memory: 372, obt/main: 17, activities/<event>: ~71, obt/guide: 1, ...}`. **All 372 NONE events live under `obt/memory/`** — clean enough to use as the family discriminator.

**M3 — handbook storyset → story-review linkage** (basis for "deterministic edges"):
```bash
.venv/bin/python -c "
from libs.game_data import (
    extract_data_from_story_review_table,
    extract_data_from_handbook_info_table,
    handbook_info_filename,
)
from libs.bases import get_value
gp = get_value('game_data_path')
sr = extract_data_from_story_review_table(gp)
hb = extract_data_from_handbook_info_table(gp, handbook_info_filename)
idx = {}
for eid, ev in sr.items():
    for i, s in enumerate(ev['stages']):
        idx.setdefault(s['storyTxt'], []).append((eid, i))
total = linked = ambig = 0
unlinked = []
for cid, val in hb.items():
    for ss in val['storysets']:
        total += 1
        hits = idx.get(ss['storyTxt'], [])
        if not hits: unlinked.append(ss['storyTxt'])
        elif len(hits) > 1: ambig += 1
        else: linked += 1
print(f'total={total} linked={linked} ambig={ambig} unlinked={len(unlinked)}')
"
```

Result observed 2026-05-08: `total=372 linked=372 ambig=0 unlinked=0`. **Every handbook storyset has a deterministic, unique link to a story-review event.**

**M4 — character text size per section** (used for "<2 KB per section, p95 9 KB total, max 11 KB"):
```bash
.venv/bin/python -c "
from libs.game_data import get_all_char_info, get_char_info_text_prompt
from libs.bases import get_value
ci, _ = get_all_char_info(get_value('game_data_path'))
named = [v for v in ci.values() if v.get('name')]   # get_char_info_text_prompt requires name
sizes = sorted(len(get_char_info_text_prompt(v)) for v in named)
print('total entries:', len(ci), 'with name:', len(named))
print('median', sizes[len(sizes)//2], 'p95', sizes[int(len(sizes)*0.95)], 'max', sizes[-1])
"
```

Result observed 2026-05-08: `total entries: 449  with name: 444  median: 5484  p95: 9016  max: 11460`. (Earlier draft of this doc had a transcription error and a missing `name` guard — fixed after Codex review 04 finding 1.)

**M5 — event-size thresholds for the summarization multi-pass trigger** (used to set the `total_length` and `stage_count` cutoffs):
```bash
.venv/bin/python -c "
from libs.game_data import extract_data_from_story_review_table, get_all_text_from_event
from libs.bases import get_value
gp = get_value('game_data_path')
sr = extract_data_from_story_review_table(gp)
sizes = sorted(len(get_all_text_from_event(gp, ev)) for ev in sr.values())
counts = sorted(len(ev['stages']) for ev in sr.values())
for thr in (50000, 80000, 100000, 150000, 200000):
    print(f'events > {thr}:', sum(1 for s in sizes if s > thr))
print(f'stages: median {counts[len(counts)//2]} p90 {counts[int(len(counts)*0.9)]} max {counts[-1]}')
print(f'events with > 10 stages:', sum(1 for c in counts if c > 10))
"
```

Result observed 2026-05-08: events > 50K: 78, > 80K: 51, > 100K: 35, > 150K: 11, > 200K: 2. Stages per event: median 1, p90 19, max 41. 69 events have >10 stages.

These commands are the source of truth for sizing decisions. Any prompt-size threshold (e.g. "summarize whole event in one shot if total < 200 KB") is set against them, not invented.

## Build pipeline (no LLM)

`scripts/kb_build.py` — single command, idempotent, ~minutes:

1. Read `keys.json` for `game_data_path`.
2. Snapshot `<game_data_path>/zh_CN/gamedata/excel/data_version.txt` and the SHA of `clean_script`'s source code → `manifest.json`. The latter is so we can detect when a parser change invalidates cached chunks.
3. Parse `story_review_table.json`. For each event:
   - Compute `source_family` via `paths.source_family(stage[0].storyTxt, entryType)`.
   - Write `event.json` (with `source_family`, `storyTxt_prefixes`).
   - Write one `stage_<NN>_<slug>.txt` per stage. Reuse `extract_data_from_story_review_table` and `get_raw_story_txt` (which calls `clean_script`).
4. Parse char tables once via `get_all_char_info`. For each char **with a non-empty `name`** (449 records total → 444 named; the 5 nameless records, all `npc_*`, are skipped):
   - Write `chars/<char_id>/manifest.json` (manifest expects `name` — nameless records cannot satisfy the schema).
   - Write each populated section (`profile.txt`, `voice.txt`, `archive.txt`, `skins.txt`, `modules.txt`).
   - Resolve handbook storysets against the story-review `storyTxt` index → write `chars/<char_id>/storysets.json` with linked `event_id`/`stage_idx`.
   - Skipped (nameless) records are listed in the build report so they're visible, not silent. They are **not** addressable by `resolve_operator_name`; if they ever surface in raw stage text, `grep_text` finds the strings directly.
5. Build all indexes via `indexer` (deterministic edges from storysets first; inferred edges via grep second; `event_to_chars.json` is the merged view).
6. Print a summary table: events written by family, stages written, chars written, sections per char, total bytes, link counts (deterministic vs inferred).

Idempotent: re-running compares hashes; only re-writes changed files. Use `os.replace` for atomicity (existing convention).

**Pruning is part of the build contract** (per Codex review 07-consistency finding 3). When the upstream `ArknightsGameData` removes or renames an event, char, or storyset, the corresponding `data/kb/events/<id>/`, `data/kb/chars/<id>/`, or storyset entry no longer appears in the freshly computed manifest. After writing all current outputs, `kb_build.py` deletes any `events/<id>/` or `chars/<id>/` directory not referenced by the new manifest **unless `--no-prune` is passed**. The same pruning rule applies to `kb_summarize.py` for `kb_summaries/events/<id>.md` files. Removed-path lists are printed in the build report so the user sees what got pruned.

## Summary pipeline (LLM)

`scripts/kb_summarize.py` — opt-in, costs tokens:

```
kb_summarize.py [--llm cli|gai|claude] [--model ...]
                [--stages]
                [--event <event_id> ...] [--force] [--estimate] [--no-prune]
```

Two bakes (no char summaries — see `summarize.py` rationale above):

- **default** — one summary per event → `kb_summaries/events/<id>.md` (single-pass or, past the M5 threshold, per-stage reduce + merge).
- **`--stages`** — one summary per `<章节>` → `kb_summaries/stages/<event_id>/<NN>.md`, always single-pass (no stage chunk approaches the threshold; ~1937 stages corpus-wide — `--stages --estimate` prints the projected ~13M-token cost). This is plan phase P-C: the chapter-level retrieval layer. `kb_build` reads these files too — each `<关键人物>` becomes a *stage-granular* `summary`-source char↔stage edge (`participants.build_char_to_events_summary`), replacing the event-scoped one for that `(char, event)`.

Both share `summarize._run_batch`: defaults to `--llm cli`; skips units whose source hash matches the manifest (separate `events` / `stages` sections, no re-billing on unchanged inputs); persists the manifest after every write so a kill / quota wall mid-bake never loses paid-for work (re-run to resume); bails the whole batch on a terminal LLM error (quota / bad model / auth); validates the expected zh tags per unit and retries once on failure (mirroring `query_llm_validated`). `--estimate` is a no-LLM dry-run of the run that would happen. Pruning (above) applies to both `kb_summaries/events/<id>.md` and `kb_summaries/stages/<event_id>/<NN>.md` (and now-empty stage dirs) on a full, completed run.

## Retrieval pipeline (no LLM)

`scripts/kb_query.py` — exposes `query.py` as a JSON-printing CLI for the agent:

```
kb_query.py event list [--family mainline|activity|mini_activity|operator_record|other]
kb_query.py event get <event_id>
kb_query.py event chars <event_id> [--source deterministic|inferred|both]
kb_query.py event stage_chars <event_id> <stage_idx> [--source deterministic|inferred|both]
kb_query.py event stage <event_id> <stage_idx> [--text]
kb_query.py family list                     # families + event counts
kb_query.py char resolve <name_or_alias>     # operator-only; output: resolved/ambiguous/missing + candidates
kb_query.py char get <char_id> [--section profile|voice|archive|skins|modules|all] [--text]
kb_query.py char appearances <char_id> [--source deterministic|inferred|both]
kb_query.py char storysets <char_id>        # deterministic links only
kb_query.py grep "<text>" [--regex] [--in events|chars|all]      # literal substring by default; --regex opts in
kb_query.py summary event <event_id>
# NOTE: `summary char <id>` not in v1 — char dossier is read directly via `char get`.
```

JSON output by default; `--text` flag returns raw text for the agent to read directly. The agent learns the command surface from `docs/AGENTS_GUIDE.md`.

## Application: ad-hoc Q/A

Pattern an agent follows:

1. Resolve any character name in the user's question via `kb_query char resolve <name>`.
2. Use `char appearances` or `event_to_chars` to narrow scope.
3. Read `kb_summaries/events/<id>.md` first (small, fast).
4. If the summary doesn't answer, drop down to the relevant raw stage(s) via `event stage --text`.
5. If the question is keyword-based ("are there any references to 圣巡 in the game?"), `grep_text` first, then read hits.

This is documented in `docs/AGENTS_GUIDE.md` with worked examples.

## Application: audit a freshly generated wiki summary

The requirement (`REQUIREMENTS.md:18-20`, `:55`) is to catch three failure classes: **(a) missed scenes / characters**, **(b) wrong attributions**, and **(c) hallucinations**. Earlier drafts of this design used only an entity-set diff — review 06 finding 2 correctly pointed out that diff alone catches (a) but is blind to (b) and (c) when the offending text references entities both the raw and the summary already mention. The audit therefore runs **two complementary signals**, not one.

### Signal 1 — Entity-coverage diff (cheap; for failure class (a))

Given a story summary at `arknights_lore_wiki/data/stories/<event_id>.txt` and the raw chunks under `data/kb/events/<event_id>/`:

1. **Extract operator-set candidates from raw text** — grep canonical operator names + curated aliases against the stage chunks; same matcher used by the inferred-edge index.
2. **Extract NPC-shaped candidates from raw text** (per Codex review 07-independent finding 1). The operator-only entity set misses NPC-led scenes (`特蕾西娅`, `霜星`, `鼠王`-style proper nouns), so without this Signal 1 has a known blind spot. Code-only rule:
   - Tokenize via a zh proper-noun regex `[一-鿿]{2,6}` (length 2-6).
   - Keep tokens with **frequency ≥ 3** in raw stages and present in **at least 2 distinct stages** (filters one-off line decoration).
   - Apply the same blocklist used by the inferred-edge indexer (`博士`, `罗德岛`, role nouns, etc.).
   - Drop tokens already in the operator-set candidates (no double-counting).
   - Cap at the **top-20 NPC candidates per event** by frequency × stage-spread before further processing.
   - This is a recall-oriented heuristic: it will produce false candidates (zh place/role nouns the blocklist doesn't cover). False positives are surfaced to the LLM for verdicts in step 4, never auto-attributed to the wiki as omissions. Documented as v1 — the LLM verdict is the precision floor.
3. Extract the same union (operators + NPC-shaped) from the wiki summary.
4. Diff: entities present in raw but absent from summary → **omission candidates**.
5. **Apply the candidate budget** (see "Budget policy" below) before any LLM call.

### Signal 2 — Claim-level coverage check (LLM-driven; for failure classes (b) and (c))

The summary file has structured tags (`<核心剧情>`, `<剧情高光>` for char wikis). Each numbered/bulleted clause inside those tags is a **claim** — a discrete assertion about who did what, where, when. The audit:

1. Parses claims out of the summary tag-by-tag (deterministic; no LLM). For events with no claim-shaped tag, falls back to sentence segmentation on `<核心剧情>`.
2. For each claim, identifies the **relevant raw stages**: the union of (a) deterministic stages for any operator named in the claim and (b) the top-K stages whose text most overlaps the claim's named entities (literal-match scoring). Cap at 3 stages per claim to bound context.
3. Asks the LLM (Chinese prompt P3 / P4) for a per-claim verdict: `有依据 / 无依据 / 不确定`, with required citation. This is what catches wrong attributions and hallucinations — the LLM is reading both the claim and the raw scene and judging support.

### Budget policy (per Codex review 05 finding 4)

Audit cost previously had no ceiling. The Phase 6 implementation enforces:

- **Max claims per event audit:** 30 (rare events with more get a `--audit-all` opt-in).
- **Max omission candidates per event:** 15, ranked by (a) hit count in raw text and (b) stage spread (entities appearing in many stages rank higher than one-off mentions).
- **Stage-context cap per LLM call:** 3 stages, truncated at 8K chars total (≈ one cheap call).
- **Per-event token soft budget:** ~150K input tokens. The script estimates before each LLM batch and stops + reports if the budget would be exceeded.
- **Same-stage candidate collapse:** if multiple omission candidates resolve to the same stage, they're checked in one prompt rather than N.

These are constants in `scripts/kb_audit_wiki.py`, not flags, because the design wants spend to be predictable; agents who need different cuts pass `--audit-all` (off-budget) or `--max-claims N`.

### Orchestration

`scripts/kb_audit_wiki.py` runs both signals, applies the budget, then merges results into a single markdown report with sections "Possible omissions" (signal 1) and "Per-claim verdicts" (signal 2). The LLM calls are offloaded — the agent's context is preserved.

## Application: audit existing wiki page

Same two-signal mechanic as above, applied to any existing summary (no "freshness" filter). Same script, same budget caps, no flag changes.

For *character* wikis, the equivalent flow uses signal 2 as primary (claim-level), since the structured tag set on a char wiki makes the claim parse cleaner than for free-form story summaries:

1. Parse `<剧情高光>` claims (and other tagged claim sections) from `arknights_lore_wiki/data/char_v3/<id>.txt`.
2. For each claim, gather candidate stages: deterministic edges from `storysets.json` first, then inferred grep hits (Class A canonical-name matches preferred over fuzzy). Same 3-stage / 8K-char cap as the story flow.
3. Ask the LLM (prompt P4) per claim: `有依据 / 无依据 / 不确定` + required citation.
4. Output flagged claims with the supporting / unsupporting raw quotes, tagged by edge confidence (deterministic / inferred). Signal 1 (entity-coverage diff) runs as well but is secondary for char wikis — they're inherently selective rather than meant to cover all entities.

## Progressive disclosure for agents

When an agent lands at `arknights_lore_wiki_lib/`:

- **Top of `CLAUDE.md`** (the existing one) gets a new section **"For knowledge-base / Q/A / audit work"** that points to `docs/AGENTS_GUIDE.md`. Existing pipeline guidance stays intact.
- **`docs/AGENTS_GUIDE.md`** explains the layers, the CLI surface, and a few worked examples. This is the agent's main entry point for KB work.
- **`docs/PROMPTS.md`** stores all Chinese system/user prompts used by `summarize.py` and `kb_audit_wiki.py`, so prompts are reviewable in isolation and reusable.
- **`docs/REQUIREMENTS.md`** + **`docs/DESIGN.md`** + **`docs/DECISIONS.md`** are for posterity / review. Agents read them only when they need to understand *why* something is built the way it is.
- **`kb_summaries/AGENTS.md`** lives at the root of the summaries folder — a small file telling an agent how to read summaries and what they cover.

So the disclosure ladder is:
```
CLAUDE.md → "for KB work see docs/AGENTS_GUIDE.md"
AGENTS_GUIDE.md → CLI surface + workflow recipes
                → links into PROMPTS.md when agent needs a prompt
                → links into kb_summaries/AGENTS.md when agent needs the summary layer
DESIGN.md / REQUIREMENTS.md / DECISIONS.md → only on review or "why"
```

## Chinese prompts

A representative prompt (from `docs/PROMPTS.md`, paraphrased):

```text
[event summary, kb_summaries/events/]
你是一个明日方舟剧情资料编写助手。以下是某次活动的全部剧情原文（按章节组织）。请输出：
1. <一句话概要>：不超过40字，概括活动主题。
2. <核心剧情>：300字左右的剧情梗概，按时间顺序，不引申、不评价、不揣测原作未交代的内容。
3. <关键人物>：用分号分隔的人物名单（仅限在剧情中实质出场或被关键提及的角色，不收录"博士"、"罗德岛"等非角色实体）。
4. <场景标签>：3-6个zh短语，覆盖主要场景/地点/事件类型。

【硬性要求】
- 严格使用简体中文。
- 不要在输出之外添加解释或对话。
- 如果某一项无法从原文中得出，写"无"，不要编造。

剧情原文：
<<<EVENT_TEXT>>>
```

Audit prompts follow a similar structure but feed both raw and summary, and ask for explicit citation of raw lines that support / contradict each claim. Full text in `docs/PROMPTS.md`.

## Testing strategy

`tests/fixtures/mini_gamedata/` — a synthetic 2-event, 5-stage, 3-char snapshot we hand-build. ~50 KB. Sufficient to exercise:

- `chunker.py`: filename slug logic, frontmatter ordering, `clean_script` integration, sectional char file emission (skip empty sections, write only populated ones), `storysets.json` link resolution against story-review index, edge cases (empty stage, missing avgTag, non-ASCII char names, char with no handbook).
- `indexer.py`: deterministic edge construction from storysets, inferred edge construction via grep, deterministic-subtraction rule (no duplicate edges), alias resolution when alias file present + degraded path when absent, blocklist behavior, source-family classification (incl. `other` fallback).
- `query.py`: every public function. Round-trip (`get_event` → `event_chars` → `get_char_section`). Source-tag filtering on appearances. Family filtering on event lists.
- `paths.py`: path computation, `source_family` classifier (covers all observed prefixes + an unknown that returns `other`), no I/O.
- `llm_clients.py`: monkeypatch `subprocess.run` and `genai.Client.models.generate_content`. Verify dispatch, retry, error surfacing — no real LLM. Also verify Claude CLI absence raises clearly.

Targeting `pytest`. `requirements.txt` gains `pytest` only. Keeps the runtime closure tiny.

For the **build** pipeline, an integration test runs `kb_build.py` against `mini_gamedata/` and asserts the resulting `data/kb/` matches a checked-in expected manifest (excluding timestamps).

For `kb_summarize.py`, only a unit-level test of dispatch logic. Real LLM runs are manual.

## Decision log integration

Per project convention (`~/Claude/CLAUDE.md` Decision Log rules), substantive decisions during this design conversation are appended to `docs/DECISIONS.md`. That file already gets seeded by this design session.

## Implementation phases (proposed)

| Phase | Output | Approx effort | Has LLM? |
|---|---|---|---|
| 0 | This DESIGN.md (review-ready) | done | no |
| 1 | `libs/kb/paths.py`, `chunker.py` + tests | small | no |
| 2 | `libs/kb/indexer.py`, `query.py` + tests | medium | no |
| 3 | `scripts/kb_build.py`, `kb_query.py` end-to-end | small | no |
| 4 | `libs/llm_clients.py` (incl. Claude CLI) + tests | small | no (mock only) |
| 5 | `libs/kb/summarize.py`, `scripts/kb_summarize.py`, `docs/PROMPTS.md` | medium | yes |
| 6 | `scripts/kb_audit_wiki.py`, audit prompts | medium | yes |
| 7 | `docs/AGENTS_GUIDE.md` + `kb_summaries/AGENTS.md` + CLAUDE.md update | small | no |

Phases 1-3 produce a usable raw KB without a single LLM call; the remainder is layered on top.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| `clean_script` future changes invalidate cached chunks. | `manifest.json` records source `data_version` and a hash of `clean_script`'s source code; `kb_build` warns when stale. |
| Grep-based char→event mapping has false positives on common-noun aliases. | Blocklist + class-aware floors (no floor for canonical names; 2-char floor for curated/fuzzy aliases) + per-edge `match_class` so consumers can downweight `canonical_short` hits. **Deterministic edges from handbook storysets remain the high-confidence layer** (372 verified links); inferred edges are a recall floor, never the only signal. LLM-derived 关键人物 list (when baked) further validates. |
| Audit cost can balloon on long mainline events with many candidates. | Hard caps in `kb_audit_wiki.py`: max 30 claims/event, max 15 omission candidates/event, 3 stages × 8K chars per LLM call, ~150K-token per-event soft budget. Same-stage candidates collapse into one call. Off-budget audits require explicit `--audit-all`. |
| Audit by entity-set diff alone misses wrong attributions and hallucinations between known entities. | Two-signal audit: entity diff for omissions, **claim-level coverage check (LLM verdicts: 有依据 / 无依据 / 不确定)** for attributions and hallucinations. Both signals run by default. |
| Future game updates may add storysets that don't link cleanly. | `kb_build` re-runs M3-style verification at build time and refuses to write `storysets.json` entries with ambiguous or missing links — surfaces them as warnings the user must resolve. |
| LLM summarization is expensive across 461 events × multiple backends. | Hash-skip in `kb_summarize.py`; default to single backend (Gemini CLI) and small models. |
| `kb_summaries/` content is committed — copyright concern resurfaces. | Cap summaries at ~300 zh chars per event; no full quotations. Document this constraint in `PROMPTS.md`. |
| Claude CLI availability varies per machine. | `llm_clients.py` checks `claude` is on `$PATH` at backend instantiation; raises a clear error if missing, doesn't silently fall back. |
| New `kb_summaries/` folder bloats git history over many updates. | Use small files, prefer markdown. Set up `.gitattributes` `merge=ours` only if churn becomes a problem (defer). |
| Source-family classifier may miss future event types (e.g. a new storyTxt prefix). | `paths.source_family()` returns `other` for unknown prefixes (never crashes); `kb_build` logs the unknowns so they're visible. Adding a new family is a one-line code change with a unit test. |
| Lore-entity coverage gap: NPCs / titles / groups (e.g. `特蕾西娅`, `整合运动`) are not first-class entities in v1. | Documented as out-of-scope under "Entity model and v1 scope". Agents fall back to `grep_text` for these. v2 may add an `entities/` layer. **The resolver name `resolve_operator_name` makes this contract honest in code.** |
| Curated alias coverage: ~90% of curated aliases (315/348) cannot be recovered from raw game data alone. | Documented in "Aliases: where they come from". `manifest.aliases` content varies by mode (raw-only vs enriched); the agent guide tells callers the resolver may return `Missing` for civilian/title/fan names without the curated file. |
| Resolver returns `Ambiguous` for 9 known duplicate display names. | API surfaces `Resolved | Ambiguous | Missing` rather than collapsing. CLI prints the full candidate list. |

## Open questions for review

1. Is `kb_summaries/` the right name, or should it sit under `arknights_lore_wiki_lib/data_committed/` for clarity?
2. Should the per-stage chunk include the existing fix for `avgTag` in the chapter heading? (Yes, but it might already do so via `get_all_text_from_event` — verify in implementation phase.)
3. Should the audit script ever modify the published wiki, or only emit a report? Proposal: **report-only**, never auto-edit.
4. Is `pytest` acceptable as a new dependency, or should we stick to `unittest` to keep `requirements.txt` minimal?
5. Should `kb_summaries/manifest.json` be the source of truth for staleness, or should we re-derive hashes on each build? Proposal: manifest is the source of truth; rebuild trusts it unless `--verify-hashes` is passed.
6. ~~The pre-existing `.agents/skills/audit-lore-wiki/SKILL.md` references scripts that don't exist in `scripts/` today.~~ **Resolved 2026-05-08:** verified orphan (zero git history for `scripts/audit_*.py`, untracked skill file). User authorized demotion. Moved to `docs/proposals/audit-lore-wiki-prior-spec.md` with an "archived, not a skill" banner. Phase 6 `kb_audit_wiki.py` will adapt the 5-pass framework from this prior spec.

These are deferred to the user / reviewer rather than guessed.
