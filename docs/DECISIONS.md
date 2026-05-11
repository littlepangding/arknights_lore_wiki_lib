# Decision Log

> Tracks substantial user instructions and project direction changes.
> Each entry summarizes the user's intent so future sessions have full context.

### 2026-05-08 — Build an agent-readable knowledge base

**User intent:** Stand up a parsed, searchable KB on top of `ArknightsGameData` so an LLM agent can do ad-hoc lore Q/A, audit new wiki updates, and audit existing wiki pages — all from `arknights_lore_wiki_lib/` as the working root. Code-and-tests should do the deterministic work; LLM access should be offloadable to Gemini CLI / Gemini API / Claude CLI rather than burning the agent's own context. Prompts should be written in Chinese to match the source. Document everything in `docs/` so reviewers (or future agents) can audit the design before any implementation.

**Outcome:** Created `docs/REQUIREMENTS.md` (verbatim user requirements) and `docs/DESIGN.md` (architecture for `libs/kb/`, `data/kb/` raw chunks gitignored, `kb_summaries/` LLM-derived navigation aids in git, retrieval via structured indexes + grep, no embeddings in v1, three-backend LLM dispatch). Implementation deferred pending review.

### 2026-05-08 — KB layout decisions (clarified before design)

**User intent:** Settle four load-bearing layout questions before drafting the design doc, so the design doesn't have to fork on speculation.

**Outcome:** Decisions:
- KB on disk: `arknights_lore_wiki_lib/data/kb/` (gitignored raw + index), `arknights_lore_wiki_lib/kb_summaries/` (in git: LLM-made summaries).
- KB scope: **raw game data only**. Existing wiki summaries (`arknights_lore_wiki/data/`) are an audit target, not a query layer.
- LLM backends: all three (Gemini CLI default, Gemini API, Claude CLI) — user explicitly added "gemini cli as default".
- Retrieval: structured indexes + grep, no embeddings in v1.

### 2026-05-08 — Design revisions after Codex reviews 01 & 02

**User intent:** Run two reviewer passes on the design before implementation. Both reviews landed at `docs/reviews/2026-05-08-codex-review-{01,02-kb-structure}.md`. The user said: *"update your design if you think the critics makes sense"*. Findings were evaluated and applied.

**Outcome:** The following structural changes to `docs/DESIGN.md` (and matching updates to `docs/AGENTS_GUIDE.md`, `docs/REQUIREMENTS.md`):
- **Source families as first-class navigation axis** (replaces `entryType` browse). Four families derived deterministically: `mainline`, `activity`, `mini_activity`, `operator_record` (was `entryType=NONE`, 372/461 events), plus residual `other`. `events_by_family.json` replaces `events_by_type.json`. CLI `--type` becomes `--family`. Empirically verified against current corpus.
- **Sectional character data** replaces the monolithic `<char_id>.txt` blob. Each char now lives at `chars/<char_id>/{profile,voice,archive,skins,modules}.txt` plus `manifest.json` and `storysets.json`. CLI gains `char get <id> --section <name>`.
- **Deterministic char↔event edges** are first-class. Built from handbook storysets via `storyTxt` lookup — empirically 372 / 372 unique linkage with zero ambiguity. Stored in `char_to_events_deterministic.json`. Grep-based edges remain as `char_to_events_inferred.json` (recall floor, never duplicates deterministic edges). Every CLI cross-reference command takes `--source deterministic|inferred|both`.
- **`source_family` and `storyTxt_prefix` are first-class metadata** on every event and stage row.
- **`char_alias.json` is now OPTIONAL enrichment**, not a build prerequisite. KB builds entirely from `ArknightsGameData` if the wiki repo's alias file is absent. (Resolves R01 Finding 3.)
- **Added `## Measurements` section to DESIGN.md** with reproducible commands for every size/count claim — supersedes inline assertions. (Resolves R01 Finding 4.)
- **Open question 6** in DESIGN.md surfaces the pre-existing `.agents/skills/audit-lore-wiki/SKILL.md` issue (R01 Finding 1) — that skill references nonexistent scripts; not introduced by this design but flagged for the user to decide. (Surfaces R01 Finding 1.)
- Root-level `CLAUDE.md` / `AGENTS.md` will get a small KB pointer block. (Resolves R01 Finding 2 — pending separate edit.)

