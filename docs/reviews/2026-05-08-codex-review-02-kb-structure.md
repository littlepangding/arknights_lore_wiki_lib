# Codex Review 02 — KB Hierarchy, Depth, and Cross References

Date: 2026-05-08

Focus:
- hierarchy depth
- index depth / context efficiency
- cross-reference quality
- whether the design is specific to Arknights data rather than generic

Evidence used:
- `docs/DESIGN.md`
- `docs/AGENTS_GUIDE.md`
- `libs/game_data.py`
- direct measurements from current `ArknightsGameData`

## Corpus facts that matter for structure

Measured from the current corpus:

- `story_review_table` contains **461 events** and **1937 stages**
- stage text lengths are:
  - median `5405`
  - p95 `9855`
  - max `20910`
- stages per event are:
  - median `1`
  - p90 `19`
  - max `41`
- `entryType` counts are:
  - `NONE`: `372`
  - `ACTIVITY`: `51`
  - `MINI_ACTIVITY`: `20`
  - `MAINLINE`: `18`
- all `entryType=NONE` events currently map to `storyTxt` prefix `obt/memory`
- `get_all_char_info()` currently exposes **372 handbook storysets** that map **cleanly and deterministically** to story-review entries via `storyTxt`, with `0` missing and `0` ambiguous links
- character text lengths are:
  - median `5477`
  - p95 `9016`
  - max `11460`

These numbers strongly suggest the structure should be tuned around:

1. activity/mainline narrative events
2. operator-record / memory events
3. per-character dossier sections
4. deterministic storyset links, not just name grep

## Findings

### 1. Blocking: the top-level event hierarchy is too generic for Arknights, because `entryType` collapses 372 memory stories into one useless `NONE` bucket

The design's main event index is `events_by_type.json`, grouped by `MAINLINE / ACTIVITY / MINI_ACTIVITY / NONE`. See `docs/DESIGN.md:48-54` and `:176-188`.

For this dataset, that is not a good primary hierarchy:

- `NONE` is not a small edge case; it is **372 / 461 events**
- those `NONE` events are not semantically random; they are all `obt/memory` operator-record style content

So the current plan loses a very important Arknights-specific distinction. A better first-class grouping would be something like:

- `mainline`
- `activity`
- `mini_activity`
- `operator_record` or `memory`

Derivation can be deterministic from `storyTxt` prefix and existing `event_id` patterns. This would give agents a navigation tree that matches the actual corpus instead of forcing them through a bloated `NONE` catch-all.

### 2. Important: the proposed CLI and index surface does not give agents a good way to browse the majority of the corpus

`docs/AGENTS_GUIDE.md` proposes:

- `kb_query event list [--type ACTIVITY|MAINLINE|MINI_ACTIVITY]`

See `docs/AGENTS_GUIDE.md:25-35`.

This omits `NONE`, which is the majority bucket. In practice that means the browsing UX is optimized for the minority of events and underserves the operator-record corpus, which is especially relevant for character lore Q/A.

If the KB is meant for Arknights lore questions, operator records should not feel like second-class citizens. The surface should let an agent browse them directly, ideally through an explicit content family rather than the label `NONE`.

### 3. Important: the character hierarchy is too flat; one monolithic `<char_id>.txt` blob is not the right depth for this corpus

The design proposes one character text blob containing profile, voice, archives, skins, and modules. See `docs/DESIGN.md:156-159`.

That is workable in raw size terms, but not ideal in retrieval terms. Arknights character data is naturally sectional:

- 招聘文本
- 语音
- 档案
- 悖论/干员密录关联剧情
- 皮肤
- 模组

And the current parser already knows some of this structure. `extract_data_from_handbook_info_table()` pulls both:

- archive text under `stories`
- story links under `storysets`

See `libs/game_data.py:118-146`.

But `get_char_info_text_prompt()` drops `storysets` entirely and flattens the rest into one text block. See `libs/game_data.py:314-348`.

For context efficiency and better cross-reference, the KB should probably store:

