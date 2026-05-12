# Agent's Guide to the Arknights Knowledge Base

> Read this when you've been asked to do **lore Q/A**, **audit a wiki update**, or **audit an existing wiki page**. If your task is to *generate* new wiki content (not audit it), use the `update-lore-wiki` skill instead — that's a different flow.

> Status: **draft**. The KB itself isn't built yet (see `DESIGN.md`). This guide describes the intended usage once it is.

## What's in the KB

Two layers:

- **Raw layer** at `data/kb/` (gitignored, regenerable from `ArknightsGameData` in minutes via `scripts/kb_build.py`):
  - Per-stage cleaned story chunks under `events/<event_id>/stage_NN_*.txt` (median 5K zh chars, max 21K).
  - Per-character **sectional** files under `chars/<char_id>/{profile,voice,archive,skins,modules}.txt` plus `manifest.json` and `storysets.json`. Read only the section you need.
  - JSON indexes: `events_by_family.json` (mainline / activity / mini_activity / operator_record / other), `char_to_events_deterministic.json`, `char_to_events_participant.json`, `char_to_events_summary.json`, `event_to_chars.json`, `stage_table.json`, `char_table.json`, optionally `char_alias.json`.
- **Summary layer** at `kb_summaries/` (in git, optionally LLM-baked via `scripts/kb_summarize.py`):
  - Per-event 600-zh-char summary + 关键人物 list + 场景标签.
  - **No per-char summaries in v1** — char data is already sectional and small (median ~5 KB, max ~11 KB total). Read `chars/<id>/manifest.json` for navigation, then specific section files for content.

Read event summaries first when picking which event to dig into. They're tiny. Drop down to raw only when the summary is insufficient.

### Source families (the primary navigation axis)

The corpus is divided into four content families, derived deterministically from `storyTxt` prefix and `entryType`:

| Family            | What's in it                          | Approx count |
|-------------------|---------------------------------------|--------------|
| `mainline`        | Main-story chapters (`obt/main/...`)  | ~17 events   |
| `activity`        | Side-story activities                 | ~51 events   |
| `mini_activity`   | Story collections                     | ~20 events   |
| `operator_record` | Operator-record / memory content (`obt/memory/...`) | **372 events** |
| `other`           | Residual (`obt/guide`, `activities/a001`, …) | ~1 event     |

For char-related Q/A, **operator_record** is usually the highest-yield family — most chars have a deterministic record there.

### Edge confidence: three layers + a tier

Every char↔stage link in the KB carries a `source` (and, for `participant`, a `tier`):

- **`deterministic`** — from a handbook storyset's `storyTxt` resolving to a story-review event. 372 such links. Ground truth; always passes any `--min-tier`. Trust these.
- **`participant`** — derived per stage from the cleaned chunk text, with a **`tier`**:
  - `speaker` — the char had ≥1 line of dialogue in that stage (`名字:台词`, materialized by `clean_script`). This is what "appears in" should mean by default — highest precision.
  - `named` — an alias appears in narration: a multi-char canonical zh name, an ASCII canonical name with a real word boundary (`W` ⊄ `World` but `W` ⊂ `W走`), a single-zh-char canonical seen ≥2× (or also listed in the event summary), or aliases summing to ≥2 mentions.
  - `mentioned` — a lone passing reference and nothing stronger. Kept as a recall floor; **dropped by default** (`--min-tier named`). When you see one, say "name-dropped", not "appears in".
  - `participant` edges are *additional* to deterministic ones (the exact stage that has a deterministic edge is not re-emitted as a participant).
- **`summary`** — *event-scoped* (`stage_idx` is `null`): the `<关键人物>` tag of a baked event summary, each surface name resolved through the alias index. Catches chars referred to only by a title/nickname a name-grep misses. Treated as `tier=named` for `--min-tier`. (Event-scoped today; a later phase will make per-stage summaries upgrade it to stage granularity.)

`event chars` / `stage_chars` / `char appearances` take `--source {deterministic,participant,summary,all}` (default `all`) and `--min-tier {speaker,named,mentioned}` (default `named`). When precision matters, pin `--source deterministic` or `--min-tier speaker`.

### What the resolver does and doesn't cover (operator-only)

`kb_query char resolve <name>` is **operator-only** — it searches `character_table` `name` + `appellation` (English codename) plus, when present, the curated `arknights_lore_wiki/data/char_alias.txt`. Three possible outputs:

- `Resolved` — single operator match.
- `Ambiguous` — multiple operators share that display name (9 known duplicates: `暮落`, `郁金香`, `Sharp`, `Stormeye`, `Pith`, `Touch`, plus three `预备干员-*`). Also surfaces when a curated-alias canonical itself is ambiguous: e.g. `沉渊` is listed in `char_alias.txt` under `暮落`, but `暮落` collides on two `char_id`s, so `沉渊` resolves as `Ambiguous([char_512_aprot, char_4025_aprot2])` rather than being silently attached to either. The CLI prints all candidates; you (the agent) pick.
- `Missing` — no match. **Common cases:** the name is an NPC (`特蕾西娅`, `霜星`, `鼠王`), a title (`皇帝的利刃`), a group (`整合运动`), an alternate-body identity (`劳伦缇娅`), or a civilian name (`玛嘉烈`). About 90% of the curated alias entries are not recoverable from raw game data, so without the alias file even some real operator aliases will return `Missing`.