### 2026-05-08 — Design revisions after Codex review 03 (alias readiness)

**User intent:** Third reviewer pass landed at `docs/reviews/2026-05-08-codex-review-03-updated-doc-alias.md`, focused on whether the revised KB design can actually handle aliases from raw game data alone. User asked for the same evaluate-and-apply treatment.

**Outcome:** Empirically verified review's claims (9 duplicate display names; 33/348 curated aliases match `name`; 3/348 match `appellation`; 93/265 curated canonicals not in `character_table` at all). Updated docs to be honest about scope:
- **`Entity model and v1 scope` section added to DESIGN.md.** v1 is operator-centric. NPCs / titles / groups (e.g. `特蕾西娅`, `整合运动`) are addressable only via `grep_text`, not via name resolution. v2 deferred — would add an `entities/` parallel layer keyed by `extended_<slug>` (matching existing `get_char_file_name` convention).
- **`Aliases: where they come from` section added.** Three-tier alias source table (name / appellation / curated file) with measured coverage. `manifest.aliases` content explicitly differs in raw-only vs enriched modes.
- **`extract_data_from_character_table` planned extension to keep `appellation`** (currently dropped). Adds English codename support (Lancet-2, Amiya, etc.). Documented as narrow-scope, not general alias coverage.
- **`resolve_char` renamed to `resolve_operator_name` with tagged-union return** (`Resolved | Ambiguous | Missing`). Surfaces ambiguity for the 9 duplicate names rather than silently picking. Honest naming about operator-only scope.
- **AGENTS_GUIDE.md gains a "what the resolver does and doesn't cover" subsection** with worked Missing → grep fallback example.
- Risks table updated with three new rows (lore-entity coverage gap, curated alias coverage, ambiguity behavior).

### 2026-05-08 — Demote .agents/skills/audit-lore-wiki/ from live skill to design proposal

**User intent:** Asked whether `.agents/skills/audit-lore-wiki/SKILL.md` is a previous incremental update process worth keeping or an orphan. Answer: orphan — `git log --all -- 'scripts/audit*'` returns 0 commits, the skill is `??` (untracked) in `git status`, and the TSVs in `tmp/` are stale 2025-vintage hand artifacts whose mtimes were touched today. The 5-pass framework (tags / coverage / xref / grounding / sticker_loss) is substantive design content though, not noise — directly informs Phase 6 `kb_audit_wiki.py`.

**Outcome:** User said "yes" to demoting. Move file to `docs/proposals/audit-lore-wiki-prior-spec.md`. Future `kb_audit_wiki.py` should reference / adapt this prior spec — the grounding pass especially is what the new audit subsumes.

### 2026-05-08 — Design revisions after Codex review 04 (simplicity / cost / correctness)

**User intent:** Fourth reviewer pass landed at `docs/reviews/2026-05-08-codex-review-04-simplicity-cost-correctness.md`. User asked for the same evaluate-and-apply treatment.

**Outcome:** All four findings empirically reproduced, then applied:
- **M4 measurement command fixed and recorded result corrected.** Earlier draft crashed because `get_char_info_text_prompt` requires `name`; recorded count `~2900` was a transcription error. Verified result: 449 entries in `get_all_char_info`, 444 with name, char-text size median=5484 / p95=9016 / max=11460. Fixed snippet adds the `if v.get('name')` guard.
- **Added M5 measurement** for event-size + stage-count distribution. Verified: 78 events > 50K, 51 > 80K, 35 > 100K, 11 > 150K, 2 > 200K; stages per event median 1, p90 19, max 41, 69 events have >10 stages.
- **Multi-pass summary trigger lowered** from `total_length > 200K` to `total_length > 80K OR stage_count > 10`. Affects ~70-90 events instead of just 2 — actually cost-aware now.
- **Per-character LLM summaries dropped from v1.** Removed `kb_summaries/chars/`, `kb_summarize.py --target chars`, `kb_query summary char`. Char data is already sectional and small; a one-line summary would duplicate `manifest.json`. P2 prompt in `PROMPTS.md` marked DEFERRED for possible v2.
- **Stage-precise deterministic edges restored** in `event_to_chars.json` (was lossy in earlier draft). Added `stage_chars(event_id, stage_idx)` query and corresponding `kb_query event stage_chars` CLI. Updated AGENTS_GUIDE workflow recipe to use `stage_chars` for tight scope rather than overstating what `event chars` guarantees.
- `event_chars` and `stage_chars` now return `Appearance` (with `stage_idx` + `source`) rather than `CharMeta`, matching the data shape and preserving relation metadata.

