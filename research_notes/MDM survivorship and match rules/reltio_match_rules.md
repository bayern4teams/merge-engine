# Reltio Match Rules and Match Tokenization (Blocking) — Technical Reference

> **Method / reliability note for the report writer.** All content below comes from
> `docs.reltio.com` public documentation pages, retrieved 2026-09-27 via an
> extraction tool that returns a *condensed* rendering of each page. Where text is
> inside quotation marks, it is reproduced as the extraction returned it and is
> quoted doc prose. **JSON blocks are reproduced as the pages present them and are
> the highest-fidelity artefacts here** — class names, operator keys and field names
> in the JSON should be treated as verbatim. Prose bullets paraphrase the page.
> Two sources could NOT be accessed and are listed in Gaps:
> `support.reltio.com/hc/en-us/articles/4403208429453-Anatomy-of-a-Match-Rule`
> returned **HTTP 403 Forbidden**, and
> `support.reltio.com/hc/en-us/articles/40163764199565-Practical-Guide-for-choosing-and-testing-Token-strategies-and-comparison-classes`
> was never fetched (same host, presumed gated). `docs.reltio.com/matchmerge/comparatorclasses.html`
> returned **404**; the current-generation page was used instead.

---

## Q1. How is a match rule structured in Reltio? Components and rule types

### Takeaway
A Reltio match rule is a JSON **match group** attached to an entity type
(`configuration/entityTypes/<Type>/matchGroups/<Name>`). Every match group carries a
`type` (which decides the *action*: auto-merge vs. queue for review), and a `rule`
object that holds three orthogonal concerns: **`cleanse`** (pre-processing),
**`matchTokenClasses`** (blocking / candidate generation), and
**`comparatorClasses`** + boolean **comparison operators** (`and`/`or`/`exact`/
`fuzzy`/`equals`/…) which form the comparison formula. A match group may instead
carry a **`negativeRule`** rather than a `rule`.

### Cited Findings
- A match rule is "a set of criteria that determines whether different records actually refer to the same entity." — [Configure match rules overview](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview)
- Match group elements and their exact JSON names — [Match Group Elements - Description and Configuration](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration):
  - `uri` — unique identifier across match groups, e.g. `configuration/entityTypes/Individual/matchGroups/Rule1`
  - `label` — "Meaningful label" displayed as a rule summary
  - `type` — valid values: **`automatic`** ("Boolean formula; issues merge directives"), **`suspect`** ("Boolean formula; queues for review"), a user-defined **`<custom>`** type "with custom match actions", and **`relevance_based`** ("Arithmetic scoring algorithm")
  - `scope` — `ALL` (default), `NONE`, `INTERNAL`, `EXTERNAL`
  - `useOvOnly` — boolean, default `false`; when true "only the OV of each attribute will be used for tokenization and comparisons" (OV = operational value / survivorship winner)
  - `rule` — the comparison logic, boolean or arithmetic depending on `type`
  - `DocumentComparator` — "Required; handles document-level comparisons"
  - `matchServiceClass` — "Required; specifies the matching service implementation"
  - `overrides` — maps method attributes to data-model attributes via `attributesMapping`
  - That page did **not** list `scoreStandalone`, `scoreIncremental`, `ignoreInToken` or `maxMatches`, although `scoreStandalone`/`scoreIncremental`/`ignoreInToken` appear in every published example JSON (see Q4).
