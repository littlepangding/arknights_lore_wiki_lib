# Codex Review 05 — Follow-up Pass

Date: 2026-05-08

Scope reviewed:
- `docs/DESIGN.md` (latest)
- `docs/AGENTS_GUIDE.md` (latest)
- `docs/PROMPTS.md` (latest)
- current measured corpus state from `ArknightsGameData`

This pass focuses on newly introduced or still-open issues after reviews 01-04 were folded in.

## Findings

### 1. Blocking correctness: the build pipeline still says "for each char with any data", but the measured corpus has nameless records

The updated M4 section now correctly records:

- `total entries: 449`
- `with name: 444`

See `docs/DESIGN.md:445`.

But the build pipeline still says:

- "For each char with any data"

and then immediately describes writing `chars/<char_id>/manifest.json` and section files. See `docs/DESIGN.md:477-480`.

That is still too loose. Five current records have no `name`, and the rest of the design is operator-name-driven:

- `manifest.json` expects `name`
- alias derivation depends on `name` / `appellation`
- `resolve_operator_name` is name-based

So the build contract should say something like:

- "For each char with `name`"

or explicitly define what happens to nameless records.

Otherwise the design still leaves room for invalid or unusable operator manifests to be emitted even though the measurements already proved those records exist.

### 2. Important correctness/cost: `PROMPTS.md` still documents the old `> 200 KB` multi-pass rule

The design now says multi-pass summary kicks in when:

- `total_length > 80,000`
- **or**
- `stage_count > 10`

See `docs/DESIGN.md:336` and `:447-463`.

But `PROMPTS.md` still says:

- "User (multi-pass, used when total event text > 200 KB ...)"

See `docs/PROMPTS.md:52-54`.

That doc drift matters because `PROMPTS.md` is supposed to be the implementation-facing prompt source. If someone implements against the prompt doc instead of the design prose, they will rebuild the too-expensive threshold that review 04 just removed.

This is a small edit, but it is important because it reintroduces both:

- cost inflation
- behavior ambiguity

### 3. Important simplicity/correctness: the fallback `grep` path should be literal-by-default, not regex-by-default

The current API and CLI still present grep as regex search:

- `grep_text(kb, regex: str, ...)`
- `kb_query.py grep "<regex>"`

See `docs/DESIGN.md:323` and `:513`.

But the guide also says `grep` is the v1 fallback for exactly the hardest names to resolve:

- NPCs
- titles
- groups
- civilian names

See `docs/AGENTS_GUIDE.md:52-60`.

Those names often contain punctuation or regex-significant characters. Existing alias examples include forms like:

- `AUS (群体)`
- `真龙 (当今)`
- `Ishar-mla`
- `“桥夹”克里夫`

Using regex-by-default makes the fallback path brittle for the exact entity types it is supposed to rescue. From a user/agent perspective, the intended operation is usually "find this literal string", not "run a regex".

Recommendation:

- make `kb_query grep` default to literal substring search
- add `--regex` only when regex semantics are actually wanted

That would improve both correctness and simplicity with almost no conceptual cost.

### 4. Important token-cost gap: the audit design still lacks an explicit candidate budget before LLM calls

The story-audit flow still says:

1. diff raw-vs-summary entities
2. for each candidate, fetch raw stage(s)
3. hand off to an LLM

See `docs/DESIGN.md:509-518`.

That is a workable outline, but it still has no explicit budget policy such as:

- max candidates per event
- deterministic prefilters
- per-event token budget
- batching vs per-candidate call strategy

So even after the summarization threshold fix, the audit phase remains a place where token spend can quietly explode, especially on:

- long mainline chapters
- noisy named-entity extraction
- events with many stage-local mentions

Because the design already cares about cost elsewhere, I think this should be made explicit before implementation. Even a simple rule would help, for example:

- audit only the top `K` unmatched candidates by frequency / stage spread
- collapse same-stage candidates into one LLM check
- stop after a per-event token budget

Without something like that, the Phase 6 design is still under-constrained on spend.

## What looks good

- The operator-only resolver contract is now much more honest.
- Removing per-char summaries from v1 is a strong simplification.
- Restoring stage precision in `event_to_chars` and adding `stage_chars` was the right correction.

## Review status

This revision is clearly stronger than the earlier drafts. The remaining issues are now more "tighten the contract" than "rethink the architecture".

If I were picking the next highest-value fixes before implementation, I would do them in this order:

1. tighten the build rule from "any data" to "has name"
2. sync `PROMPTS.md` with the new multi-pass threshold
3. make grep literal-by-default
4. add a simple audit candidate/token budget rule
