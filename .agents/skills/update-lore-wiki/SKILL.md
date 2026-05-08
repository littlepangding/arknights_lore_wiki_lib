---
name: update-lore-wiki
description: Run the end-to-end Arknights lore wiki update flow when new game data lands. TRIGGER when the user says "update the lore wiki", "process the new game update", "run the wiki update", or refers to a new story/character batch in arknights_lore_wiki / arknights_lore_wiki_lib / ArknightsGameData. Walks through pulling, story summarization, character discovery (with mandatory human gates), per-character generation, compilation, and PR.
---

# Update Arknights Lore Wiki

This skill orchestrates a full update of the lore wiki when ArknightsGameData publishes new content. It assumes three sibling repos under `~/Claude/arknights/`:

- `ArknightsGameData/` — upstream game data dump (read-only)
- `arknights_lore_wiki_lib/` — this repo, the processing lib (cwd for all commands)
- `arknights_lore_wiki/` — the published wiki (commit & PR target)

All Python commands assume cwd is `arknights_lore_wiki_lib/` and use `.venv/bin/python` (run `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` once on a fresh checkout).

## Hard rules

- **Never call the LLM without a confirmation gate.** Every batch (stories, then chars) MUST be confirmed by the user before any LLM call. The LLM bill is real and the 关键人物 extraction is noisy.
- **Confirm BATCH_DATE up front** (e.g. `260428` for 2026-04-28). Used as the suffix for `tmp/stories_<BATCH_DATE>.txt` and `tmp/char_<BATCH_DATE>.txt`. If a previous batch with the same suffix exists, ask before overwriting.
- **One commit per logical step in the wiki repo**, on a branch off `update`.
- **If any LLM output is malformed (missing required tags), surface it. Don't silently skip.** `query_llm_validated` retries once; persistent failures need user attention.

## Workflow (7 steps)

### Step 1 — Sync all three repos

```
cd ~/Claude/arknights/ArknightsGameData     && find . -name .DS_Store -delete && git pull --ff-only origin master
cd ~/Claude/arknights/arknights_lore_wiki   && find . -name .DS_Store -delete && git pull --ff-only origin update
cd ~/Claude/arknights/arknights_lore_wiki_lib && git status
```

If `arknights_lore_wiki_lib` has uncommitted changes from a previous run, ask the user before discarding. If the wiki repo isn't on `update`, switch to `update` and confirm with the user before any new commits.

### Step 2 — Find new stories

```
cd ~/Claude/arknights/arknights_lore_wiki_lib
.venv/bin/python -m scripts.find_new_stories
```

Outputs a Python list of story_ids that exist in `ArknightsGameData/zh_CN/gamedata/excel/story_review_table.json` but have no `data/stories/<id>.txt` in the wiki repo.

**GATE 1 — confirm story list with user.** Present the full list, group MAINLINE / ACTIVITY / MINI_ACTIVITY (use `extract_data_from_story_review_table` to look up `entryType`), and ask which to include in *this* batch. Some stories are tutorial / reused / not worth wiki-ing. Write the approved list to `tmp/stories_<BATCH_DATE>.txt`, one per line.

### Step 3 — Generate story summaries (LLM)

```
.venv/bin/bash tmp/run_story.sh tmp/stories_<BATCH_DATE>.txt
```

This runs `get_story_wiki.py` once per story id. The script skips ids already in `data/stories/` unless `--force` is passed. Default backend is `cli` (gemini-3.1-flash) per `keys.json`.

**Validate every output before proceeding:**

```
.venv/bin/python -c "
import os
from libs.bases import validate_and_rebuild, story_wiki_tags, get_value
data = os.path.join(get_value('lore_wiki_path'), 'data', 'stories')
import sys
with open('tmp/stories_<BATCH_DATE>.txt') as f:
    ids = [l.strip() for l in f if l.strip()]
fails = []
for i in ids:
    p = os.path.join(data, i + '.txt')
    try:
        validate_and_rebuild(open(p).read(), story_wiki_tags)
    except Exception as e:
        fails.append((i, str(e)))
if fails:
    for i, e in fails:
        print('PARSE FAIL:', i, e)
    sys.exit(1)
print('all', len(ids), 'stories validated')
"
```

For each fail: re-run `get_story_wiki.py <id> --force` (one auto-retry happens internally). If still failing after a manual retry, surface to the user — likely needs prompt tweaking or the LLM truly can't extract a tag.

### Step 4 — Discover characters from new stories (step 2.5 bridge)

```
.venv/bin/python -m scripts.find_chars_in_new_stories \
    --new-stories tmp/stories_<BATCH_DATE>.txt \
    --out tmp/char_<BATCH_DATE>.txt
```

