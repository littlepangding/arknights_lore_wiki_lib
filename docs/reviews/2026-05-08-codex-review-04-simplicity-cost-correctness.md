# Codex Review 04 — Simplicity, Token Cost, and KB Correctness

Date: 2026-05-08

Scope reviewed:
- `docs/DESIGN.md` (latest revision)
- `docs/AGENTS_GUIDE.md`
- `docs/DECISIONS.md`
- `docs/proposals/audit-lore-wiki-prior-spec.md`
- current measurable corpus properties from `ArknightsGameData`

This pass focuses on:

- simplicity of the KB design
- LLM token / cost exposure
- correctness of the KB semantics and the design doc itself

## Findings

### 1. Blocking correctness: the M4 measurement section is currently not reproducible, and the recorded result is wrong

The design says the measurement commands are "the source of truth for sizing decisions." See `docs/DESIGN.md:440`.

But the M4 snippet as written:

- iterates `ci.values()` without filtering
- calls `get_char_info_text_prompt(v)` on every entry

See `docs/DESIGN.md:430-434`.

That command currently crashes on entries without `name`, because `get_char_info_text_prompt()` expects `val['name']`.

I reproduced the failure directly. After adding the minimal guard `if v.get('name')`, the real result on the current corpus is:

- `chars_with_name = 444`
- `median = 5484`
- `p95 = 9016`
- `max = 11460`

The recorded result in the design is:

- `chars=~2900 median ~5500 p95 ~9000 max ~11500`

See `docs/DESIGN.md:438`.

So the doc currently has both:

1. a crashing "source-of-truth" measurement command
2. a materially incorrect recorded count

This should be fixed before implementation, because the design explicitly uses these measurements to justify token-size decisions.

### 2. Important cost/correctness: the `200 KB` single-pass summary threshold is too high for the actual corpus

The design currently says:

- summarize the whole event in one shot if total length is `<= 200 KB`

See `docs/DESIGN.md:331`.

Measured against the current corpus:

- `78` events exceed `50,000` chars
- `35` events exceed `100,000` chars
- `11` events exceed `150,000` chars
- only `2` events exceed `200,000` chars

That means the current threshold would still send almost every very large event through a single giant prompt. This is simple on paper, but it is not a good trade for either:

- **cost** — many large events still pay for one massive prompt
- **correctness** — longer inputs raise omission risk and format-drift risk

The threshold is therefore too permissive to be a good default.

Recommendation:

- lower the single-pass threshold substantially (something in the `80k-100k` range would at least force multi-pass on the truly large events), or
- trigger multi-pass on either `total_length > X` **or** `stage_count > Y`

The current rule is simple, but it is not cost-aware enough for this corpus.

### 3. Important simplicity/cost: per-character LLM summaries look like poor v1 value for their complexity

The design still includes:

- committed `kb_summaries/chars/<char_id>.md`
- per-char one-line summaries in the summary layer
- `kb_summarize.py [--target events|chars|all]`
- `kb_query summary char <char_id>`

See:

- `docs/DESIGN.md:108-114`
- `docs/DESIGN.md:336`
- `docs/DESIGN.md:466-472`
- `docs/DESIGN.md:489-490`
- `docs/AGENTS_GUIDE.md:15-17`
- `docs/AGENTS_GUIDE.md:79`

I don’t think that pays for itself in v1.

Why:

- the raw char data is already sectional and small
- measured current sizes for named chars are only:
  - median `5484`
  - max `11460`
- the agent guide already tells the agent to read only the needed section

So event summaries are doing heavy lifting, but char summaries are mostly duplicating information that is already:

- cheap to read directly
- better structured than a one-line abstraction
- partly duplicated again in `manifest.json` and `storysets.json`

This adds:

- extra LLM spend
- extra git churn
- extra interface surface
- another thing to keep stale-state logic for

with limited retrieval upside.

Recommendation:

- drop `kb_summaries/chars/` from v1 entirely, or
- keep char summarization as an explicit opt-in side path, but do not advertise it as a default part of the summary layer

If the goal is simplicity plus cost discipline, event summaries are the clear priority and char summaries are the easier thing to cut.

### 4. Important correctness: the design currently loses stage precision in one of its main cross-reference views

The deterministic edge source is stage-precise:

- `char_to_events_deterministic.json` stores `{event_id, stage_idx, story_set_name}`

See `docs/DESIGN.md:256-264`.

But the merged reverse view:

- `event_to_chars.json`

is documented only at event level, and its example drops `stage_idx` for deterministic edges:

- `{"char_id": "...", "source": "deterministic"}`

See `docs/DESIGN.md:271-279`.

The query API also returns:

- `event_chars(event_id, ...) -> list[CharMeta]`

which similarly loses the relation metadata. See `docs/DESIGN.md:313-315`.

That becomes a user-facing correctness issue in the guide, which says:

- `event chars <event_id> --source deterministic` helps find chars "guaranteed in that stage"

See `docs/AGENTS_GUIDE.md:92`.

But with the current API shape, that statement is too strong. Event-level reverse lookup is not the same as stage-level certainty.

Recommendation:

- preserve `stage_idx` in the `event_to_chars.json` relation objects for deterministic edges, and
- either:
  - return relation objects instead of plain `CharMeta` from `event_chars`, or
  - add a `stage_chars(event_id, stage_idx)` query explicitly

This would improve both correctness and token efficiency, because agents would have a tighter way to fetch only the relevant raw stage.

## What improved

- The alias scope is much more honest now.
- The operator-only resolver contract is a good simplification compared with the earlier over-general promise.
- Moving the dead audit skill into `docs/proposals/` is cleaner and safer.
- The family-based hierarchy is still the strongest part of the design.

## Review status

This revision is noticeably better than the last one, but I would still want the four issues above addressed before treating it as implementation-ready.

If I had to pick the one change that best improves all three axes at once:

- cut or demote per-char summaries in v1

That reduces complexity, token spend, and stale-state surface without weakening the core KB very much.
