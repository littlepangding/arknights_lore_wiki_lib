# Codex Review 03 — Updated Design and Alias Readiness

Date: 2026-05-08

Scope reviewed:
- `docs/DESIGN.md` (revised)
- `docs/AGENTS_GUIDE.md` (revised)
- `docs/REQUIREMENTS.md` (revised)
- root `AGENTS.md` / `CLAUDE.md` routing update
- current alias-related code in `libs/game_data.py`, `scripts/get_char_wiki_v3.py`, `scripts/find_chars_in_new_stories.py`

This review focuses on one question:

> Are we actually equipped to handle aliases from the game data?

Short answer:

- **The updated docs are substantially better overall.**
- **For rich lore alias handling from raw game data alone: no, not yet.**
- **For canonical operator names plus optional curated alias enrichment: yes, partially.**

## Findings

### 1. Blocking: the revised design still overstates what "game-data-only" alias handling can do

The revised docs now say `char_alias.json` is optional enrichment rather than a build prerequisite. That is a real improvement. See:

- `docs/REQUIREMENTS.md:40`
- `docs/DESIGN.md:72-77`
- `docs/DESIGN.md:242`

But other parts of the design still read as if alias resolution is a generally available capability:

- `chars/<char_id>/manifest.json` includes `aliases` — `docs/DESIGN.md:63`, `:188-205`
- query API exposes `resolve_char(kb, name_or_alias)` — `docs/DESIGN.md:260-265`
- the agent guide starts Q/A with `kb_query char resolve 司辰` — `docs/AGENTS_GUIDE.md:54`, `:69-73`

That is stronger than the current data supports.

Measured against the current corpus:

- existing `char_alias.txt` has **267 canonical lines** and **348 alias entries**
- only **33 / 348** alias entries match any game-data `name`
- **0 / 348** alias entries match game-data `appellation`
- therefore **315 / 348** aliases are **not recoverable** from those structured fields

So in practice, a raw-game-data-only KB is not equipped for broad alias resolution. It can do:

- canonical display names
- deterministic storyset links
- maybe English codenames if the parser is extended to keep `appellation`

It cannot, from structured game data alone, reliably resolve the kinds of aliases already present in the curated wiki layer:

- civilian names
- titles / epithets
- honorifics
- alternate-body identities
- partial names
- joke / fandom labels

Examples from the existing alias file that are not present in structured game-data name/appellation fields:

- `玛嘉烈` → `临光`
- `劳伦缇娜` → `幽灵鲨`
- `切利尼娜` → `德克萨斯`
- `陈晖洁` → `陈`
- `AMa-10` → `凯尔希`
- `伊莎玛拉` → `斯卡蒂`

Recommendation:

- explicitly state in `DESIGN.md` and `AGENTS_GUIDE.md` that **without curated alias enrichment, `resolve_char` is effectively canonical-name resolution, not true alias resolution**
- rename the behavior more conservatively if needed (`resolve_canonical_char` or `resolve_operator_name`)

### 2. Blocking: the new KB model is still too `char_id`-centric to cover many lore entities that matter for alias handling

The revised design models characters under `chars/<char_id>/...`, and the query surface is built around `char_id`:

- `docs/DESIGN.md:61-69`
- `docs/DESIGN.md:251-277`
- `docs/AGENTS_GUIDE.md:13-14`
- `docs/AGENTS_GUIDE.md:55-60`

That fits operator-table-backed entities, but not the full lore entity space already represented in the existing alias workflow.

Measured against the current alias file:

- **93 / 267** canonical alias-file entries are **not present** in game-data `name` or `appellation`

Those are not fringe cases; they include many lore-important NPCs / titles / groups, e.g.:

- `爱国者`
- `科西切`
- `特蕾西娅`
- `鼠王`
- `霜星`
- `博士`
- `皇帝的利刃`

The current repo already has a concept for non-playable entities: `get_char_file_name()` falls back to `extended_char_<hash>` when a name is not in `char_name_info`. See `libs/game_data.py:351-356`.

The revised KB design drops that idea entirely. As written, it has no obvious home for:

- NPC-only entities
- title-only entities
- group entities
- canonical names that exist in the alias file but not in `character_table`

That means the updated doc still does **not** describe a KB that is equipped for lore-facing alias resolution across the whole corpus. It describes a strong operator KB plus optional alias enrichment for inferred grep.

Recommendation:

- either add a second identity layer (`entity_id`, with `operator_char_id` optional) for non-playables / NPCs / groups
- or state clearly that v1 alias handling only covers operator-table-backed characters, not general lore entities

### 3. Important: even the limited alias-like field the game data *does* have is currently discarded by the parser

`character_table` contains an `appellation` field, but `extract_data_from_character_table()` currently keeps only:

- `name`
- `itemUsage`
- `itemDesc`
- `nationId`

See `libs/game_data.py:68-75`.

So even if the updated design intends to use minimal game-data-native aliases, the current parser path is not equipped for it yet.

This is especially relevant because the revised design now writes `aliases` into `manifest.json` — `docs/DESIGN.md:188-205` — but there is no concrete extraction rule for those aliases in the raw-only mode.

Important nuance:

- `appellation` is **not** a full alias solution
- in the current corpus it matches **0** of the existing curated alias entries
- but it is still useful metadata and should not be thrown away

Recommendation:

- explicitly add `appellation` to the parsed character metadata
- document it as a **low-confidence / narrow-scope alias source** (mostly codename / English-form support), not as general alias coverage

### 4. Important: ambiguity behavior for `resolve_char` is under-specified

The revised query API still says:

- `resolve_char(kb, name_or_alias) -> CharMeta | None`

See `docs/DESIGN.md:262`.

But the game data already contains duplicate names. I measured **9 duplicate display names** in the current corpus, including:

- `暮落`
- `郁金香`
- multiple reserve-operator names like `预备干员-术师`

With optional alias enrichment absent, a single-return-value resolver is not enough to model:

- multiple candidate operators sharing the same visible name
- alias collisions
- canonical-name vs NPC-title collisions if a future entity layer is added

Recommendation:

- change the API contract to return either:
  - a list of candidates, or
  - `Resolved | Ambiguous | Missing`
- and document what ranking signal is used when more than one match exists

## What improved in the updated docs

- The root progressive-disclosure pointer is now present in `AGENTS.md` / `CLAUDE.md`.
- The corpus-family structure is much better and now matches actual Arknights data shape.
- Deterministic storyset links are correctly elevated above grep inference.
- The optional-enrichment framing for `char_alias.txt` is much healthier than the first draft.

## Verdict on alias readiness

If the question is:

**"Can this KB handle aliases using raw game data alone?"**

My answer is:

- **No, not in the broad lore sense.**

If the question is:

**"Can this KB build without the alias file and still function?"**

My answer is:

- **Yes, but mostly for canonical operator names and deterministic storyset links.**

If the question is:

**"Can this KB support strong alias handling once the curated alias file is present?"**

My answer is:

- **Partially, yes — for inferred lookup / grep expansion.**
- **But it still needs an explicit entity model if you want NPC / title / group aliases to be first-class rather than incidental.**

## Suggested next doc revision

1. Narrow the promise of `resolve_char` in raw-only mode.
2. Define where `manifest.json.aliases` comes from when no alias file exists.
3. Add `appellation` to the parser and document its limited value.
4. Decide whether v1 is:
   - operator-centric only, or
   - a broader lore-entity KB
5. If broader, add a non-`char_id` entity layer before implementation.