Prints three sections:
- **EXISTING** — chars whose page exists; need a re-run to absorb the new event.
- **NEW char candidates** — names extracted from `<关键人物>` that don't resolve to a known canonical with a page.
- **POTENTIAL ALIASES** — names similar to a known canonical; flag for manual review of `char_alias.txt`.

**GATE 2 — confirm candidate char list with user. This is non-negotiable.** The LLM-extracted 关键人物 includes background mentions, walk-on roles, and occasionally hallucinated names. The user must prune the file. After their edits:

1. Show the diff of `tmp/char_<BATCH_DATE>.txt` before/after their edits and confirm the final list.
2. If POTENTIAL ALIASES were flagged, ask the user whether to add lines to `arknights_lore_wiki/data/char_alias.txt`. Format: `canonical;alias1;alias2`. **Always put the canonical name first.** Commit alias updates as a separate commit in the wiki repo.

### Step 5 — Per-character generation (LLM)

```
.venv/bin/bash tmp/run_char.sh tmp/char_<BATCH_DATE>.txt
```

Each invocation of `get_char_wiki_v3.py` runs three LLM steps. Step 2 (per-event summarization) is checkpointed to `tmp/char_v3_cache/<file_name>/<event_id>.txt` after each event — a crash mid-batch loses at most one event's work. Resume is automatic on re-run.

**Cost note:** existing chars with N events already cached + 1 new event will only call the LLM for the 1 new event in step 2 + step 3 (synthesis). Don't pass `--force` unless the user explicitly asks — it nukes the cache.

**Validate outputs:**

```
.venv/bin/python -c "
import os
from libs.bases import validate_and_rebuild, char_wiki_tags, get_value
data = os.path.join(get_value('lore_wiki_path'), 'data', 'char_v3')
import sys, glob
fails = []
# only validate files modified during this batch
for p in glob.glob(os.path.join(data, '*.txt')):
    if os.path.basename(p).startswith(('prompt_', 'depre')):
        continue
    try:
        validate_and_rebuild(open(p).read(), char_wiki_tags)
    except Exception as e:
        fails.append((os.path.basename(p), str(e)))
if fails:
    for n, e in fails: print('PARSE FAIL:', n, e)
    sys.exit(1)
print('all char files validated')
"
```

Same recovery pattern as step 3. For a persistent failure, the user can manually edit the file in `data/char_v3/` to fix the missing tag — that file is the source of truth, the markdown page is regenerated.

### Step 6 — Compile the website

```
.venv/bin/python -m scripts.compile_website \
    --new-stories-file tmp/stories_<BATCH_DATE>.txt \
    --new-chars-file   tmp/char_<BATCH_DATE>.txt
```

Regenerates every `docs/<sub>/*.md`, the indexes, and prints a copy-pasteable README "what's new" snippet. Skips broken pages with a `WARN` line — re-check those files before moving on.

### Step 7 — Commit and PR

```
cd ~/Claude/arknights/arknights_lore_wiki
git status
git add data/stories/  data/char_v3/  data/char_alias.txt  docs/
# update README.md "what's new" section with the snippet from step 6
$EDITOR README.md
git add README.md
git commit -m "<BATCH_DATE> update: <N stories>, <M chars>"
git push origin update
gh pr create --base main --head update --title "<date> update" --body "..."
```

**Confirm with the user before pushing.** They may want to review locally first.

## Recovery / partial state

- **Crash mid step 3 (story batch):** re-run the same `tmp/run_story.sh` command — completed stories skip themselves.
- **Crash mid step 5 (char batch):** re-run the same `tmp/run_char.sh` command — completed chars skip via `final_results_path` check; in-progress chars resume from `tmp/char_v3_cache/<id>/`.
- **Wrong char in `tmp/char_<BATCH_DATE>.txt`:** delete `data/char_v3/<file_name>.txt` and `tmp/char_v3_cache/<file_name>/`, then re-run.
- **Stale prompt cache:** `--force-final` regenerates the final wiki from the existing `prompt_<id>.txt` without re-running step 2. Use this when the user only wants step 3 retried (e.g., to fix a synthesis-level hallucination without re-running event summaries).

## Backend / model

`keys.json` carries `llm_backend` (`"cli"` or `"gai"`) and `llm_model` (default `gemini-3.1-flash` for cli). Override per-script with `--llm cli|gai` and `--model <id>`. The CLI backend shells out to the `gemini` command; the gai backend uses the `google-genai` SDK with `genai_api_key`.

If the user reports rate limits or large bills: prefer `cli` with `gemini-3.1-flash`; fall back to `gai` only if the CLI is unavailable.