**Single-character operator names work.** 23 operators have one-character zh display names (e.g. `陈`, `年`, `夕`, `黑`, `令`, `空`, `阿`). These resolve normally. In the `participant` edge layer a *lone* narration hit of a one-char name stays at `tier=mentioned` (so `年` ⊄ "appears in" just because the prose says `今年`); it's promoted to `named` only when the char also speaks, the name appears ≥2×, or the event summary lists it. So with the default `--min-tier named` a `年`-the-operator participant edge means a real mention, not noise — but if you ever drop to `--min-tier mentioned`, verify a single-char hit by reading the stage.

**When the resolver returns `Missing`, fall back to `kb_query grep "<name>"`.** The grep search is **literal substring by default** (use `--regex` only if you actually want regex semantics) and finds any occurrence in stage text or char-section files, regardless of entity type. For NPCs and groups, literal grep is the v1 retrieval mechanism, and it handles names with parentheses / hyphens / smart quotes (`AUS (群体)`, `Ishar-mla`, `"桥夹"克里夫`) correctly without escaping.

Example resolver flow when uncertain:
```
$ kb_query char resolve 玛嘉烈
{"kind": "missing", "candidates": []}     # alias file absent OR name truly unknown

$ kb_query grep 玛嘉烈
[{"event_id": "...", "stage_idx": 3, "snippet": "..."}, ...]   # literal substring; always works as a recall floor
```

## CLI surface (no LLM, fast)

All commands run from the lib repo root with `.venv/bin/python`:

```
.venv/bin/python -m scripts.kb_query family list
.venv/bin/python -m scripts.kb_query event list [--family mainline|activity|mini_activity|operator_record|other]
.venv/bin/python -m scripts.kb_query event get <event_id>
.venv/bin/python -m scripts.kb_query event chars <event_id> [--source deterministic|participant|summary|all] [--min-tier speaker|named|mentioned]
.venv/bin/python -m scripts.kb_query event stage_chars <event_id> <stage_idx> [--source ...] [--min-tier ...]
.venv/bin/python -m scripts.kb_query event stages <event_id>          # per-chapter listing: idx / name / avgTag / length — pick one <章节> to read
.venv/bin/python -m scripts.kb_query event stage <event_id> <stage_idx> [--text]
.venv/bin/python -m scripts.kb_query char resolve <name_or_alias>     # OPERATOR-ONLY; returns resolved/ambiguous/missing
.venv/bin/python -m scripts.kb_query char get <char_id> [--section profile|voice|archive|skins|modules|all] [--text]
.venv/bin/python -m scripts.kb_query char card <char_id_or_name>      # deterministic fact card: 基础档案 fields / 客观履历 verbatim / skin+module names / storysets — each tagged with its source table
.venv/bin/python -m scripts.kb_query char appearances <char_id_or_name> [--source deterministic|participant|summary|all] [--min-tier speaker|named|mentioned]
.venv/bin/python -m scripts.kb_query char storysets <char_id>
.venv/bin/python -m scripts.kb_query grep "<text>" [--regex] [--in events|chars|summaries|all]    # literal substring by default; --regex opts in; `summaries` searches the baked event summaries (high-signal, no raw-script noise)
.venv/bin/python -m scripts.kb_query summary event <event_id>
# `summary char <id>` is intentionally absent in v1 — read the char dossier directly via `char get`.
```

Output is JSON unless `--text` is set. JSON is short — designed to fit in your context. Raw `--text` of a stage may be 5-20 KB; pull only when needed. Per-section char text is much smaller (typically <2 KB per section), so prefer `char get <id> --section archive --text` over a full dossier dump.

## Workflow: ad-hoc Q/A

### Example A — operator that resolves cleanly

User asks: *"陈在哪些活动里出现过？"* (`陈` is a real single-character operator name; resolves directly.)

1. `kb_query char resolve 陈` → returns `Resolved(char_010_chen)`.
2. `kb_query char storysets char_010_chen` → deterministic operator-record link(s). Read these first; they're guaranteed about this char.
3. `kb_query char appearances char_010_chen --source deterministic` → same data formatted as `Appearance` rows (one per stage). For text-derived mentions across mainline / activities, drop the filter (the default `--source all --min-tier named` already keeps the trustworthy ones); drop to `--min-tier speaker` for "where did 陈 actually have lines", or `--min-tier mentioned` only as a last-resort recall floor (then sanity-check single-char hits on the stage text).
4. For each event you care about, `kb_query summary event <event_id>` for a short answer.
5. If the user wants a specific scene, `kb_query event stage_chars <event_id> <stage_idx>` returns the chars whose edge points at that exact stage (tighter than `event chars`, which spans the whole event). Then `kb_query event stage <event_id> <idx> --text` to read it.

