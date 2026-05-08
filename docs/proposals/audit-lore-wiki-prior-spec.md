> **Archived design proposal — NOT a live skill.**
> This was previously at `.agents/skills/audit-lore-wiki/SKILL.md` but its referenced scripts (`scripts/audit_*.py`, `scripts/audit_all`) were never committed. Moved here on 2026-05-08 to keep the 5-pass framework as design input for `scripts/kb_audit_wiki.py` (Phase 6 of `docs/DESIGN.md`). Do not invoke as a skill.

# Audit Arknights Lore Wiki (prior spec)

This skill runs the `scripts/audit_*.py` suite against the live wiki and the upstream `ArknightsGameData` dump, then turns the results into a punch list of concrete fixes.

Assumes the same three-repo layout as `update-lore-wiki`:

- `ArknightsGameData/` — upstream
- `arknights_lore_wiki_lib/` — cwd for all commands; uses `.venv/bin/python`
- `arknights_lore_wiki/` — wiki being audited

## When to run

- Before publishing a major release of the wiki
- After a batch update, to spot regressions or noisy `<关键人物>` extraction
- Periodically (quarterly), to catch alias drift and renamed game-data IDs
- Whenever a `libs/game_data.py` extractor bug is fixed — the audit's sticker-loss report tells you which existing summaries are most worth re-running

## What gets audited (5 passes)

| Pass | Asks | Output signal |
|---|---|---|
| `audit_tags` | does every wiki file parse against its required tag set? | structural failures (LLM truncations, empty refusals) |
| `audit_coverage` | upstream stories/chars with no wiki page, and vice versa | gaps to backfill or orphaned files to remove |
| `audit_xref` | alias-file hygiene; story `<关键人物>` resolve to a page; char `<相关活动>` map to real events | alias gaps, role-descriptor noise, hallucinated activity names |
| `audit_grounding` | does each `<关键人物>` (or alias / token) appear in the underlying script? | LLM-typo'd names, ungrounded role descriptions |
| `audit_sticker_loss` | how many `[Sticker(...text=…)]` lines did the OLD extractor drop per story? | priority list for selective re-runs after the 8dfb2f7 fix |

Each script is a standalone module; you can run any one in isolation. `audit_all` runs all five and prints a one-screen summary.

## Hard rules

- **Never auto-edit the wiki**. The audit is read-only. Fixes (alias additions, re-runs, file deletions) are presented for the user's approval, not applied silently.
- **Run each subaudit independently first** if `audit_all` shows a failure — the summary truncates detail.
- **Don't bulk re-run** stories based solely on the sticker-loss report. The re-run cost is real; threshold-pick (e.g. ≥30 lost lines) and confirm with the user.

## Workflow

### Step 1 — Sync upstream

```
cd ~/Claude/arknights/ArknightsGameData    && find . -name .DS_Store -delete && git pull --ff-only origin master
cd ~/Claude/arknights/arknights_lore_wiki  && find . -name .DS_Store -delete && git pull --ff-only origin update
cd ~/Claude/arknights/arknights_lore_wiki_lib
```

### Step 2 — Run the orchestrator

```
.venv/bin/python -m scripts.audit_all
```

This runs all five subaudits and prints headline numbers. Per-row TSVs land at `tmp/audit_grounding.tsv` and `tmp/audit_sticker.tsv`.

If you need every line each subaudit prints:

```
.venv/bin/python -m scripts.audit_all --full
```

### Step 3 — Triage by failure type

Walk through each non-OK subaudit in this order:

#### 3a. `tags` — structural failures

Run `.venv/bin/python -m scripts.audit_tags` for the full list. For each FAIL:

1. Read the file. If it's an LLM refusal ("我无法完成您的请求"), the original input was empty — usually a non-playable char with no archive entries that should never have been generated.
2. Decide with the user: delete the stub (`rm data/char_v3/<name>.txt`), or queue a regeneration with proper input. Do not silently keep a broken file.

#### 3b. `coverage` — gaps and orphans

- **STORIES upstream-but-no-wiki**: candidates for the next `update-lore-wiki` batch. Hand off the list there.
- **STORIES orphan-wiki**: a story_id that disappeared from upstream. Investigate before deleting (could be renamed). If renamed, update the file and any `<相关活动>` references; if removed, delete and rebuild any cross-references.
- **PLAYABLE CHARS upstream-but-no-wiki**: many are alters (`*_2`, `*2`, `*3` suffix) and trainee/token chars (`char_5xx`, `char_6xx`) that don't get separate pages. Filter those out before recommending backfills.

