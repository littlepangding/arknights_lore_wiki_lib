# Codex Review 06 (Independent)

Scope: reviewed `/Users/dehuacheng/Claude/arknights/arknights_lore_wiki_lib/docs/DESIGN.md` against `/Users/dehuacheng/Claude/arknights/arknights_lore_wiki_lib/docs/REQUIREMENTS.md` only. I did **not** read the other files already under `docs/reviews/`.

## Findings

### [P1] One-character operator names are excluded from inferred appearances

`DESIGN.md` says the inferred-edge pass does regex search over stage text, but imposes a **2-character minimum** for Chinese aliases (`DESIGN.md:266-268`). That drops a real slice of the current operator corpus: there are 24 single-character operator display names in `character_table.json`, including major query targets such as `陈`, `年`, `夕`, `黑`, and `令`.

Because the deterministic layer only covers handbook storyset links, these operators would lose most of their event-appearance recall in `char appearances` / `event chars`, which directly weakens the Q/A goal and success criterion in `REQUIREMENTS.md:18-20` and `REQUIREMENTS.md:54-55`.

Recommendation: do not apply the 2-character floor to canonical operator display names. If noise is the concern, treat canonical names, curated aliases, and fuzzy aliases as separate classes with different matching rules.

### [P1] The audit design cannot reliably catch wrong attributions or hallucinations

The story-summary audit flow is currently defined as:

1. grep named entities from raw text,
2. grep the same entities from the summary,
3. diff the sets,
4. ask the LLM to inspect the remaining candidates (`DESIGN.md:534-543`).

That can surface some omissions, but it does **not** reliably catch two of the three promised failure classes from the requirements: **wrong attributions** and **hallucinations**. If the summary mentions the same entities as the raw text but assigns an action to the wrong character, or invents a relationship between already-mentioned entities, the entity-set diff will not flag it. It also misses scene omissions that do not introduce unique named entities.

That leaves the design short of the audit target in `REQUIREMENTS.md:18-20` and the explicit success criterion in `REQUIREMENTS.md:55`.

Recommendation: make claim-level or stage-level coverage comparison a first-class audit signal. Entity-diff is a useful heuristic, but it is not enough to be the primary audit contract.

### [P2] Curated alias enrichment is ambiguous for duplicate operator names

In enriched mode, the design says alias-file entries are attached to an operator whenever the alias-file canonical equals that operator's `name` (`DESIGN.md:244-245`, `DESIGN.md:282`). That join key is not unique in the current corpus. Among `char_*` entries alone, there are duplicate operator names, and at least one of them already collides with the curated alias data:

- `暮落` maps to both `char_512_aprot` and `char_4025_aprot2`
- `char_alias.txt` contains `暮落;沉渊`

Under the proposed rule, `沉渊` either gets attached to both operators or one is chosen arbitrarily. Both outcomes violate the "deterministic and testable where possible" principle in `REQUIREMENTS.md:24-25`, and they would make `resolve_operator_name()` less trustworthy exactly in the cases where users need alias help.

Recommendation: do not key curated alias enrichment by display name alone. If the alias source cannot be tied to `char_id`, then duplicate canonicals should stay unresolved and surface as ambiguous instead of being auto-attached.
