# Codex Review 07 — Consistency / Correctness Follow-up

Scope: reviewed the latest `/Users/dehuacheng/Claude/arknights/arknights_lore_wiki_lib/docs/DESIGN.md`, `/Users/dehuacheng/Claude/arknights/arknights_lore_wiki_lib/docs/AGENTS_GUIDE.md`, `/Users/dehuacheng/Claude/arknights/arknights_lore_wiki_lib/docs/PROMPTS.md`, and `/Users/dehuacheng/Claude/arknights/arknights_lore_wiki_lib/docs/README.md`.

## Findings

### [P1] The audit prompt spec has drifted out of sync with the redesigned two-signal audit contract

The design and agent guide now say the LLM-side audit works by parsing claims and asking for a per-claim verdict of `有依据 / 无依据 / 不确定` with citations:

- `DESIGN.md:555-561`
- `AGENTS_GUIDE.md:108-110`

But `PROMPTS.md#P3` is still the older omission/hallucination batch prompt with `<遗漏>` / `<可疑描述>` output tags:

- `PROMPTS.md:99-137`

That is not a small wording difference. It changes the output schema, the unit of judgment, and the failure modes the audit can actually catch. If implementation follows `PROMPTS.md`, the story-audit path will regress toward the weaker pre-review design even though the higher-level docs now promise claim-level grounding.

Recommendation: rewrite `P3` to match the current Signal-2 contract exactly, or split the prompts explicitly into `P3a` (entity-diff follow-up) and `P3b` (claim verification) so the implementation target is unambiguous.

### [P1] The reverse-edge API shape is still internally inconsistent for inferred appearances

The merged reverse index now stores inferred event hits as one char entry with `stage_hits`:

- `DESIGN.md:276-281`

But the public query contract still says `event_chars()` returns `list[Appearance]`, and `Appearance` only carries a single `stage_idx_or_none` plus `count_or_none`:

- `DESIGN.md:319-321`
- `DESIGN.md:336`

Those two shapes do not line up. A single inferred event appearance may span multiple stages, but the documented `Appearance` type cannot represent that aggregate without either flattening into one row per stage or dropping detail. Right now the design is trying to do both at once: event-level aggregation in the index, stage-level precision in the API.

Recommendation: pick one contract and use it everywhere:

- either flatten inferred reverse edges to one `(char_id, event_id, stage_idx, count)` record per stage,
- or introduce a separate aggregate type for `event_chars()` that explicitly carries `stage_hits`.

Until that is resolved, `event_chars`, `stage_chars`, and `event_to_chars.json` are underspecified relative to each other.

### [P2] Incremental rebuild behavior still lacks a pruning rule for removed events, chars, and summaries

The build section says re-running is idempotent, hash-based, and "only re-writes changed files":

- `DESIGN.md:494`

The summary section similarly says `kb_summarize.py` skips unchanged items based on the manifest:

- `DESIGN.md:506`

But there is still no explicit rule for what happens when upstream data removes or renames an event, a char, or a storyset. In that case, old directories under `data/kb/events/`, old char folders, and committed files under `kb_summaries/events/` can linger indefinitely unless the implementation adds a prune step. For a KB that is meant to track game-data updates over time, stale files are a correctness problem, not just cleanup noise.

Recommendation: make pruning part of the contract. The simplest version is: rebuild indexes from scratch, then delete any event/char/summary path not referenced by the newly computed manifest unless `--no-prune` is set.

### [P3] A few smaller doc surfaces are still stale enough to trip future implementers

Two examples stood out:

- `README.md:25` still says the design docs were revised after reviews 01 and 02, even though 03-06 are already folded in below.
- The top-level on-disk layout comment for `event_to_chars.json` still shows the older coarse shape `[{char_id, source: deterministic|inferred}, ...]`:
  - `DESIGN.md:105`

Neither issue breaks the architecture, but both are exactly the kind of small drift that later turns into implementation drift.

Recommendation: tighten these small surfaces now while the design is still changing quickly.