### Example B — name that doesn't resolve (the `Missing → grep` fallback)

User asks: *"特蕾西娅在哪些剧情里出现过？"* (`特蕾西娅` is an NPC, not in `character_table`.)

1. `kb_query char resolve 特蕾西娅` → returns `Missing` (no operator match — expected for NPCs / titles / groups).
2. `kb_query grep "特蕾西娅"` → literal substring search across stage text + char-section files; returns hits with `(event_id, stage_idx, snippet)` rows. This is the v1 retrieval mechanism for NPCs.
3. Group hits by `event_id` and read the most-mentioned stages first via `event stage <id> <idx> --text`.
4. Optionally `kb_query summary event <event_id>` for the surrounding event context.

The same flow handles names with parens / hyphens / smart quotes (`AUS (群体)`, `Ishar-mla`, `"桥夹"克里夫`) — `grep` is literal by default, so no escaping needed.

**Don't dump the raw event into your context unless the question requires it.** Stage-level granularity is usually enough. **Don't pull every char section** — if the question is about voice lines, `--section voice` is enough; archives, `--section archive`; etc.

## Workflow: audit a freshly generated story summary

Use `scripts/kb_audit_wiki.py`. It's the orchestrator — you don't write the audit prompts yourself.

```
.venv/bin/python -m scripts.kb_audit_wiki --target story <event_id>
```

What happens under the hood — **two signals run together** (per `DESIGN.md` audit section):

1. **Entity-coverage diff** (cheap, no LLM): grep entities from raw vs. summary, surface omission candidates. Catches missing scenes / characters.
2. **Claim-level coverage** (LLM, gated by budget): parse claims out of the summary's tagged sections, gather 1-3 relevant raw stages per claim, ask the LLM (`docs/PROMPTS.md#P3`) for a `有依据 / 无依据 / 不确定` verdict per claim with required citation. Catches wrong attributions and hallucinations between already-mentioned entities.
3. Both signals merge into one markdown report (sections "Possible omissions" + "Per-claim verdicts").

**Budget caps are baked in.** Default: ≤30 claims, ≤15 omission candidates, 3 stages × 8K chars per LLM call, ~150K input-token soft budget per event. Same-stage candidates collapse into one prompt. Pass `--audit-all` if you need to bypass caps for a long mainline event; otherwise the script stops + reports when the budget would be exceeded.

**The LLM call is offloaded.** The agent (you) doesn't process the raw text in-context — the audit script's CLI subprocess does. You read the report.

To override the backend: `--llm cli|gai|claude`.

## Workflow: audit an existing character wiki page

```
.venv/bin/python -m scripts.kb_audit_wiki --target char <char_id>
```

Same two-signal pattern, but the **claim-level signal is primary** for char wikis (their structured tag set parses cleanly into per-claim assertions). For each `<剧情高光>` bullet, the script pulls relevant raw stages — deterministic edges from `storysets.json` first, then `participant` edges at `speaker`/`named` tier — and asks the LLM to verify with `docs/PROMPTS.md#P4`. Same budget caps as `--target story`.

## When to use which LLM backend

- **Default (Gemini CLI):** cheap, long context, good at zh. Use for bulk summarization and audit work.
- **Gemini API (gai):** when CLI is flaky or when you want concurrency. Costs the same.
- **Claude CLI:** for a "second opinion" on an audit, or when you want output in a slightly different register. Adds variance for cross-checking — useful when the Gemini result feels off.

To compare two backends on one task:
```
kb_audit_wiki --target story <id> --llm cli   > tmp/audit_gemini.md
kb_audit_wiki --target story <id> --llm claude > tmp/audit_claude.md
diff -u tmp/audit_gemini.md tmp/audit_claude.md
```

## What NOT to do

- **Don't** read `arknights_lore_wiki/data/stories/<id>.txt` and treat it as primary source for Q/A. It's an LLM-generated summary that may have hallucinations. Use the raw KB for ground truth.
- **Don't** build the KB by writing your own parser. Reuse `libs/kb/build.py` (and `libs/game_data.py` underneath).
- **Don't** call an LLM directly from your context for bulk summarization. Use `kb_summarize.py` or `kb_audit_wiki.py` so the call happens out-of-context.
- **Don't** commit `data/kb/`. It's gitignored; verify with `git status` if unsure.
- **Don't** hand-edit `kb_summaries/`. Re-run `kb_summarize.py` if a summary is wrong.

## When summaries are stale

`kb_summaries/manifest.json` records the source-text hash for each summary. `kb_summarize.py` rebuilds only stale entries. After a `git pull` of `ArknightsGameData`, re-run `kb_build.py` first; then `kb_summarize.py` will detect the deltas.

## Where to file bugs

This guide is part of the code. If a workflow recipe doesn't match reality, either the code or this doc is wrong — surface it with the user before patching either.