- `chars/<char_id>/manifest.json`
- `chars/<char_id>/profile.txt`
- `chars/<char_id>/voice.txt`
- `chars/<char_id>/archive.txt`
- `chars/<char_id>/skins.txt`
- `chars/<char_id>/modules.txt`
- `chars/<char_id>/storysets.json`

Then an agent can read only the relevant section instead of always dragging voice lines and cosmetic text into a lore prompt.

### 4. Important: the cross-reference design leans too heavily on grep even though Arknights already gives deterministic links for a large and important subset

The design treats `char_to_events.json` and `event_to_chars.json` as grep-derived mappings over names and aliases. See `docs/DESIGN.md:179-186`.

That is a reasonable fallback, but it should not be the first or only strategy, because the data already provides stronger structure:

- 372 handbook storysets map exactly to story-review events through `storyTxt`
- this is a deterministic char <-> story link from game data, not an inferred one

That means the KB should distinguish at least two relation types:

- `deterministic_links`
  - operator record / memory storysets from handbook data
- `inferred_links`
  - name-mention grep across mainline and activity scripts

Without this distinction, the KB risks mixing high-confidence and low-confidence edges together and making it harder for agents to know what to trust.

### 5. Medium: stage-level chunking is a good base unit, but the design is missing an intermediate navigation layer above it

The measured stage sizes are actually good news:

- median `5405`
- p95 `9855`
- max `20910`

So `event -> stage` is a reasonable raw-content depth for activities and mainline chapters. The issue is not the stage chunk size; it is the navigation above it.

Right now the design gives:

- `events/<event_id>/stage_<NN>...txt`
- flat `stage_table.json`
- flat `events_by_type.json`

See `docs/DESIGN.md:39-54` and `:187-188`.

What seems missing is an intermediate, Arknights-shaped browse layer such as:

- family index: `mainline`, `activity`, `mini_activity`, `operator_record`
- within family:
  - ordered event list
  - event manifest
  - stage manifest

This matters because a flat stage table and a flat event list are both cheap for code, but not very pleasant for an agent trying to progressively disclose only the relevant part of the corpus.

### 6. Medium: the design underuses story-path metadata that would make cross references and hierarchy much cleaner

The first `storyTxt` prefix already separates the corpus into meaningful source families:

- `activities/*` for activity and mini-activity content
- `obt/main/*` for mainline
- `obt/memory/*` for operator-record style memory content

The draft design stores `event_id`, `entryType`, stage name, and avgTag, but it does not elevate source-path family to a first-class index key. See `docs/DESIGN.md:142-154` and `:176-188`.

For Arknights specifically, this field is valuable and should probably be preserved in:

- `event.json`
- `stage_table.json`
- browse commands / filters

It is a better structural clue than `NONE`.

## What looks good

- Stage-level chunking is the right raw narrative unit for events.
- The measured stage sizes are reasonable for LLM use.
- Keeping raw chunks and committed summary aids separate still makes sense.

## Suggested structural revision

If I were tightening the design before implementation, I would reshape it roughly like this:

1. Top-level content families:
   - `mainline`
   - `activity`
   - `mini_activity`
   - `operator_record`
2. Event hierarchy:
   - keep `event -> stage`
   - preserve `storyTxt` family/path metadata
3. Character hierarchy:
   - replace one `char.txt` blob with section files plus a manifest
   - make `storysets.json` first-class
4. Cross references:
   - deterministic edges first
   - grep-inferred edges second
   - keep confidence / provenance on each edge
5. Query surface:
   - add family-aware listing and filtering
   - add section-aware character retrieval
   - add deterministic `char_storysets` lookup

## Review status

The current draft is directionally good on chunk size, but still too generic in hierarchy and too grep-heavy in cross-reference strategy. The main improvement I’d want before implementation is for the KB shape to reflect the actual Arknights corpus:

- operator records are a first-class content family
- character data is sectional, not monolithic
- storyset links are deterministic and should be treated that way
