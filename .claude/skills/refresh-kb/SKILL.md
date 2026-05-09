---
name: refresh-kb
description: Refresh the arknights knowledge base — `data/kb/` raw chunks (deterministic) and `kb_summaries/` LLM-baked event summaries (token-cost). Use this for both first-time full builds and later incremental updates after `ArknightsGameData` pulls; the same flow handles both because `kb_build` is idempotent and `kb_summarize` is hash-skip cached. TRIGGER on "refresh the KB", "rebuild kb", "bake the corpus", "kb summaries for everything", "incremental kb update", or after the user updates the upstream game data. SKIP for single-event runs (just call `kb_summarize --event <id>` directly), wiki regeneration (use `update-lore-wiki`), or pure read-side queries (use `kb_query`).
---

# Refresh the arknights knowledge base

End-to-end orchestration of `scripts.kb_build` (deterministic, no LLM) followed by `scripts.kb_summarize` (LLM, opt-in per-event). One skill so the same flow covers a fresh checkout's first-time build *and* a later incremental refresh after the upstream game data updates.

## Preconditions

Always check, in this order:

1. **Working directory.** Must be `arknights_lore_wiki_lib/` (the lib repo root). Verify by `[ -f scripts/kb_build.py ] && [ -d libs/kb ]`. The lib's scripts read `keys.json` via a relative path; running from elsewhere will fail.
2. **`keys.json` exists** with at least `game_data_path` set. Read with `bases.try_get_value`. If absent, ask the user to populate it before continuing — don't try to invent paths.
3. **`.venv/` exists.** If not: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`. Always invoke the venv: `.venv/bin/python -m scripts.<name>`. System python on macOS hits PEP 668.

## Workflow

### Step 1 — Deterministic build

```bash
.venv/bin/python -m scripts.kb_build
```

Always run this first, even on incremental updates. It's idempotent, ~8s on the live corpus, and writes:
- `data/kb/events/<id>/event.json` + `stage_*.txt` (per-stage chunks)
- `data/kb/chars/<id>/{manifest,storysets,profile,voice,archive,skins,modules}` (per-char sectional)
- `data/kb/indexes/*.json` (events_by_family, deterministic + inferred edges, stage_table, char_table, ambiguous_aliases, optional char_alias)
- `data/kb/manifest.json` (snapshots upstream `data_version` + a 12-char SHA of `clean_script` for cache-staleness detection)

Default-on prune drops any `events/<id>/` or `chars/<id>/` directory that is no longer in the upstream snapshot. Pass `--no-prune` only if explicitly asked.

Capture the printed report; it tells you the new event count per family.

### Step 2 — Decide LLM scope

Compare the freshly built KB against any existing `kb_summaries/manifest.json`:

```bash
# Quick delta calc — events present in the build but not in the prior summary manifest
.venv/bin/python -c "
import json, pathlib
build_events = sorted(p.name for p in pathlib.Path('data/kb/events').iterdir() if p.is_dir())
summarized = set()
mf = pathlib.Path('kb_summaries/manifest.json')
if mf.is_file():
    summarized = set(json.loads(mf.read_text())['events'].keys())
new = [e for e in build_events if e not in summarized]
gone = sorted(summarized - set(build_events))
print(f'events_total={len(build_events)} already_summarized={len(summarized)} new_to_run={len(new)} stale_to_prune={len(gone)}')
print('new (first 10):', new[:10])
print('gone:', gone)
"
```

This is the **scope preview** the user wants to see before any LLM cost is incurred:
- **First-time run** → `already_summarized=0`, `new_to_run` = all events (~461). Wall time on Gemini CLI: roughly 1–2 hours single-pass + a multi-pass tail of ~70–90 events.
- **Incremental run** → typically a small `new_to_run`; `stale_to_prune` is event_ids the upstream removed. Cost scales linearly with `new_to_run` only (changed-content detection is via per-event source hash inside `kb_summarize`, so renames/changes are caught for free on the next step).

### Step 3 — Confirm scope with the user

Always confirm before kicking off the LLM step. Quote the numbers from Step 2 and the backend that will be used (read `keys.json llm_backend`, default `cli`). Offer:

- **Run all** — proceed with the full set.
- **Run a single family** — pass-through filter for budget control. There's no native `--family` flag yet; emulate by extracting the family's event list from `data/kb/indexes/events_by_family.json` and passing each via `--event` (repeatable). Useful for staging a big corpus run by family.
- **Skip the LLM step entirely** — just stop after Step 1; report what would have run.
- **Switch backend** — pass `--llm gai` or `--llm claude` if the user prefers. (Note: only `--llm cli` has been smoke-tested end-to-end as of 2026-05-08.)

### Step 4 — Run `kb_summarize`

For the full corpus:

```bash
.venv/bin/python -u -m scripts.kb_summarize
```

For a filtered subset:

```bash
.venv/bin/python -u -m scripts.kb_summarize --event <id1> --event <id2> ...
```

Use `python -u` (unbuffered) and `tee /tmp/kb_summarize.log` if running in the background, so per-event progress is visible. The tool is hash-skip cached — re-running is free for unchanged events. Per-event errors don't abort the batch; they land in the final report.

If the user said "skip LLM step", end here.

### Step 5 — Report

After `kb_summarize` returns, summarize what changed:
- `wrote: N` (new or re-run summaries)
- `skipped (unchanged): N` (hash-cache hits)
- `errors: N` — list each `(event_id, message)`. Common causes: model returned malformed tags twice (validate-and-rebuild raised), missing stage file (kb_build prune mismatch — re-run kb_build), or transient network failures (re-run; hash-cache resumes).
- `pruned: N` — orphan `kb_summaries/events/<id>.md` files dropped because the upstream removed the event.

If the user wants the published wiki regenerated against the freshly-summarized KB, that's the **`update-lore-wiki` skill**, not this one. Mention it but don't auto-trigger.

## Useful invariants

- **`kb_build` runs in seconds with zero LLM cost.** Always cheap to re-run — use it freely.
- **`kb_summarize`'s hash gate uses `(filename, text)` over sorted stage files.** If you suspect cache poisoning, delete `kb_summaries/manifest.json` and re-run; the source-hash comparison will rebuild from scratch.
- **Aborting mid-summarize is safe.** The next run resumes; nothing partial lands at the destination because writes are atomic (`os.replace`).
- **`data/kb/` is gitignored** (raw chunks, regenerable). **`kb_summaries/` is in git** (committed navigation aid).

## Common mistakes to avoid

- Don't run `kb_summarize` without first running `kb_build` — the summarizer reads from `data/kb/events/`, which only exists after the build.
- Don't pass `--force` unless the user asked. `--force` re-bills every event.
- Don't auto-pick a different backend on errors. Surface the error and let the user decide.
- Don't commit `kb_summaries/manifest.json` if it has half-completed entries from an aborted run — it's safe to commit (atomic writes), but if the user says they'll re-run, let them decide.
- Don't try to update the published `arknights_lore_wiki/` repo as part of this skill. That's a separate flow.

## Tunables to know

- `MULTI_PASS_LENGTH_THRESHOLD = 80_000` chars or `MULTI_PASS_STAGE_THRESHOLD = 10` stages → switches to per-stage reduce + merge. ~70–90 events trip this on the live corpus. Defined in `libs/kb/summarize.py`.
- `RETRY_LIMIT = 5`, `RETRY_SLEEP_TIME = 60` (linear backoff: 60+120+180+240+300 = 15 min worst case per event). Defined in `libs/bases.py`.
- Default model: `gemini-3-flash-preview` for `--llm cli`, `gemini-2.5-flash` for `--llm gai`, `claude-haiku-4-5` for `--llm claude`. Override per-call with `--model`, or globally via `keys.json llm_model` / `gai_model` / `claude_model`.