#### 3c. `xref` — alias hygiene + invented references

- **Duplicate canonical lines / canonical-as-alias**: open `data/char_alias.txt` and merge or remove. Confirm the canonical-of-record with the user.
- **Story `<关键人物>` not resolved to a page, top-frequency entries**: each one is either a missing alias mapping, a missing wiki page, or noise. The most actionable pattern is a name like `凯尔希医生` (3 stories) — clearly belongs as alias of `凯尔希`. Propose the alias-file edits as a single commit on the wiki repo's `update` branch.
- **Char `<相关活动>` not matching any activity_name**: usually LLM either invented an activity ("罗德岛日常") or wrote a paragraph instead of a name. Open the char wiki and either clean the entry by hand or queue a `--force-final` re-run of the char's step 3 in `get_char_wiki_v3`.

#### 3d. `grounding` — content hallucinations

```
.venv/bin/python -m scripts.audit_grounding --out tmp/audit_grounding.tsv
```

Look at the output's "Top missing names" list and the singletons sample.

Categorize each miss:

- **Role descriptor** (`医疗干员`, `村长`, `克劳迪娅的母亲`, `感染者村民`): the LLM included a role/relationship as a `<关键人物>`. Mostly noise. Worth fixing if a story has many such entries — surface to user as a prompt-quality concern, not a per-file fix.
- **True LLM typo** (`凯斯特公爵` for `开斯特公爵`, `Sargon医生` for `萨尔贡医生`): grep the script to confirm the correct name, then either edit the wiki file in place or queue a re-run.
- **Foreign-language form** (`大副ガルシア`, `拉恕爾`): the script uses a different romanization. If the LLM was right about the character, add an alias mapping; if it invented the form, fix it.
- **Genuine miss** (the wiki uses a name that's not in the script anywhere): probably hallucination — open the file with the user and decide.

For per-story drill-down, grep the TSV: `awk -F'\t' '$3=="MISS"' tmp/audit_grounding.tsv | grep <story_id>`.

#### 3e. `sticker` — re-run prioritization

```
.venv/bin/python -m scripts.audit_sticker_loss --threshold 30
```

`main_13` has by far the largest loss (~1900 lines under the old extractor). Anything ≥30 lost lines + `is_pre_fix=1` is a real candidate. Present the top-N to the user for re-run approval. Re-runs go through the normal `update-lore-wiki` flow (write to `tmp/stories_<DATE>.txt`, run `get_story_wiki.py --force` for each, validate, commit). **Don't queue all 49 candidates at once** — the user pays the LLM bill.

### Step 4 — Produce the punch list

Summarize for the user as four sections:

1. **Broken files** (from `tags`) — must delete or fix.
2. **Alias gaps** (from `xref` top-unresolved) — propose lines to add to `char_alias.txt`. Format: `canonical;new_alias_1;new_alias_2`. Canonical name first, always.
3. **Hallucinations / typos** (from `grounding`) — propose per-file edits.
4. **Re-run candidates** (from `sticker`) — propose a `tmp/stories_<DATE>.txt` to feed back into the update flow.

Each section gets concrete, copy-pasteable actions. Do not just dump the audit's stdout — synthesize.

### Step 5 — Apply fixes (with user approval)

- Alias edits: one commit on the wiki repo's `update` branch, `update char_alias.txt: <reason>`.
- Per-file wiki edits: one commit per logical change (e.g., "fix 凯斯特公爵 typo in act22side").
- Re-runs: hand off to `update-lore-wiki` skill for the existing flow.

## Recovery / partial state

- `audit_all` swallows individual subaudit exceptions and prints exit codes at the bottom. If one subaudit hangs (e.g., huge corpus + slow I/O), run it directly with `--limit` (grounding only) for an early sanity check.
- The grounding audit is the slowest pass (loads all event scripts) — ~30s on the current corpus. If it dominates, run the cheap suite first: `audit_tags && audit_coverage && audit_xref`.

## Output artifacts

- `tmp/audit_grounding.tsv` — `story_id, key_name, status (OK|MISS), canonical, hits`. Useful for `awk`/`grep` per-story drill-down.
- `tmp/audit_sticker.tsv` — `story_id, occurrences, lines, wiki_time, is_pre_fix`. Sort by `lines` desc to prioritize.

Do not check these into git; they are debugging artifacts for the audit session.