- Inside `rule`, the observed top-level keys across all five published examples are: `cleanse`, `matchTokenClasses` (with a `mapping` array of `{attribute, class, parameters}`), `comparatorClasses` (same shape), and a boolean tree rooted at `and` (which may nest a further `and` or `or`). — [Example 1](https://docs.reltio.com/en/model/consolidate-data/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-1---simple-rule-with-reltio-name-dictionary), [Example 2](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-2---using-a-custom-comparator-and-token-class)
- Every example JSON terminates with `"scoreStandalone": <int>, "scoreIncremental": <int>` outside the `rule` object. In Examples 1–3 both are `0`; in Example 4 they are `25`/`25` (rule 1) and `50`/`25` (rule 2). — [Example 4](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-4---two-rules-one-a-fuzzier-version-than-the-other)
- Cleansers are attached per-rule via `cleanse: [{cleanseAdapter, cleanseAdapterParams, mappings}]`. Observed adapters, verbatim: `com.reltio.cleanse.impl.NameDictionaryCleanser` and `com.reltio.cleanse.impl.RegexpReplaceCleanser` (params `dictionary` = an S3 URL, `keepOriginalValue`). Each `mappings` entry is `{attribute, mandatory, allValues, cleanseAttribute}`. — [Example 3 Rule 1](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-3---four-rules-working-together/example-3---individual-entity-type-rule-1)
- The doc names two remediation cleansers by product name: "String Replacement Cleanser" and "Name Dictionary Cleanser". — [Design your Comparison Formulas](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/design-your-comparison-formulas)
- Rules can be authored either by "JSON editing of tenant configuration or the Match Rule Editor within the Data Modeler", where the editor's "Advanced Editor" handles functionality the standard builder doesn't support. — [Testing and Tuning your Match Rule Design](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/testing-and-tuning-your-match-rule-design)

### Inferences
- The architecture cleanly separates the three stages a home-grown matcher must also
  separate: **normalize (cleanse) → block (matchTokenClasses) → score/decide
  (comparatorClasses + boolean formula) → act (`type`)**. Notably the *decision* is
  not a score threshold in the boolean rule types; it is the rule's `type`. The
  formula is a predicate, and which predicate fired determines auto-merge vs. review.
- `useOvOnly` is a meaningful design lever: matching against *all* crosswalk values
  vs. only the survived value changes both recall and cost. A home-grown matcher
  that only ever compares the golden record is implicitly `useOvOnly: true`.
- `scoreStandalone`/`scoreIncremental` being `0` in the boolean examples and non-zero
  in Example 4 suggests they are advisory confidence figures surfaced to stewards
  rather than the gating mechanism for boolean rules.

### Gaps
- Exact semantics of `scoreStandalone` vs. `scoreIncremental` are not stated on any
  page retrieved; the match-group-elements page omitted them entirely.
- `DocumentComparator` and `matchServiceClass` are described as "required" but no
  page retrieved shows their values, and they do not appear in any published example
  JSON — so their defaults/omissibility is unverified.
- The `<custom>` rule type with "custom match actions" is named but its configuration
  is not documented on any page retrieved.

---

## Q2. What is a match tokenization scheme? Token classes and how blocking works

### Takeaway
Tokenization **is** Reltio's blocking mechanism. Each rule attribute is mapped to a
`matchTokenClass`; each class emits one or more tokens per attribute value; tokens are
then **cross-multiplied across attributes into "token phrases"**, and two profiles
become a *match candidate pair* iff they share at least one identical token phrase.
Comparators only ever run on candidate pairs. Reltio publishes 15 token classes.

### Cited Findings — mechanism
- "Match tokens identify candidates for matching by the specified match rules." — [Design your Match Tokenization Scheme](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/design-your-match-tokenization-scheme)
- Purpose: "Tokenization avoids the need to compare every possible pair of records (out of perhaps millions of records) by identifying 'match candidate pairs' in your tenant that are 'close enough' to be used when evaluating match rules." — [Match Token Classes](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/match-token-class/match-token-classes)
- Tokens "individually and collectively represent the profile mathematically in various ways so that other profiles in your tenant with any of the same mathematical representations are easily identified as match candidates." — [Match Token Generation](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/match-token-class/match-token-generation)
- Tokens are either plain strings pulled from the attribute (e.g. `william`) or encoded representations (e.g. `YG-76`) standing for phonetic sounds. — [Match Token Generation](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/match-token-class/match-token-generation)
- **The blocking key is a token *phrase*, not a token**: "the engine uses token phrases which are the combination of tokens, to constrain the candidates it attracts to just meaningful candidates." Example phrase given: `<elizabeth:smith>` — "Any other profile containing this identical phrase becomes a match candidate." — [Match Token Generation](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/match-token-class/match-token-generation)
- **Cartesian blow-up formula.** Applying a tokenization scheme to a profile yields token phrases numbering `[all values for attribute 1] × [all values for attribute 2] × … × [all values for attribute n]`. — [Design your Match Tokenization Scheme](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/design-your-match-tokenization-scheme)
  - Consequence stated: single-value attributes (SSN, zip code, suffix) "multiply by 1, so they're safe to include"; attributes with aggregated multiple values, fuzzy classes, or dictionary cleansers multiply token phrases significantly.
- **Hard cap on block size**: "The system caps candidates per token phrase at 300 profiles to prevent performance degradation." — [Match Token Generation](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/match-token-class/match-token-generation)
- Direction of the two knobs: adding more attributes to a rule **decreases** candidate volume (all parts of the phrase must match), while fuzzy token classes **expand** candidates by matching variant spellings. — [Match Token Generation](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/match-token-class/match-token-generation)
- **Token/comparator alignment is the central correctness rule**: "a comparator class and matchToken class that are aligned" prevents mismatches "where tokens attract candidates that the comparator logic then rejects." — [Match Token Generation](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/match-token-class/match-token-generation)
- Restated as best practice: "Match Token classes are designed to mimic the same strategies as the comparator classes. So if you're using a SoundexComparator for an attribute, and you determine that tokenizing that attribute is important, then you'll likely want to use the SoundexTextMatchToken class for it." Also: "fuzzy match token classes should be avoided unless necessary — they can generate numerous tokens and slow down candidate retrieval." — [Match Token Classes](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/match-token-class/match-token-classes)
- The stated trade-off, with Reltio's opinion on which side to err: "Adding attributes to your tokenization scheme, especially those that have a fuzzy match token class increases the number of token phrases, yet generally reduces the number of match candidate pairs placed into the match table. However, most agree that it is more important from a performance perspective to reduce the number of token phrases, even at the expense of increasing the number of match candidate pairs returned." — [Design your Match Tokenization Scheme](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/design-your-match-tokenization-scheme)

### Cited Findings — the six-step scheme design method
From [Design your Match Tokenization Scheme](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/design-your-match-tokenization-scheme). Stated objective is dual: (1) map attributes efficiently to identify the right number of candidates that will evaluate true by the comparison formula, and (2) "Maximize reusability of token class mappings across rules to minimize variations in token phrase definitions".
1. Order attributes by their ability to narrow scope (given order: **SSN → Email → Street Address → Last Name**, etc.)
2. Include attributes mapped to fuzzy comparator classes to "cast a wider net"
3. Review cross-rule reusability to minimize the overall token-phrase population
4. Assign recommended match token classes **matching each attribute's comparator class**
5. Create custom comparators/token classes for parameter overrides or stemmers
6. Apply **`ignoreInToken`** to all remaining attributes "to prevent default class assignment"

### Cited Findings — the 15 match token classes (names verbatim)
All from [Match Token Classes](https://docs.reltio.com/matchmerge/matchtokenclasses.html) (mirrored at the [current-gen page](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/match-token-class/match-token-classes)). Java package for all of these, per the example JSON, is `com.reltio.match.token.<ClassName>`.

| Token class (verbatim) | What it does | # tokens | Non-Latin |
|---|---|---|---|
| `ExactMatchToken` | Single token from lowercased value, keeping only letters, digits and spaces. "John B" → `john b`. Behaves the same under Exact and Fuzzy operators. | 1 | yes |
| `ExactNumberMatchToken` | "This class generates a match token that represents the numeric characters from the string." `ACL89786291D` → `89786291`. | 1 | yes |
| `SoundexTextMatchToken` | Phonetic token via the **Soundex** algorithm. Identical behaviour for Fuzzy and Exact. | 1 | no |
| `DoubleMetaphoneMatchToken` | "This class is based on the Double Metaphone algorithm that can generate two match tokens, a primary and a secondary code for a string." Secondary covers ambiguous cases such as character ordering. | 2 | no |
| `FuzzyTextMatchToken` | "This class generates a token for each common spelling up to a maximum of six tokens." e.g. "Michael" → tokens for *Michale*, *Michel*, *Micheal*. | ≤6 | no |
| `DictionaryStatsPhoneticFuzzyToken` | Metaphone tokens **plus** character-frequency-based tokens, to catch misspellings and duplicated characters. Behaves *differently* for Fuzzy vs Exact operators. | ≤2 | no |
| `ComplexPhoneticNameToken` | "works in the same way as the `DictionaryStatsPhoneticFuzzyToken` class but selects only letters." | ≤2 | no |
| `AddressLineMatchToken` | For AddressLine1: lowercase → split into words → remove numbers → filter garbage words → sort alphabetically. | 1 | yes |
| `OrganizationNameMatchToken` | Splits org name into words, removes noise terms such as "inc" and "limited", generates a primary token from sorted/stemmized words **plus pairwise combinations**. | n (excl. noise words) | — |
| `RangeNumericMatchToken` | Numeric ranges via threshold parameters. "If the attribute value is identified for an Exact match, this class generates a token formatted to have four digits after the decimal." | 1–2 | — |
| `BasicTokenizedOrganizationNameMatchToken` | Like `OrganizationNameMatchToken` but **no stemming**, limited delimiters, shorter noise dictionary. | — | — |
| `CustomMatchToken` | "This match token class is like a container for unlimited groups of other match token classes." Config-level params `combineBy`, `limit`; per-group settings include patterns, transliteration and custom word replacements. | — | — |
| `DistinctWordsMatchToken` | "This match token class generates tokens by distinct words based on the parameters specified." Tokens from word subsets meeting a threshold; **defaults to 50% threshold, up to 1,000 tokens per value**. | ≤1000 | — |
| `VolumeMatchToken` | "Enables you to extract volumes from attribute values and generate tokens based on these extracted values." Converts ml, l, g, kg; supports tolerance for acceptable differences. | — | — |
| `RangeNumericMatchToken` / `VolumeMatchToken` are the parameterised numeric pair; see comparators of the same names. | | | |

Additionally, `SoundexTextExactMatchToken` is named on the Match Token Generation page as a fuzzy-token example. It does **not** appear in the token-class table, so treat that name as unconfirmed (possible doc error for `SoundexTextMatchToken`).

### Inferences
- Reltio's blocking is **conjunctive multi-attribute blocking with per-attribute fuzzy
  key expansion** — i.e. classic sorted-neighbourhood/canopy blocking generalised so
  that each blocking field can expand to several keys (Double Metaphone → 2, Fuzzy
  text → 6, DistinctWords → up to 1000) and the block key is the cross product.
  A home-grown matcher using single-field blocking keys is strictly weaker; one using
  *disjunctive* blocking (union over several single-field keys) has different
  precision/recall characteristics than Reltio's per-rule conjunctive phrase.
- The 300-profile cap per token phrase is a **silent recall cliff**: any block wider
  than 300 (e.g. `<john:smith>`) truncates, so pairs are dropped without any rule
  evaluating false. Directly relevant to evaluating a home-grown matcher that reports
  "0 uncompared pairs" — Reltio's own engine would not.
- `ignoreInToken` implies that *by default every rule attribute is tokenized* with a
  default class; blocking is opt-out, not opt-in. That is the opposite of most
  hand-built matchers, where blocking is opt-in.

### Gaps
- The exact default token class applied when an attribute is neither mapped nor listed
  in `ignoreInToken` is not named on any page retrieved.
- Whether the 300-cap is per token phrase per rule or global, and whether it is
  tenant-configurable, is not stated.
- `CustomMatchToken`'s `combineBy` and `limit` parameter value sets are not enumerated
  on the pages retrieved.

---

## Q3. Comparator classes and how comparison formulas combine

### Takeaway
Comparators are Java classes mapped per attribute; they answer a boolean "are these
two values the same?" (and, for relevance-based rules, a similarity score). The formula
itself is a nested boolean tree of **operator keys** whose *operands are attribute URI
lists* — so `and`/`or` combine **operator groups**, not individual predicates. There
are no per-attribute weights in the boolean rule types; weights exist only in
`relevance_based` matching.

### Cited Findings — comparator classes (verbatim names, `com.reltio.match.comparator.*`)
From [Comparator Classes](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/comparator-classes). Reltio's own framing: comparator classes "define the behavior of comparison operators", each one both deciding comparability (a comparison algorithm) and calculating similarity (a relevance calculation).

| Comparator (verbatim) | Description | Parameters | Parameterised |
|---|---|---|---|
| `BasicStringComparator` | "treats the attribute values as strings and returns `true` if the strings are identical" | — | No |
| `DamerauLevenshteinDistance` | minimum single-character operations to convert one string to the other | `thresholds` (by string length or percentage) | Yes |
| `DynamicDamerauLevenshteinDistance` | as above but "supports greater operations on longer strings" | `thresholds` | Yes |
| `MetaphoneComparator` | "returns `true` if the two strings are phonetically equal based on the Metaphone algorithm" | — | No |
| `DoubleMetaphoneComparator` | "…based on the Double Metaphone algorithm" | — | No |
| `SoundexComparator` | "…based on the Soundex algorithm" | — | No |
| `StringCharactersComparator` | "strips the two strings of all non-alphabetic characters" then compares | — | No |
| `StringComparatorIgnoringNulls` | "returns `true` if the strings are identical AND both strings are non-zero in length" | — | No |
| `PhoneNumberComparator` | strips non-numerics, requires 10 digits, compares the rightmost 10 characters | `noiseDictionary` | Yes |
| `OrganizationNamesComparator` | parses org name into words; true if **≥60%** of words match | — | No |
| `AddressLineComparator` | normalizes address lines, removes noise words, compares word sets by the formula **`N/(M1+M2-N) ≥ 0.6`** | — | No |
| `RangeNumericComparator` | "Returns `true` if the difference in magnitude between two values falls within a specified range" | `threshold` (absolute or percentage) | Yes |
| `BasicTokenizedOrganizationNameComparator` | normalizes org names, removes garbage words, matches if **≥60%** of words are identical | — | No |
| `CustomComparator` | "container for unlimited groups of other comparators" with configurable preprocessing | `className`, `classParams`, `pattern`, `splitByWordsBoundaries`, `acceptMissingBoth`, `acceptMissingOneOf`, `transliterate`, `transliterateCommand`, `TransliterateCleanser`, `wordReplacements`, `noiseDictionary`, `useNoiseIfEmpty`, `useStemmer`, `useSoundex`, `wordDelimiter`, `sortWords` | Yes |
| `DistinctWordsComparator` | compares values by distinct words; includes optional character-histogram comparison | `pattern`, `threshold`, `thresholdChars`, `splitByWordsBoundaries`, `wordReplacements`, `substringComparison`, `wordDistance`, `transliterate`, `transliterateCommand`, `TransliterateCleanser`, `noiseDictionary`, `useNoiseIfEmpty`, `useStemmer`, `useSoundex` | Yes |
| `ExactMultiComparator` | "accepts two lists of values from the two entities"; matches if both lists contain only one distinct identical value | — | No |
| `CrossMultiComparator` | compares multiple attributes that could be mixed up; calculates relevance across combinations | — | No |
| `ProximateGeoComparator` | "define a maximum distance between two locations that is considered semantically the same" | `distance_miles` | Yes |
| `ExactOrNullCrossMultiComparator` | compares mixed attributes where one can be null; **"does not produce match tokens"** | — | No |
| `JaroWinklerComparator` | Apache Commons Jaro-Winkler similarity | `similarityThreshold` | Yes |
| `VolumeComparator` | extracts and compares volume measurements, converting to millilitres | `acceptMissingOneof`, `acceptMissingBoth`, `toleranceLevel` | Yes |

### Cited Findings — comparison operators
From [Comparison Operators](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/the-comparison-formula/comparison-operators), which enumerates six:
- **`exact`** — "ensures a comparison returns `true` when the tested values match exactly as defined by the assigned comparator class". (Note: "exactly" means *as the comparator defines equality* — a `SoundexComparator` under `exact` is still phonetic.)
- **`fuzzy`** — "indicates that the comparison for the attribute is considered `true` if any values being tested are similar, but not necessarily identical"
- **`exactOrNull`** — true if both values match exactly, **or** one has a value and the other doesn't, **or** both are empty
- **`exactOrAllNull`** — true if both entities have identical values **OR** both entities lack values
- **`notExact`** — "negates the behavior of the `exact` operator while preserving the connection between nested attribute values"; returns false for matching values, true for differences
- **`multi`** — "considers more than one attribute when matching entities"; for detecting swapped or permuted values across fields
- **Token-generation caveat, verbatim in effect:** `exactOrNull`, `exactOrAllNull` and `notExact` **do not produce match tokens**. `exact` and `fuzzy` differ slightly in token generation but use the same comparison logic.
- That page did **not** mention `equals`, `notEquals`, `notExactSame`, `nullValues` or `ignoreInToken` — yet all of these appear in the published example JSON and on the Negative Rule page. **Flagged conflict / incompleteness in the operator page.**

### Cited Findings — operators observed in example JSON (verbatim keys)
- `exact`, `fuzzy` — attribute URI lists.
- `equals: [{ "values": [...], "uri": <attributeUri> }]` — a **filter/guard** restricting the rule to profiles whose attribute holds one of the listed literals. E.g. Example 1 requires `HCPType == "Physician"`; Example 3 Rule 1/Rule 3 require `AddressType ∈ {"Bill Location","BIL"}`; Example 2 requires `AddressType == "Shipping"`.
- `notEquals: [{ "values": [...], "uri": ... }]` — the inverse guard. Example 2 excludes `OrgJourneyStatus ∈ {"Generic Account","House Account"}`.
- `ignoreInToken: [ <attributeUri>, … ]` — suppress tokenization of listed attributes while still comparing them.
- `exactOrNull`, `exactOrAllNull` — null-tolerant equality.
- `nullValues: [{ "attribute": ..., "value": "false" }]` — declares a literal to be *treated as null*. Example 4 uses it so that `IsCurrent == false` is coerced to null and then absorbed by `exactOrAllNull`.
- Nesting: `and` may contain a nested `and` or `or`. Example 2 has `and { … "or": { "exactOrNull": [DUNSNumber], "exactOrAllNull": [DUNSNumber] } }`; Example 4 has `and { exact, fuzzy, and { exactOrAllNull, nullValues } }`.
- Sources: [Example 1](https://docs.reltio.com/en/model/consolidate-data/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-1---simple-rule-with-reltio-name-dictionary), [Example 2](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-2---using-a-custom-comparator-and-token-class), [Example 3 Rule 1](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-3---four-rules-working-together/example-3---individual-entity-type-rule-1), [Example 4](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-4---two-rules-one-a-fuzzier-version-than-the-other)

### Cited Findings — weights and scoring
- Boolean rules have **no attribute weights**. Weighted/arithmetic scoring is a separate rule type: "Relevance-Based Matching uses an arithmetic scoring approach to rule design (rather than a boolean evaluation)", offered as the way to reduce rule count. — [Design your Comparison Formulas](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/design-your-comparison-formulas)
- Rule type `relevance_based` is the corresponding `type` value; a dedicated page exists at [Configuring relevance-based matching rules](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/configuring-relevance-based-matching-rules) (not fetched — see Gaps).
- Thresholds live **inside comparators**, not in the formula: `DistinctWordsComparator` `threshold: "45%"`, `RangeNumericComparator` `threshold: "2"`, `JaroWinklerComparator` `similarityThreshold`, `ProximateGeoComparator` `distance_miles`, `DamerauLevenshteinDistance` `thresholds` by length or percentage. — [Comparator Classes](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/comparator-classes), [Example 2](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-2---using-a-custom-comparator-and-token-class), [Example 4](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-4---two-rules-one-a-fuzzier-version-than-the-other)

### Inferences
- The design decision worth copying: **a threshold is a property of a comparator, not of
  a rule.** That makes a comparator+threshold pair reusable and testable in isolation,
  and keeps the rule a pure boolean predicate.
- `exactOrNull` vs `exactOrAllNull` is a distinction most hand-built matchers collapse.
  `exactOrNull` treats "present vs absent" as agreement (maximally permissive);
  `exactOrAllNull` only treats "both absent" as agreement. Getting this wrong is a
  classic false-positive source: `exactOrNull` on SSN means a record with no SSN
  matches everyone.
- That `exactOrNull`/`exactOrAllNull`/`notExact` generate **no tokens** means a rule
  built only from those operators would have no blocking key at all — so every rule
  needs at least one `exact`/`fuzzy` attribute to be tokenizable. Example 2's `or`
  branch on `DUNSNumber` is exactly such a non-tokenizing branch.
- `ExactOrNullCrossMultiComparator` being documented as "does not produce match tokens"
  confirms that comparator choice can itself silently remove a blocking key.

### Gaps
- `relevance_based` rule configuration (score fields, per-attribute weights, score
  thresholds for auto-merge vs review) — page identified but not retrieved.
- The `multi` operator's JSON shape is not shown in any example retrieved.
- The operator page's omission of `equals`/`notEquals`/`nullValues`/`ignoreInToken`
  means their formal semantics are inferred from example JSON only.

---

## Q4. The five illustrative examples, in detail

### Takeaway
The published examples are real customer configurations. They show four recurring
tactics: (a) pair a fuzzy comparator with the *matching* fuzzy token class; (b) add a
synonym/dictionary cleanser to widen semantic tolerance without widening tokens;
(c) use `ignoreInToken` to move blocking off the fuzzy field onto a high-selectivity
exact field; (d) pair an `automatic` strict rule with a `suspect` loosened twin.

### Example 1 — Simple Rule with Reltio Name Dictionary
Source: [Example 1](https://docs.reltio.com/en/model/consolidate-data/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-1---simple-rule-with-reltio-name-dictionary)

Stated characteristics: "Uses the in-built Reltio Name Dictionary for first names";
"Uses a phonetic (Double Metaphone) strategy for first names"; "Together, the synonym
dictionary and the phonetic strategy will produce a rich amount of tolerance for first
name variations still considered semantically the same."

```json
{
  "uri": "configuration/entityTypes/HCP/matchGroups/MatchonFirstNameandLastName",
  "label": "Match on FirstName and LastName",
  "type": "suspect",
  "scope": "ALL",
  "useOvOnly": "true",
  "rule": {
    "cleanse": [{"cleanseAdapter": "com.reltio.cleanse.impl.NameDictionaryCleanser"}],
    "matchTokenClasses": {"mapping": [{"attribute": "configuration/entityTypes/HCP/attributes/FirstName", "class": "com.reltio.match.token.DoubleMetaphoneMatchToken"}]},
    "comparatorClasses": {"mapping": [{"attribute": "configuration/entityTypes/HCP/attributes/FirstName", "class": "com.reltio.match.comparator.DoubleMetaphoneComparator"}]},
    "and": {"exact": ["configuration/entityTypes/HCP/attributes/LastName"], "equals": [{"values": ["Physician"], "uri": "configuration/entityTypes/HCP/attributes/HCPType"}], "fuzzy": ["configuration/entityTypes/HCP/attributes/FirstName"]}
  },
  "scoreStandalone": 0,
  "scoreIncremental": 0
}
```

**Why designed this way.** Two layers of first-name tolerance stacked on one field:
the `NameDictionaryCleanser` handles *semantic* variants (Bob/Robert — not phonetically
similar), and `DoubleMetaphoneMatchToken`/`DoubleMetaphoneComparator` handle *phonetic*
variants (Jon/John). Crucially the token class and comparator are the **same
algorithm**, so the block and the scorer agree. `LastName` is `exact` and provides the
selective half of the token phrase; `equals` on `HCPType == "Physician"` is a scope
filter so the rule never fires on non-physician records. `type: "suspect"` means this
fires only into the review queue, not an auto-merge. `useOvOnly: "true"` restricts
tokenization and comparison to the survived value.

### Example 2 — Using a Custom Comparator and Token Class
Source: [Example 2](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-2---using-a-custom-comparator-and-token-class)

Stated characteristics: custom comparator class **and** custom match token class for
Organization Name, both built on `DistinctWords`; "Uses the Stemmer and Soundex
functions within the custom classes and uses a custom noise words dictionary"; "Uses
exact match on five attributes and fuzzy on one attribute, and only operates on
profiles where the address type = Shipping and OrgJourneyStatus is not Generic Account
or House Account"; token suppression on four attributes deemed unhelpful for matching;
and — noted by Reltio as **redundancy** — both `exactOrNull` and `exactOrAllNull` on
`DUNSNumber` when "only one [is] needed".

```json
{
  "uri": "configuration/entityTypes/Organization/matchGroups/FuzzyNameandExactAddressPhone",
  "label": "(Fuzzy) Name, (Exact) AddressLine1, City, State, Phone Number, (Equals) Shipping",
  "type": "suspect",
  "scope": "ALL",
  "useOvOnly": "true",
  "rule": {
    "matchTokenClasses": {
      "mapping": [
        {
          "attribute": "configuration/entityTypes/Organization/attributes/Name",
          "parameters": [
            {
              "parameter": "groups",
              "values": [
                {
                  "classParams": {"threshold": "45%", "useStemmer": "true", "useSoundex": "true"},
                  "className": "com.reltio.match.token.DistinctWordsMatchToken",
                  "noiseDictionary": "https://s3.amazonaws.com/{your-s3-bucket}/Acme/AcmeNoiseDictionary.txt"
                }
              ]
            }
          ],
          "class": "com.reltio.match.token.CustomMatchToken"
        }
      ]
    },
    "comparatorClasses": {
      "mapping": [
        {
          "attribute": "configuration/entityTypes/Organization/attributes/Name",
          "parameters": [
            {
              "parameter": "groups",
              "values": [
                {
                  "classParams": {"threshold": "45%", "useStemmer": "true", "useSoundex": "true"},
                  "className": "com.reltio.match.comparator.DistinctWordsComparator",
                  "noiseDictionary": "https://s3.amazonaws.com/{your-s3-bucket}/Acme/AcmeNoiseDictionary.txt"
                }
              ]
            }
          ],
          "class": "com.reltio.match.comparator.CustomComparator"
        }
      ]
    },
    "and": {
      "exact": [
        "configuration/entityTypes/Organization/attributes/Addresses/attributes/AddressLine1",
        "configuration/entityTypes/Organization/attributes/Addresses/attributes/City",
        "configuration/entityTypes/Organization/attributes/Addresses/attributes/StateProvince",
        "configuration/entityTypes/Organization/attributes/Phone/attributes/Number",
        "configuration/entityTypes/Organization/attributes/Addresses/attributes/AddressType"
      ],
      "equals": [
        {"values": ["Shipping"], "uri": "configuration/entityTypes/Organization/attributes/Addresses/attributes/AddressType"}
      ],
      "fuzzy": ["configuration/entityTypes/Organization/attributes/Name"],
      "ignoreInToken": [
        "configuration/entityTypes/Organization/attributes/Phone/attributes/Number",
        "configuration/entityTypes/Organization/attributes/Addresses/attributes/AddressType",
        "configuration/entityTypes/Organization/attributes/BuyerLifecycleStatus/attributes/OrgJourneyStatus",
        "configuration/entityTypes/Organization/attributes/Addresses/attributes/StateProvince"
      ],
      "notEquals": [
        {"values": ["Generic Account", "House Account"], "uri": "configuration/entityTypes/Organization/attributes/BuyerLifecycleStatus/attributes/OrgJourneyStatus"}
      ],
      "or": {
        "exactOrNull": ["configuration/entityTypes/Organization/attributes/DUNSNumber"],
        "exactOrAllNull": ["configuration/entityTypes/Organization/attributes/DUNSNumber"]
      }
    }
  },
  "scoreStandalone": 0,
  "scoreIncremental": 0
}
```

**Why designed this way.** The **symmetry is the point**: the identical `classParams`
(`threshold: "45%"`, `useStemmer`, `useSoundex`) and the identical `noiseDictionary`
URL are given to both the `CustomMatchToken`/`DistinctWordsMatchToken` block *and* the
`CustomComparator`/`DistinctWordsComparator` scorer, so the blocker and the scorer use
the same notion of "same org name". A 45% distinct-word threshold is deliberately
permissive for messy multi-word org names ("Acme Manufacturing Co" vs "Acme Mfg");
stemming plus Soundex plus a customer-supplied noise dictionary strips legal suffixes
and industry filler. The four `ignoreInToken` entries are low-selectivity fields
(`StateProvince` has few distinct values; `AddressType` fewer still) whose inclusion in
the token phrase would multiply phrases while barely narrowing candidates. The
`equals`/`notEquals` pair is pure scoping: only shipping addresses, never generic or
house accounts — Reltio's own answer to the "everybody matches the catch-all account"
false-positive pattern.

### Example 3 — Four Rules Working Together (Individual entity type)
Parent page: [Example 3 - Four Rules Working Together](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-3---four-rules-working-together) — "Please review the example set of four match rules for the Individual entity type. It is instructive to review an entire grouping of rules to understand holistically the strategy, yet also the tactics of each rule."

**The shared skeleton across all four.** Same fuzzy fields (`FirstName`, `LastName`),
same comparator (`DamerauLevenshteinDistance`), same token class
(`FuzzyTextMatchToken`), same `ignoreInToken` on both names, same
`NameDictionaryCleanser`. What varies is **the exact anchor**: address triple → email →
zip only → phone. The anchor's selectivity determines the `type`.

| Rule | Anchor (`exact`) | Fuzzy | `type` | Extra |
|---|---|---|---|---|
| Rule1 | AddressLine1 + Zip5 + StateProvince | FirstName, LastName | `automatic` | `equals` AddressType ∈ {Bill Location, BIL}; `RegexpReplaceCleanser` on AddressLine1 |
| Rule2 | Email | FirstName, LastName | `automatic` | `RegexpReplaceCleanser` on Email |
| Rule3 | Zip5 only | FirstName, LastName, **AddressLine1** | `suspect` | `equals` AddressType ∈ {Bill Location, BIL}; `AddressLineMatchToken` + `AddressLineComparator` |
| Rule4 | Phone Number | FirstName, LastName | `suspect` | `RegexpReplaceCleanser` on Phone |

**Rule 1 — `Rule1(auto): Fuzzy(FirstName, LastName) and Exact(AddressLine1, Zip5, State)`**
([source](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-3---four-rules-working-together/example-3---individual-entity-type-rule-1))

```json
{
  "uri": "configuration/entityTypes/Individual/matchGroups/Rule1",
  "label": "Rule1(auto): Fuzzy(FirstName, LastName) and Exact(AddressLine1, Zip5, State)",
  "type": "automatic",
  "rule": {
    "and": {
      "exact": [
        "configuration/entityTypes/Individual/attributes/Address/attributes/AddressLine1",
        "configuration/entityTypes/Individual/attributes/Address/attributes/Zip/attributes/Zip5",
        "configuration/entityTypes/Individual/attributes/Address/attributes/StateProvince"
      ],
      "equals": [
        {"values": ["Bill Location", "BIL"], "uri": "configuration/entityTypes/Individual/attributes/Address/attributes/AddressType"}
      ],
      "fuzzy": [
        "configuration/entityTypes/Individual/attributes/FirstName",
        "configuration/entityTypes/Individual/attributes/LastName"
      ],
      "ignoreInToken": [
        "configuration/entityTypes/Individual/attributes/FirstName",
        "configuration/entityTypes/Individual/attributes/LastName"
      ],
      "cleanse": [
        {
          "cleanseAdapter": "com.reltio.cleanse.impl.NameDictionaryCleanser",
          "mappings": [
            {"attribute": "configuration/entityTypes/Individual/attributes/FirstName", "mandatory": false, "allValues": false, "cleanseAttribute": "configuration/entityTypes/Individual/attributes/FirstName"},
            {"attribute": "configuration/entityTypes/Individual/attributes/LastName", "mandatory": false, "allValues": false, "cleanseAttribute": "configuration/entityTypes/Individual/attributes/LastName"}
          ]
        },
        {
          "cleanseAdapter": "com.reltio.cleanse.impl.RegexpReplaceCleanser",
          "cleanseAdapterParams": {"dictionary": "https://reltio-customers.s3.amazonaws.com/Acme/Match/replace-AddressLine1.txt", "keepOriginalValue": "false"},
          "mappings": [
            {"attribute": "configuration/entityTypes/Individual/attributes/Address/attributes/AddressLine1", "cleanseAttribute": "configuration/entityTypes/Individual/attributes/Address/attributes/AddressLine1"}
          ]
        }
      ],
      "matchTokenClasses": {
        "mapping": [
          {"attribute": "configuration/entityTypes/Individual/attributes/FirstName", "class": "com.reltio.match.token.FuzzyTextMatchToken"},
          {"attribute": "configuration/entityTypes/Individual/attributes/LastName", "class": "com.reltio.match.token.FuzzyTextMatchToken"}
        ]
      },
      "comparatorClasses": {
        "mapping": [
          {"attribute": "configuration/entityTypes/Individual/attributes/FirstName", "class": "com.reltio.match.comparator.DamerauLevenshteinDistance"},
          {"attribute": "configuration/entityTypes/Individual/attributes/LastName", "class": "com.reltio.match.comparator.DamerauLevenshteinDistance"}
        ]
      }
    }
  },
  "scoreStandalone": 0,
  "scoreIncremental": 0
}
```
Stated characteristics: tactic is "Exact address and fuzzy name comparison"; rule type
automatic, "triggers record merging without override capability"; "Uses built-in Reltio
Name Dictionary for **both first and last names**"; "Tokenization: Suppressed on first
and last name fields via `ignoreInToken`"; custom replacement dictionary on
AddressLine1; Damerau-Levenshtein for fuzzy. Reltio explicitly flags a customer
oddity: the Name Dictionary was applied to *last* names "despite it containing only
first names".

*Why:* a full exact address (line1 + zip5 + state), restricted to billing addresses, is
selective enough that a fuzzy name on top is safe to auto-merge. The names are
deliberately *not* tokenized — the address triple carries the whole token phrase, which
keeps phrase count down (names would multiply by up to 6 each under
`FuzzyTextMatchToken`) while names are still fuzzily compared.

**Rule 2 — `Rule2(auto): Fuzzy(FirstName, LastName) and Exact(Email)`**
([source](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-3---four-rules-working-together/example-3---individual-entity-type-rule-2)) — identical structure, `exact: [Email/attributes/Email]`, `RegexpReplaceCleanser` with `replace-Email.txt`, no `equals` guard. Stated as "High stringency; no negative rule override possible."

*Why:* email is near-unique, so it alone is a sufficient anchor for `automatic`. No
address-type guard is needed because email has no type dimension. The regex dictionary
normalizes email noise (aliases, `+` suffixes, domain variants) *before* the exact test
— i.e. Reltio's answer to "exact match, but on a canonicalized value".

**Rule 3 — `Rule3(suspect): Fuzzy(FirstName, LastName, AddressLine1) and Exact(Zip5)`**
([source](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-3---four-rules-working-together/example-3---individual-entity-type-rule-3)) — `exact: [Zip5]`, `equals` AddressType ∈ {Bill Location, BIL}, `fuzzy: [FirstName, LastName, AddressLine1]`, `ignoreInToken: [FirstName, LastName]` (AddressLine1 **is** tokenized, with `com.reltio.match.token.AddressLineMatchToken`), comparators `DamerauLevenshteinDistance` ×2 plus AddressLine comparison.

*Why:* this is Rule 1 with the address demoted from exact to fuzzy. That loses the
precision that justified auto-merge, so the type drops to `suspect`. Blocking shifts:
with AddressLine1 now fuzzy it gets `AddressLineMatchToken` (normalize → drop numbers →
drop garbage words → sort), so the token phrase is `Zip5 × sorted-address-words` — still
selective, while tolerating "123 Main St" vs "Main Street".

> **Probable documentation error to flag:** in Rule 3's `comparatorClasses`, AddressLine1
> is mapped to `"com.reltio.match.token.AddressLineComparator"` — the `token` package,
> not `comparator`. The comparator-classes page documents it as
> `com.reltio.match.comparator.AddressLineComparator`. Report this as a doc typo, not as
> a second class.

**Rule 4 — `Rule4(suspect): Fuzzy(FirstName, LastName) and Exact(Phone)`**
([source](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-3---four-rules-working-together/example-3---individual-entity-type-rule-4)) — `exact: [Phone/attributes/Number]`, `RegexpReplaceCleanser` with `replace-Phone.txt` applied *before* the name cleanser, otherwise identical to Rule 2.

*Why:* phone is *shared* (household, switchboard, office line) in a way email usually is
not, so the same shape of rule that was `automatic` on email is `suspect` on phone.
This is the clearest illustration in the corpus that **the anchor attribute's
real-world uniqueness, not the formula's shape, sets the rule type.**

### Example 4 — Two Rules, One a Fuzzier Version than the Other (Household)
Source: [Example 4](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-4---two-rules-one-a-fuzzier-version-than-the-other)

Rule A — `Exact(AddressLine1, Zip) & Fuzzy(Last)`, `type: "automatic"`, scores 25/25:

```json
{
  "uri": "configuration/entityTypes/Household/matchGroups/HouseholdAddressAndZip",
  "label": "Exact(AddressLine1, Zip) & Fuzzy(Last)",
  "type": "automatic",
  "rule": {
    "matchTokenClasses": {
      "mapping": [
        {"attribute": "configuration/entityTypes/Household/attributes/Name", "class": "com.reltio.match.token.ComplexPhoneticNameToken"}
      ]
    },
    "and": {
      "exact": [
        "configuration/entityTypes/Household/attributes/Address/attributes/Zip5",
        "configuration/entityTypes/Household/attributes/Address/attributes/AddressLine1"
      ],
      "fuzzy": ["configuration/entityTypes/Household/attributes/Name"],
      "and": {
        "exactOrAllNull": ["configuration/entityTypes/Household/attributes/IsCurrent"],
        "nullValues": [{"attribute": "configuration/entityTypes/Household/attributes/IsCurrent", "value": "false"}]
      }
    }
  },
  "scoreStandalone": 25,
  "scoreIncremental": 25
}
```

Rule B — `Close Last + Exact Address (Exact Street and Close Postal Code)`, `type: "suspect"`, scores 50/25:

```json
{
  "uri": "configuration/entityTypes/Household/matchGroups/CloseLastExactAddressCloseZip",
  "label": "Close Last + Exact Address (Exact Street and Close Postal Code)",
  "type": "suspect",
  "rule": {
    "matchTokenClasses": {
      "mapping": [
        {"attribute": "configuration/entityTypes/Household/attributes/Name", "class": "com.reltio.match.token.ComplexPhoneticNameToken"}
      ]
    },
    "comparatorClasses": {
      "mapping": [
        {
          "attribute": "configuration/entityTypes/Household/attributes/Address/attributes/Zip5",
          "parameters": [{"parameter": "threshold", "value": "2"}],
          "class": "com.reltio.match.comparator.RangeNumericComparator"
        }
      ]
    },
    "and": {
      "exact": [
        "configuration/entityTypes/Household/attributes/Address/attributes/Premise",
        "configuration/entityTypes/Household/attributes/Address/attributes/Street"
      ],
      "exactOrAllNull": ["configuration/entityTypes/Household/attributes/Address/attributes/POBox"],
      "fuzzy": [
        "configuration/entityTypes/Household/attributes/Address/attributes/Zip5",
        "configuration/entityTypes/Household/attributes/Name"
      ],
      "and": {
        "exactOrAllNull": ["configuration/entityTypes/Household/attributes/IsCurrent"],
        "nullValues": [{"attribute": "configuration/entityTypes/Household/attributes/IsCurrent", "value": "false"}]
      }
    }
  },
  "scoreStandalone": 50,
  "scoreIncremental": 25
}
```

**Why designed this way.** Rule B is Rule A with two specific relaxations, each
implemented at a different layer:
1. **Zip5 moves from `exact` to `fuzzy` with `RangeNumericComparator threshold: "2"`** —
   i.e. postal codes within ±2 numerically are accepted. Reltio's stated rationale:
   "This accommodates geographic proximity scenarios where near-matching zip codes
   likely indicate the same household, warranting human review rather than automatic
   action."
2. **The address is re-decomposed**: instead of exact `AddressLine1`, Rule B requires
   exact `Premise` + `Street` and tolerates PO Box variation via `exactOrAllNull` on
   `POBox`. So it is *stricter* on structured sub-components while *looser* on the
   composite line and the zip.

Both rules share `ComplexPhoneticNameToken` on `Name` and the same
`exactOrAllNull(IsCurrent)` + `nullValues(IsCurrent = "false")` idiom — the latter
coerces the literal `false` to null so that "not current" and "unknown" are treated
identically and the pair is not rejected for disagreeing on currency. Rule A's
relaxation costs it the `automatic` designation: A auto-merges, B queues.
`scoreStandalone` rises 25 → 50 as the rule loosens, while `scoreIncremental` stays 25.

### Inferences
- Across all five examples, **exactly one pattern is universal**: a fuzzy comparator is
  always paired with the token class of the same family, or the attribute is placed in
  `ignoreInToken` and blocking is carried entirely by a different, exact attribute.
  Never is a fuzzily-compared attribute blocked on exactly. That is the single most
  transferable design rule here.
- The examples never use a score threshold to decide merge vs review. The decision is
  structural: *which rule fired*. Auto-merge is reserved for rules with at least one
  high-cardinality exact anchor (full address, email); shared/low-cardinality anchors
  (phone, zip-only, ±2 zip) get `suspect`.
- Reltio's own annotations point out redundancy (Example 2's duplicate DUNS operators)
  and a questionable choice (Example 1/3's Name Dictionary on last names) — these are
  presented as real customer configs warts and all, so the report should not treat every
  detail as endorsed best practice.
- `scoreStandalone` of 25 vs 50 in Example 4 rising as the rule gets *looser* suggests
  these are not confidence scores in the usual sense; more likely steward-facing
  ranking weights. Unverified.

### Gaps
- The parent Example 3 page's introductory discussion of *how the four rules interact as
  a set* (beyond the sentence quoted) was not returned by the extraction.
- No example in the published set demonstrates the `multi` operator, `CrossMultiComparator`,
  `ProximateGeoComparator`, `JaroWinklerComparator` or `VolumeComparator` in situ.

---

## Q5. Guidance on testing and tuning match rules

### Takeaway
Reltio's published workflow is: load a ~10% random, source-proportional sample **with
matching disabled**, then add rules and iterate rebuild → review → hunt **false
positives** → tighten → rebuild. Ordering guidance is explicitly strict-first:
exact-only rules, then exact+fuzzy blends, then heavily fuzzy. Target rule count is
**under 10, ideally 5–7**. The only named metric is false positives; there is no
published precision/recall/F1 framework.

### Cited Findings — sampling
From [Testing and Tuning your Match Rule Design](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/testing-and-tuning-your-match-rule-design):
- Load a "statistically meaningful sample" by drawing **proportionally from each data source** and selecting records **randomly without bias**.
- Volume guidance: approximately **10% of total volume**, or, if that is impractical, "10M records should be a good starting point".

### Cited Findings — the nine-step workflow (same source)
1. **Initial load** — load records with **match rules not enabled**, "to ensure fast processing and accurate profile creation".
2. **Validation** — review loaded profiles for accuracy and iterate until satisfied.
3. **Rule transfer** — implement rules either by JSON editing of the tenant configuration or via the Match Rule Editor in the Data Modeler; the "Advanced Editor" covers what the standard builder cannot express.
4. **Rule analysis** — use the "Rule Analyzer available in the Match Rule Editor (top right corner)" to examine attribute patterns and compare exact versus fuzzy approaches.
5. **Indexing** — run the index **and match table rebuild** jobs.
6. **Review** — examine potential matches through Hub Search and individual profile views.
7. **Identify issues** — "you are looking for false positives (profiles that should not have matched)"; determine "what patterns you see and what modifications to make to your rules to reduce the false positives".
8. **Adjust rules** — modify to reduce false positives, then rebuild match tables.
9. **Iterate** — repeat steps 5–8 until the balance is acceptable, acknowledging that "matching is always an approximation and will never be perfect".
- Stated success criterion: "good enough for my business objectives" rather than perfection.

### Cited Findings — ordering, rule count, and the strict-to-loose progression
From [Design your Comparison Formulas](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/design-your-comparison-formulas):
- "Each rule has one comparison formula and we need to design a set of rules that work together in an efficient and non-overlapping manner."
- **Start strict**: begin by assuming error-free data, using exact operators. Example starting formulas given in shorthand: `Exact(First, Last, Address, Zip) + ExactOrNull(SSN)` and `Exact(Last, NPI) + Equals(Country=USA)`.
- **Rule ordering**: "Order your rules so that the resulting matchGroup overall starts with rules based on exact matches then moving on to a blend of exact and fuzzy, then moving on to heavily fuzzy based rules."
- **Rule count**: "Typically the total number of rules should be less than 10. Five to seven is a good target."
- **Profile the data first**, and distinguish match-usefulness from tokenization-usefulness: "State might be a decent element in a match rule for your comparison tactic, but a poor attribute for tokenization because it only has 26 unique values."
- Eight remediation options are offered for data-quality problems, ranging from fixing source data with ETL tools to Reltio's built-in "String Replacement Cleanser" and "Name Dictionary Cleanser". One of the options is creating "an additional but fuzzier version of the rule" (the tactic Example 4 embodies).
- A dedicated API exists for rule analysis: [Match Rule Analyzer version 2 (Dynamic)](https://docs.reltio.com/en/developer-resources/entity-management-apis/entity-management-apis-at-a-glance/potential-matches-api/match-rule-analyzer-version-2-dynamic) (page identified via search; not fetched).

### Inferences
- The "load without match rules enabled, then turn them on and rebuild the match table"
  sequence makes the match table a **recomputable artefact**, so rule changes are
  evaluated by full rebuild rather than incrementally. A home-grown matcher that cannot
  cheaply rebuild its candidate table cannot run this loop.
- The workflow is **asymmetric on purpose**: it hunts false positives only, because the
  strict-first ordering means recall is added deliberately by adding looser rules, while
  precision must be defended by inspection. This matches the "start strict then loosen"
  prescription — loosening is a *rule-addition* step, not a threshold-tuning step.
- Reltio publishes **no quantitative acceptance metric** — no target precision, no
  recall estimate, no ground-truth construction guidance. For evaluating a home-grown
  matcher this is a notable absence rather than a benchmark: Reltio's bar is steward
  judgement on sampled output. A home-grown matcher with an independent scorer and
  ground truth is measuring something Reltio's own documented workflow does not.
- The 26-unique-values remark about State is effectively a cardinality heuristic for
  blocking-key selection: low-cardinality fields belong in the comparison formula and in
  `ignoreInToken`, never in the token phrase.

### Gaps
- No published precision/recall/F1 targets, confusion-matrix methodology, or
  ground-truth/labelled-pair construction guidance was found on any page retrieved.
- False *negatives* (missed matches) are mentioned only in passing as the other side of
  the balance; no procedure is given for detecting them.
- The Rule Analyzer's actual outputs (what statistics it reports) were not retrieved.

---

## Q6. How multiple rules interact; can one rule veto another?

### Takeaway
Rules are evaluated independently and their candidate sets **union**; each rule that
evaluates true emits a directive according to its own `type`. The strongest directive
wins (an `automatic` hit overrides a `suspect` hit on the same pair). There **is** a
veto mechanism: a **negative rule** (`negativeRule` in place of `rule`), which when true
**demotes any merge directive from any other match group to queue-for-review** — note it
demotes, it does not delete the pair, and it does not affect suspect matches.

### Cited Findings
- "Each rule has one comparison formula and we need to design a set of rules that work together in an efficient and non-overlapping manner." — [Design your Comparison Formulas](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/design-your-comparison-formulas)
- Rule types define the action: `automatic` "issues merge directives"; `suspect` "queues for review". — [Match Group Elements](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration)
- Precedence is stated from the suspect side in Example 3 Rule 3: the `suspect` type means pairs "queue for data steward review **unless overridden by higher-confidence rules**", and "resolution by higher-confidence rules automatically proceeds without review". — [Example 3 Rule 3](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-3---four-rules-working-together/example-3---individual-entity-type-rule-3)
- Example 3 Rule 2 is described as "High stringency; **no negative rule override possible**" — confirming negative rules are the recognised override channel and that some rules are configured to be immune to it. — [Example 3 Rule 2](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-3---four-rules-working-together/example-3---individual-entity-type-rule-2)
- **Negative rule semantics**, from [Negative Rule](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/negative-rule):
  - A negative rule "allows you to prevent any other rule from merging records."
  - "If it evaluates to `true`, it will demote any directive of merge coming from another match group to queue for review."
  - "A match group can contain either a rule OR a negative rule, not both."
  - It **does not affect suspect matches** — only merge directives.
  - Supported operands, verbatim: **`notExactSame`, `notFuzzySame`, `or`, `and`, `equals`, `notEquals`, `nullValues`**. The **`not` operand is explicitly unsupported** in negative rules.
  - Published example JSON:
    ```json
    {
        "uri": "configuration/entityTypes/Individual/matchGroups/BySSN",
        "label": "Individuals not match by SSN",
        "type": "automatic",
        "negativeRule" : {
            "notExactSame": [
                "configuration/entityTypes/Individual/attributes/SSN"
            ]
        }
    }
    ```
  - Illustrating use case: four similar profiles with varying phone numbers should be flagged for review rather than auto-merged when one attribute (SSN) differs significantly.
- Blocking/candidate generation is per-rule: token classes are mapped inside each rule's `rule` object, and the tokenization-design guidance explicitly asks you to "Review cross-rule reusability to minimize the overall token phrase population" — i.e. distinct rules generate distinct token-phrase sets against the same match table. — [Design your Match Tokenization Scheme](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/design-your-match-tokenization-scheme)

### Inferences
- The interaction model is **disjunctive across rules, conjunctive within a rule**: a pair
  is a candidate if *any* rule's token phrase matches, and merges if *any* `automatic`
  rule's formula is true. Candidate sets union; directives resolve by strength
  (automatic > suspect), with negative rules able to knock automatic down to suspect.
- The veto is deliberately **non-destructive** — demote to review rather than suppress.
  A home-grown matcher's "never match" list is a stronger and more dangerous primitive
  than Reltio's negative rule, because it removes the pair from human sight entirely.
- Note the negative-rule example's `type` is `"automatic"` even though the body is a
  `negativeRule`. So `type` appears to describe the match group, not the veto; the veto's
  effect is fixed (demote to review) regardless.
- `notExactSame` and `notFuzzySame` exist **only** in negative rules — they are not in the
  comparison-operator list. This is a distinct, negation-specific operator vocabulary.

### Gaps
- Tie-break/precedence among *multiple* `automatic` rules firing on one pair, and whether
  the surviving `matchGroup` URI is recorded on the merge, is not stated on any page
  retrieved.
- The mechanism by which Example 3 Rule 2 is "no negative rule override possible" is not
  shown in its JSON — no field in that rule expresses immunity. Unresolved; possibly
  prose meaning "the customer has no negative rule covering email", not a config flag.
- Whether negative rules are evaluated on all candidate pairs or only on pairs already
  carrying a merge directive is not stated.

---

## Q7. Parent/subsidiary vs. genuine duplicate; suppressing known non-matches

### Takeaway
Reltio provides two published mechanisms, both generic rather than hierarchy-aware: the
**negative rule** (config-time: demote merges when a discriminating attribute differs)
and the steward **not-a-match flag** (run-time: a pair a human declared distinct). I
found **no published Reltio mechanism that reasons about a parent/subsidiary
relationship specifically** — no relationship-aware match operator and no documented
"is-a-child-of suppresses match" feature.

### Cited Findings
- Negative rules are the documented way to stop over-merging of genuinely distinct but
  highly similar records: a negative rule "allows you to prevent any other rule from
  merging records" and demotes merge to queue-for-review when true; its operand
  vocabulary (`notExactSame`, `notFuzzySame`, `notEquals`, `nullValues`) is built for
  expressing "these differ on a discriminating attribute". — [Negative Rule](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/negative-rule)
- **Not-a-match flag** (steward decision, persisted): "You can choose to reset the
  not-a-match or as-match flag based on specific conditions. If the flag is reset and you
  perform a data load, updated entities are checked if they are part of a match pair that
  is marked as not-a-match, and if this is applicable, then all such entities are removed
  from the not-a-match list to ensure such entities can participate in matching." —
  [Reset the as-Match Not-a-Match Flag](https://docs.reltio.com/en/explore/get-a-crash-course/get-ready-to-turn-your-data-into-action/learn-about-multidomain-mdm/reltio-match-merge-and-survivorship/match-group-elements---description-and-configuration/reset-the-as-match-not-a-match-flag)
- Scope filters (`equals` / `notEquals` on a classification attribute) are the published
  way to keep structurally different records out of each other's rules — Example 2
  excludes `OrgJourneyStatus ∈ {"Generic Account","House Account"}`, and Example 1
  restricts to `HCPType == "Physician"`. — [Example 2](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-2---using-a-custom-comparator-and-token-class), [Example 1](https://docs.reltio.com/en/model/consolidate-data/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-1---simple-rule-with-reltio-name-dictionary)
- Reltio models organizational hierarchy as **relationships** (a separate object class
  with its own relation types and APIs), not as an attribute of the entity —
  [Relation Types](https://docs.reltio.com/en/developer-resources/relation-management-apis/relation-management-apis-at-a-glance/relations-api/relation-types). None of the match-rule pages retrieved reference relationships or relation types as operands in a comparison formula.
- **Explicitly noted as unverified:** a web-search result summary asserted that "For
  parent-subsidiary relationship scenarios specifically, you would typically configure a
  negative rule that detects the parent-subsidiary relationship and prevents these
  entities from being merged". This was **search-engine-generated inference, not a quote
  from any Reltio page**, and no Reltio page I retrieved states it. Do not report it as
  Reltio guidance.

### Inferences
- The practical Reltio pattern for parent-vs-subsidiary is almost certainly a **negative
  rule over a discriminating identifier** — e.g. `notExactSame` on `DUNSNumber`, or on a
  site/location identifier — because that is the only published operator family that
  expresses "same name and address, different legal entity". Example 2's inclusion of
  `DUNSNumber` in a null-tolerant `or` branch is consistent with DUNS being the intended
  legal-entity discriminator in B2B org matching, though that rule uses it permissively
  rather than as a veto.
- Address-level disambiguation is available structurally: Example 4 shows `Premise` and
  `Street` as separate exact-matched sub-attributes, so "same street, different
  premise/suite" can be made to fail — the standard way a subsidiary at the same campus
  is separated from its parent.
- The absence of relationship-aware matching is a real architectural boundary: Reltio
  matches entities on attributes, and hierarchy lives in a separate relationship graph
  that the match formula does not appear to read. A home-grown matcher that *can* consult
  a hierarchy edge during scoring is doing something Reltio does not publish support for
  — which is a design opportunity, but also means there is no Reltio precedent to copy.
- The not-a-match flag is the true "suppress this specific known non-match" primitive,
  and the documented *reset* behaviour matters: suppression can be revoked on data load
  under configured conditions, so it is not a permanent blacklist by default.

### Gaps
- No page retrieved documents matching on relationship existence, relationship type, or
  graph distance. Whether this is genuinely unsupported or merely undocumented on the
  pages I reached, I cannot say.
- The API/UI surface for setting the not-a-match flag, and whether it can be bulk-loaded
  from an external list of known non-duplicates, was not retrieved (only the *reset*
  behaviour page was).
- The `support.reltio.com` articles "Anatomy of a Match Rule" (HTTP 403) and "Practical
  Guide for choosing and testing Token strategies and comparison classes" (not fetched)
  may cover both the parent/subsidiary pattern and testing metrics in more depth. Both
  are on a gated host and could not be read.

---

## Consolidated source list

- [Configure match rules overview](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview)
- [Design your Comparison Formulas](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/design-your-comparison-formulas)
- [Design your Match Tokenization Scheme](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/design-your-match-tokenization-scheme)
- [Testing and Tuning your Match Rule Design](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/testing-and-tuning-your-match-rule-design)
- [Illustrative Examples of Various Match Rules](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules)
- [Example 1 - Simple Rule with Reltio Name Dictionary](https://docs.reltio.com/en/model/consolidate-data/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-1---simple-rule-with-reltio-name-dictionary)
- [Example 2 - Using a Custom Comparator and Token Class](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-2---using-a-custom-comparator-and-token-class)
- [Example 3 - Four Rules Working Together](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-3---four-rules-working-together) and its four sub-pages `example-3---individual-entity-type-rule-1` … `-rule-4`
- [Example 4 - Two Rules, One a Fuzzier Version than the Other](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/configure-match-rules-overview/create-initial-match-rules/illustrative-examples-of-various-match-rules/example-4---two-rules-one-a-fuzzier-version-than-the-other)
- [Match Group Elements - Description and Configuration](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration)
- [Comparator Classes](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/comparator-classes)
- [Comparison Operators](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/the-comparison-formula/comparison-operators)
- [Match Token Classes (legacy path)](https://docs.reltio.com/matchmerge/matchtokenclasses.html) and [Match Token Classes (current)](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/match-token-class/match-token-classes)
- [Match Token Generation](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/match-token-class/match-token-generation)
- [Negative Rule](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/negative-rule)
- [Reset the as-Match Not-a-Match Flag](https://docs.reltio.com/en/explore/get-a-crash-course/get-ready-to-turn-your-data-into-action/learn-about-multidomain-mdm/reltio-match-merge-and-survivorship/match-group-elements---description-and-configuration/reset-the-as-match-not-a-match-flag)
- Identified but NOT retrieved: [Configuring relevance-based matching rules](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/configuring-relevance-based-matching-rules); [Match Rule Analyzer version 2 (Dynamic)](https://docs.reltio.com/en/developer-resources/entity-management-apis/entity-management-apis-at-a-glance/potential-matches-api/match-rule-analyzer-version-2-dynamic); [Creating a Custom Comparator and Match Token class](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge/match-group-elements---description-and-configuration/rule-element/match-token-class/creating-a-custom-comparator-and-match-token-class)
- **Inaccessible**: `support.reltio.com/hc/en-us/articles/4403208429453-Anatomy-of-a-Match-Rule` — **HTTP 403 Forbidden**; `support.reltio.com/hc/en-us/articles/40163764199565-Practical-Guide-for-choosing-and-testing-Token-strategies-and-comparison-classes` — not retrieved (gated host).