### 2026-05-08 — Design revisions after Codex reviews 05 (follow-up) and 06 (independent)

**User intent:** Two more reviewer passes landed at `docs/reviews/2026-05-08-codex-review-05-followup.md` (4 findings) and `docs/reviews/2026-05-08-codex-review-06-independent.md` (3 findings, P1/P1/P2 priority). User asked for joint evaluate-and-apply across both, since 06 is independent of the prior conversation.

**Outcome:** Empirically verified each finding (5 nameless `npc_*` records, 23 single-zh-char operators incl. `陈/年/夕/黑/令`, `暮落` collides on `char_512_aprot` + `char_4025_aprot2`, `暮落;沉渊` is line 93 of `char_alias.txt`). All 7 findings applied:

- **Build rule tightened from "any data" to "has `name`"** (R05 #1). 5 nameless `npc_*` records explicitly skipped + listed in build report. Manifest schema requires `name`, so this prevents emitting unusable operator manifests.
- **PROMPTS.md multi-pass threshold synced to DESIGN.md** (R05 #2). Was still saying `> 200 KB`; fixed to `total_length > 80,000 OR stage_count > 10`. Added a callout linking back to M5.
- **`grep_text` made literal-by-default; `--regex` opt-in** (R05 #3). The fallback path is hit hardest by NPC/group names with parens / hyphens / smart quotes, where regex-by-default is brittle. `kb_query grep "<text>" [--regex]` mirrors the API change.
- **Inferred-edge matcher split into three classes** (R06 #1). Class A (canonical names + appellation) has **no length floor** — restores recall on 23 single-char operators. Class B (curated aliases) and C (fuzzy) keep the 2-char floor. Each edge records its `match_class` so consumers can downweight `canonical_short` hits without forced filtering.
- **Audit redesigned to two-signal model** (R05 #4 + R06 #2). Signal 1: entity-coverage diff (catches omissions). Signal 2: claim-level LLM coverage check with `有依据 / 无依据 / 不确定` verdicts (catches wrong attributions + hallucinations between known entities — what entity-diff alone misses). Both run by default. Budget caps baked into `kb_audit_wiki.py` as constants: ≤30 claims/event, ≤15 omission candidates/event, 3 stages × 8K chars per call, ~150K input-token soft budget. `--audit-all` opts out for long mainline events.
- **Curated-alias join now refuses ambiguous canonicals** (R06 #3). When `canonical` collides with a duplicate display name (`暮落` etc.), the alias line goes into a separate `ambiguous_aliases` map keyed by canonical, NOT into either operator's `manifest.aliases`. `resolve_operator_name(沉渊)` returns `Ambiguous([char_512_aprot, char_4025_aprot2])` — auto-attaching to one or both would either be arbitrary or silently broaden alias scope.
- **Risks table expanded:** updated grep-false-positives row to reflect class-aware floors; added rows for audit-cost ceiling and the entity-diff blind spot.

### 2026-05-08 — Design revisions after Codex reviews 07-consistency and 07-independent

**User intent:** Two more reviewer passes landed: `2026-05-08-codex-review-07-consistency.md` (4 findings; P1×2, P2, P3) and `2026-05-08-codex-review-07-independent.md` (3 findings; P1, P2×2, independent of prior context). User asked for a joint update round.

**Outcome:** Empirically reproduced each empirical claim — `司辰` is genuinely absent from `character_table.name`, `appellation`, `char_alias.txt`, and raw `character_table.json` (an embarrassing example I'd written); README / DESIGN.md inline drift confirmed by direct read; `Appearance` / `event_to_chars.json` shape inconsistency confirmed. All 7 findings applied:

- **P3 prompt rewritten and split** (R07c #1). Old single-prompt schema (`<遗漏>` + `<可疑描述>`) replaced with `P3a` (omission verification — Signal 1) and `P3b` (per-claim verdict — Signal 2). Each is per-item, matches the two-signal contract in DESIGN.md exactly. Output tags: P3a uses `判断 / 理由 / 原文证据`; P3b uses `判断 / 证据 / 说明`.
- **`Appearance` and `event_to_chars.json` flattened to one shape** (R07c #2). Both now use one row per `(char_id, stage_idx)`. No more aggregate-vs-flat mismatch. Inferred edges that span K stages produce K rows. Event-level rollup is the caller's responsibility via a `query.py` helper. Inline ASCII layout at DESIGN.md:105 updated to match.
- **Prune rule added to build contract** (R07c #3). `kb_build.py` deletes `events/<id>/` and `chars/<id>/` directories not referenced by the freshly computed manifest unless `--no-prune` is passed; same rule for `kb_summarize.py` against `kb_summaries/events/<id>.md`. Removed paths printed in build report. Closes the stale-file gap when upstream `ArknightsGameData` removes/renames an event.
- **README "01 & 02" status row updated to "01-07"** (R07c #4); inline ASCII shape (DESIGN.md:105) replaced as part of the flatten fix above.
- **Signal 1 broadened with NPC-shaped candidate extraction** (R07i #1). Code-only, regex `[一-鿿]{2,6}` with frequency ≥3 and stage-spread ≥2, blocklist-filtered, capped at top-20 per event. Closes the NPC-omission blind spot. False positives are surfaced to the LLM (P3a) for verdicts, never auto-attributed.
- **`match_class` threaded through `Appearance` and CLI** (R07i #2). Was internal-only despite being doc'd as a consumer-facing mitigation. Now part of `Appearance` and surfaced in `kb_query` JSON.
- **AGENTS_GUIDE.md ad-hoc Q/A example rewritten** (R07i #3). Replaced the impossible `司辰` example with two examples: (A) `陈` — real single-character operator that exercises `Resolved` + `match_class=canonical_short` caveat, (B) `特蕾西娅` — NPC that demonstrates the documented `Missing → grep` fallback path.

### 2026-05-08 — Phase 1 implementation kicked off

**User intent:** "Look at `arknights_lore_wiki_lib/docs/` and start the execution." User confirmed scope = Phase 1 only (paths + chunker + tests) and pytest (over unittest) as the test framework.

**Outcome:** Phase 1 landed:

- `libs/kb/__init__.py`, `libs/kb/paths.py`, `libs/kb/chunker.py` written.
- `libs/game_data.py` extended: `extract_data_from_character_table` now retains `appellation` (DESIGN.md "Aliases" pre-req).
- `tests/fixtures/mini_gamedata/` synthetic snapshot built (3 events / 6 stages / 3 chars covering all 4 source families + nameless `npc_*` skip case).
- `tests/test_paths.py` + `tests/test_chunker.py` — 56 tests, green.
- `pytest` added to `requirements.txt`; installed in `.venv`.
- M1–M5 measurement snippets re-run against full game data; numbers match DESIGN.md (461 events / 1937 stages, 372 storyset linked / 0 ambiguous, 444 named chars, etc.).
- **`source_family` classifier amended** to honor `entryType=MAINLINE` ahead of `storyTxt` prefix. Real data has `main_0` (the prologue, entryType=MAINLINE) whose first stage is `obt/guide/beg/0_welcome_to_guide` — a prefix-only rule misclassified it as `other`. Doc rule "mainline | storyTxt starts with `obt/main/` (also matches entryType=MAINLINE)" was implementable as either; now the classifier and DESIGN.md docstring both spell out entryType-first.

### 2026-05-08 — Phase 2 implementation landed (indexer + query)

**User intent:** Continue Phase 2 from the entry points the prior session left in `docs/README.md` and `docs/DESIGN.md#implementation-phases-proposed` — `libs/kb/indexer.py`, `libs/kb/query.py`, and the matching tests, with no LLM use.

**Outcome:** Phase 2 landed:

- `libs/kb/indexer.py` — pure-code builders for the six (or seven, with curated aliases) `data/kb/indexes/*.json` files. Implements: curated alias-file parser, ambiguous-canonical computation, `events_by_family` grouping, deterministic edges from `chars/<id>/storysets.json`, inferred-edge grep with the three-class match floor (canonical / canonical_short / curated; fuzzy reserved as a future class), per-(char, event) deterministic-subtraction rule, the highest-precision-class-wins aggregation per `(char, stage)` row, the flat one-row-per-(char, stage) `event_to_chars.json` shape, the resolver `alias_to_char_ids` index that attaches curated aliases to *all* owners when the canonical collides (so the resolver returns `Ambiguous` rather than picking arbitrarily). Atomic JSON writes via `os.replace`.
- `libs/kb/query.py` — pure-function retrieval API on top of a loaded `KB` dataclass: `load_kb`, family-aware `list_events` / `list_families`, `get_event` / `get_stage_text`, `list_chars` (nation filter), `resolve_operator_name` returning `Resolved | Ambiguous | Missing`, `get_char_section` (single section or `all`), `char_storysets`, `char_appearances` / `event_chars` / `stage_chars` returning `Appearance` (carrying `stage_idx`, `source`, `count`, `match_class`, `story_set_name` per the flat shape), `grep_text` literal-by-default with `regex=True` opt-in, `group_by_event` rollup helper, `get_event_summary` reading from optional `summaries_root`.
- `tests/test_indexer.py` (32 tests) + `tests/test_query.py` (33 tests) — full unit coverage of every public function, plus integration tests that build a real KB on disk via `chunker.write_event` / `write_char` against the mini fixture and exercise the indexer + query end-to-end. Hand-built KB helpers cover the ambiguous-canonical / collision cases that the mini fixture can't reproduce.
- Smoke-tested `parse_curated_alias_file` against the live `arknights_lore_wiki/data/char_alias.txt`: 265 canonicals (matches DESIGN.md "Aliases" measurement); spot-checks for `临光`, `凯尔希`, `暮落` parse correctly.
- Total test count: **127 passing.**

The KB is now retrieval-ready end-to-end *modulo* a `kb_build.py` script (Phase 3) that wires `chunker.write_event` + `write_char` + `indexer.build_all_indexes` into a single command. The `query.load_kb` / `query.*` API can already drive an agent against any KB built by hand; Phase 3 just packages the builder.


### 2026-05-08 — Phase 3 implementation landed (build + query CLIs)

**User intent:** After Phase 2 indexer + query went green, ship Phase 3 alone (no Phase 4 bundling) — the design called this out as the natural validation checkpoint where assumptions either survive contact with the full corpus or get revised before the LLM-using phases pile on. Branch was renamed `kb-phase-1` → `kb-build` to match scope.

**Outcome:** Phase 3 landed:

- `scripts/kb_build.py` — end-to-end deterministic build. Reads `keys.json` (fallback flags `--game-data-path`, `--wiki-path`, `--curated-aliases`, `--kb-root`). Snapshots `data_version.txt` and a 12-char SHA of `clean_script`'s source into `data/kb/manifest.json` so a future build can detect parser-driven cache staleness. Pre-computes ambiguous canonicals once and threads them into both `chunker.write_char` and the indexer so curated-alias attachment is consistent across both passes. Skips the 5 nameless `npc_*` records and lists them in the manifest. Prunes any leftover `events/<id>/` or `chars/<id>/` directory not in the new build (default on; `--no-prune` skips). Emits a printed report + a complete `manifest.json`.
- `scripts/kb_query.py` — argparse subcommand surface mirroring DESIGN.md§"Retrieval pipeline": `event {list,get,chars,stage_chars,stage}`, `family list`, `char {resolve,get,appearances,storysets}`, `grep`, `summary event`. JSON output by default, `--text` flag returns raw chunk text on the three text-returning commands. `char resolve` exits 2 on `Missing` so an agent can branch on exit code alone. Dataclasses + `Resolution` types serialize via `dataclasses.asdict` so the `kind` discriminator is preserved end-to-end.
- Real-data validation: `kb_build` runs in 8.2s against the live corpus and produces exactly the M-series numbers from DESIGN.md — 461 events (`mainline`:18, `activity`:51, `mini_activity`:20, `operator_record`:372, `other`:0), 444 named chars + 5 nameless skipped, 372 deterministic edges across 323 chars, 360 chars with inferred edges, 9 ambiguous canonicals (the documented `暮落 / 郁金香 / Sharp / Stormeye / Pith / Touch / 预备干员-{术师,狙击,重装}` set), 265 curated alias canonicals loaded, 0 storyset warnings. `main_0` correctly carries `storyTxt_prefixes: ["obt/guide", "obt/main"]` (the multi-prefix case). The `圣巡` Sticker-text canary surfaces in `act46side` stage 4 — confirms the `clean_script` Sticker fix flows through to the KB. Idempotent rebuild leaves the file count unchanged. Prune contract verified by injecting fake `events/zzz_dead_event/` + `chars/char_dead_xyz/` and confirming both disappear after rebuild.
- Test suite still **132 passing** — Phase 3 didn't perturb the libs.


### 2026-05-08 — Phases 4 + 5 implementation landed (LLM dispatch + first summarizer)

**User intent:** Bundle Phase 4 (`libs/llm_clients.py` — three-backend dispatch) with Phase 5 (`libs/kb/summarize.py` + `scripts/kb_summarize.py`) on one branch (`kb-summarize`) so the substrate ships alongside its first real consumer. Phase 4 alone has nothing to exercise it; pairing with Phase 5 gives end-to-end validation in one PR.

**Outcome:** Phase 4 + Phase 5 landed:

- **`libs/llm_clients.py`** — `LLMClient` Protocol + three implementations behind a single `make_client(backend, **kwargs)` factory. `GeminiCLIClient` (default model `gemini-3-flash-preview`), `GeminiSDKClient` (`gemini-2.5-flash`), `ClaudeCLIClient` (`claude-haiku-4-5`, with `shutil.which` check at instantiation per DESIGN.md "raises a clear error if missing"). All three honor `RETRY_LIMIT` / `RETRY_SLEEP_TIME` and surface `LLMError` on persistent failure. Gemini CLI invocation uses `--approval-mode plan` (read-only — refuses to call any tool) instead of `-y` (YOLO auto-approve); strictly safer for batch text-summarization where no tool calls are intended. Claude CLI invocation uses `--print --system-prompt --output-format json --no-session-persistence`, sends the prompt via stdin, and checks `is_error` so zero-exit rate-limit / blocked / model-not-found responses still raise instead of being treated as content. It intentionally avoids `--bare` so subscription/OAuth keychain auth continues to work. Added `query_with_validated_tags(client, system, prompt, required_tags)` helper — same retry-once-with-reminder semantics as `bases.query_llm_validated`, but takes a client directly (used by summarize and reusable for future audit code).
- **`libs/bases.py` refactor** — `query_llm` and `query_llm_validated` keep their public signatures but now delegate through `make_client`. The two old per-backend helpers (`query_llm_cli`, `query_llm_gai`) and their duplicated retry logic are deleted; `subprocess` / `time` imports follow them out. Existing scripts (`get_story_wiki`, `get_char_wiki_v3`) call sites stay byte-identical. Added a `TypeError` on unexpected kwargs to catch typos that the old `**kwargs`-blind dispatch would have silently lost.
- **`libs/kb/summarize.py`** — pure module: `summarize_event` for one event, `summarize_all` for the corpus. Routes to single-pass vs multi-pass on the M5 thresholds (`total_length > 80,000` OR `stage_count > 10`). Multi-pass = per-stage `章节概要 + 本章人物` reduce, then a merge call asking for the four event-level tags. Validates with `validate_and_rebuild` after a one-shot retry-with-reminder. Source-hash gate (sha256 over sorted stage filenames + bytes) keeps re-runs free unless `force=True` or the .md is missing on disk. Manifest at `kb_summaries/manifest.json` records the per-event hash + backend + model + generated-at. Prune is part of the contract (matches Phase 3): orphan `kb_summaries/events/<id>.md` files are dropped unless `--no-prune`. Output is markdown with frontmatter (`event_id`, `event_name`, `source_family`, `source_hash`, `passes`, `total_length`, `stage_count`, `backend`, `model`) followed by canonicalized zh tags.
- **`scripts/kb_summarize.py`** — CLI wrapper. `--llm cli|gai|claude` (default reads `keys.json llm_backend`), `--model`, `--event <id>` (repeatable), `--force`, `--no-prune`, `--kb-root`, `--summaries-root`. Reads keys.json for backend-specific defaults (`claude_cli_path`, `claude_model`, `gemini_cli_path`, `genai_api_key`). Exits non-zero if any per-event errors occurred but keeps going through the batch.
- **Real-LLM smoke test** on `story_12fce_set_1` (3,156 chars, single-pass): Gemini CLI returned a complete `<一句话概要> + <核心剧情> + <关键人物> + <场景标签>` block on second try (first attempt was missing `场景标签` — the retry-with-reminder mechanism fired and recovered, exactly as designed). Re-run was a no-op via the source-hash cache. `kb_summaries/events/story_12fce_set_1.md` and `kb_summaries/manifest.json` committed alongside the code as the canonical first artifact.
- **Model-name drift fix:** `keys.json llm_model` and `bases.DEFAULT_CLI_MODEL` were both set to `gemini-3.1-flash`, which the live Gemini API now rejects with `ModelNotFoundError`. Updated to `gemini-3-flash-preview` per the user. Surfaces here because the smoke test would have hung indefinitely without it.
- **`docs/PROMPTS.md` already had the P1 prompts** (single-pass + stage-reduce + merge); summarize.py mirrors them as Python constants. PROMPTS.md remains the canonical reviewable source.
- **Tests:** 184 passing total (132 prior + 27 new in `test_llm_clients.py` + 25 new in `test_summarize.py`). Mock-based throughout — `subprocess.run` and `genai.Client.models.generate_content` are monkeypatched; a `FakeClient` records every `(system, prompt, model)` tuple so call-count and prompt-shape assertions are exact. No real LLM in CI.

### 2026-05-11 — `kb_summarize --estimate` (no-LLM cost dry-run)

**User intent:** "How many tokens are needed to finish the kb summary work?" — asked frequently, so the user wanted it as reusable code + documented in the skill rather than recomputed by hand each time.

**Outcome:** Added `summarize.estimate_remaining()` / `estimate_event_cost()` in `libs/kb/summarize.py` (with `EST_*` output-size guesses and an `EST_CHARS_PER_TOKEN` divisor as tunables) and a `--estimate` flag on `scripts/kb_summarize.py`. The flag skips client construction entirely (no keys.json needed) and prints events-to-run (single vs multi pass), already-done count, total LLM calls, and projected input/output/total chars≈tokens. Honors `--event` / `--force` / `--kb-root` / `--summaries-root`; selection mirrors the real run (no manifest entry or missing `.md` → counts). Documented under Step 2 of the `refresh-kb` skill. Baseline 149/461 → ~312 events, ~559 calls, ~4.2M tokens (~3.8M in / ~0.4M out); a full `--force` re-bake ≈ ~13.2M tokens.

### 2026-05-11 — Live progress output during `kb_summarize` runs

**User intent:** A real (non-`--estimate`) `kb_summarize` run was silent for the entire multi-hour bake. The user wanted it to stream estimated progress per event — "added into the existing kb summary, output during run".

**Outcome:** `summarize_all` now accepts an optional `progress` callback and emits a `ProgressEvent` per event (position, status, running token estimate vs. the run's projected total, elapsed, token-rate ETA). Selection/estimate math is shared with `--estimate` via a new `_classify_run` helper (`estimate_remaining` was refactored onto it). `scripts/kb_summarize.py` passes a printer: `[i/N] <event_id>  +single|+multi  done X/Y ev  ~tok/tok  elapsed  ETA ~…` for writes, `· cached` for hash-skip hits, `✗ TERMINAL …` then stop on a terminal LLM error. The redundant per-event `+ <eid>` enumeration in the final report was dropped (progress already streamed it). `progress` defaults to `None`, so library callers / tests are unaffected (31/31 `test_summarize.py` still pass). README §4 and the `refresh-kb` skill Step 4 updated.
