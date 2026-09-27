# Reltio Survivorship Rule Design — Technical Reference

Scope note on sourcing: Reltio's docs site serves the same survivorship content at several
URL paths (`/en/model/consolidate-data/...`, `/en/objectives/resolve-potential-matches/...`,
and the legacy `/matchmerge/*.html`). Where the same claim appears at more than one path I cite
the one I actually fetched. Pages retrieved via a fetch-and-summarize tool; **direct verbatim
quotes below are those the tool returned in quotation marks** — I have marked anything that is
the tool's paraphrase rather than a literal quote. One page (Reltio Support KB article
360026781592) returned HTTP 403 and one guessed URL returned 404; both are listed under Gaps.

---

## Q1. The exact named survivorship strategies Reltio provides

### Takeaway
Reltio ships twelve named attribute survivorship strategies, identified in the L3 config by the
literal `survivorshipStrategy` value: `LUD`, `SRC_SYS`, `Frequency`, `Aggregation`, a
record-values based source priority strategy (`winnerSourceType`), `OldestValue`, `MinValue`,
`MaxValue`, `OtherAttributeWinnerCrosswalk`, `WinnerEntityCrosswalk`, `ValueBasedPriority`, plus
user-defined **custom strategies** that extend a parent strategy. `LUD` (recency) is the default.
**There is no "longest string" strategy** in Reltio's documented set.

### Cited Findings

Strategy list with documented behaviour (strategy identifier verbatim; behaviour text in quotes
is verbatim from the page):

- **`LUD`** (Last Update Date) — the default strategy. "Attributes that were recently updated (with the maximum number of update dates) will have highest priority to be marked as OV." Winner is the crosswalk with the most recent update date, considering `singleAttributeUpdateDate`, `updateDate`, and `sourcePublishDate`. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- **`SRC_SYS`** (Source System) — "The user provides a priority list of sources...All crosswalks from a source system with the highest priority become winners." If no priority is present in the rule mapping, the system falls back to the group's priority list, then to the source-type configuration. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- **`Frequency`** — "Calculate amounts of crosswalks from which values were provided. Values with the most amount of crosswalks become winners." Ties are broken by applying LUD. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- **`Aggregation`** — "All presented values become winners (`ov` = True)." No selection occurs at all. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules); also [legacy Survivorship Rules](https://docs.reltio.com/matchmerge/survivorshiprules.html)
- **Record-values based source priority** — "All values from the winner source type become winners." Configured with `winnerSourceType` and optional `winnerSourceAttributes`; falls back to LUD if no values exist from the winner source type. (This is the strategy family whose advanced variant is called `RecordBase`.) — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- **`OldestValue`** — "Finds a crosswalk with the oldest create date. All values that were provided by this crosswalk become winners." — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- **`MinValue`** — "The smallest value becomes the winner." Comparison follows attribute type: numeric = smallest number, date = earliest, boolean = `false`, string = lexicographical order. Nested attributes require `comparisonAttributeUri`. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- **`MaxValue`** — "Values with the highest value become winners." Mirror of MinValue; also supports `comparisonAttributeUri`. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- **`OtherAttributeWinnerCrosswalk`** — "It gets all winner crosswalks from this primary attribute. If the current attribute has any crosswalks that are winners for the primary attribute, all of them and all values provided by them become winners." Requires `primaryAttributeUri`; cyclic dependencies are not allowed. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- **`WinnerEntityCrosswalk`** — "The entity value of the current winning entity becomes the winner. All crosswalks from the winner entity also become winners." After a merge the winner's OV is set to `true` and the loser's to `false`. Winner selection follows the `explicitWinner` parameter, defaulting to the oldest entity. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- **`ValueBasedPriority`** — "A user-defined priority list of attribute values determines the Operational Value (OV). The value with the highest priority becomes the winner." Requires `valuesPriorityOrder`. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules); [Value-Based Priority Survivorship Strategy](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/value-based-priority-survivorship-strategy)
- **Custom survivorship strategy** — extends an existing strategy via `mainSourceType` and `parentStrategyURI`. "All crosswalks from the winner source type become winners, and all values from those crosswalks also become winners." The parent strategy is applied as a fallback. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules); [Configuring Survivorship Rules in the L3](https://docs.reltio.com/en/developer-resources/system-administration-apis/system-administration-apis-at-a-glance/configuration-api/configuring-survivorship-rules-in-the-l3)

Complete set of configuration field names (JSON keys) for a survivorship rule mapping entry:

- `attribute` (required) — the attribute URI the rule applies to
- `survivorshipStrategy` (required) — the strategy identifier
- `sourcesForOv` (optional; spelled `sourcesForOV` in the fallback-strategies page)
- `comparisonAttributeUri` (optional)
- `comparisonAttributeOvOnly` (optional, boolean)
- `fallbackStrategies` (optional)
- `fallbackUsingCriteria` (optional: `MORE_THAN_ONE`, `ZERO`, `ZERO_OR_MORE_THAN_ONE`)
- `primaryAttributeUri` (`OtherAttributeWinnerCrosswalk` only)
- `sourcesUriOrder` (`SRC_SYS`)
- `filter` (optional)
- `sortAs` (`MinValue`/`MaxValue` only)
- `valuesPriorityOrder` (`ValueBasedPriority` only)
- `lookupComparisonField` (optional: `lookupCode`, `lookupValue`, `lookupRawValue`)
- `ignoreCase` (optional, boolean)
- `winnerSourceType` (record-values based source priority)
- `winnerSourceAttributes` (optional)
- `mainSourceType`, `parentStrategyURI` (custom strategy)
- `explicitWinner` (`WinnerEntityCrosswalk`)
- `sortCrosswalksInSrcSYSNonAdvanced` (optional, `SRC_SYS` tie-breaking)

— [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules); [Configuring Fallback Strategies](https://docs.reltio.com/en/developer-resources/system-administration-apis/system-administration-apis-at-a-glance/configuration-api/configuring-fallback-strategies-for-survivorship-rules)

Data Modeler UI display names differ from the L3 identifiers. The UI names observed are
**Recency** (= `LUD`, "Applied as the default strategy"), **Frequency**, **Minimum value**
(requires selecting a comparison attribute), **Maximum value** (requires selecting a comparison
attribute), **Other Attribute Winner Crosswalk** (uses `primaryAttributeUri`), and
**Source Systems** (priority assigned by drag-and-drop ordering). — [Manage Survivorship rules in Data Modeler](https://docs.reltio.com/en/objectives/model-data/data-modeling-at-a-glance/data-modeling-operation/define-entity-types-and-attributes/configure-survivorship-groups-in-data-modeler/manage-survivorship-rules-in-data-modeler)

Framing of survivorship itself: "Survivorship rules, also known as survivorship strategy or
operational value (OV) rules, define a way to govern which attribute values must be identified as
the OV." and "Reltio survivorship is a process to instantly get an Operational Value (OV) for the
attribute based on the type of survivorship rule selected." Survivorship "operates separately from
merging, executes in real-time during API calls" — i.e. OV is **computed at read time**, not
materialised at merge time. — [Design survivorship rules](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules); [Survivorship Rules](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-rules)

### Inferences
- The strategy set is deliberately crosswalk-centric: `LUD`, `SRC_SYS`, `OldestValue`, `Frequency`,
  `OtherAttributeWinnerCrosswalk` and `WinnerEntityCrosswalk` all select **crosswalks** first and
  then take whatever values those crosswalks contributed. Only `MinValue`, `MaxValue` and
  `ValueBasedPriority` select on the *value* itself. A home-grown engine that only reasons over
  values (recency-of-value, longest-string, etc.) is missing Reltio's primary axis.
- Because OV is computed on read rather than stamped at merge, non-OV values are necessarily
  retained; there is no "losing value discarded" step in the model.

### Gaps
- **No "longest string" / string-length strategy is documented anywhere I could reach.** The Data Modeler page explicitly does not list one. If the evaluation target implements longest-string, that is an extension beyond Reltio's documented set, not a parity feature.
- I could not retrieve a canonical single table of *all* L3 `survivorshipStrategy` enum literals (e.g. whether the record-values-based strategy has a literal name distinct from the `RecordBase` short name used in `survivorshipAdvancedBehavior`). Treat "Record-values based source priority" as a prose label, not a verified enum value.

---

## Q2. How the DEFAULT strategy is configured and how per-attribute rules override it

### Takeaway
Default is `LUD` out of the box. Configuration happens through **survivorship groups**: one group
per entity type carries `"default": true`, and its `mapping` array of
`{attribute, survivorshipStrategy, ...}` entries overrides `LUD` per attribute. Non-default groups
inherit from the default group attribute-by-attribute.

### Cited Findings
- "By default, LUD is the default survivorship strategy. But you can specify certain strategies as the default strategy." — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- Each survivorship group contains "a mapping of attribute URIs to survivorship strategies," allowing different attributes within an entity type to use distinct rules. — [Survivorship groups](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-groups)
- Group JSON fields: `"default"` (boolean, designates the primary group), `"mapping"` (array of attribute-strategy pairs), and within each mapping entry `"attribute"`, `"survivorshipStrategy"`, `"sourcesUriOrder"`, `"filter"`, `"fallbackStrategies"`. — [Survivorship groups](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-groups)
- "The survivorship group with `default=true` is treated as a parent for all other non-default survivorship groups of the current entity type." — [Survivorship groups](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-groups)
- "If the survivorship strategy, for an attribute, is not defined in the non-default survivorship group, the survivorship strategy for the attribute is inherited from the default survivorship group." — [Survivorship groups](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-groups)
- "Attributes lacking explicit mapping inherit default LUD strategy." — [legacy Survivorship Rules](https://docs.reltio.com/matchmerge/survivorshiprules.html)
- Configuration scopes are "**Default group**: Applied system-wide" and "**Non-default groups**: Applied to users with specific roles". — [legacy Survivorship Rules](https://docs.reltio.com/matchmerge/survivorshiprules.html)
- A survivorship strategy can also be set directly on an attribute definition in L3, e.g. `"survivorshipStrategy":"SRC_SYS"` inline on the attribute. — [Configuring Survivorship Rules in the L3](https://docs.reltio.com/en/developer-resources/system-administration-apis/system-administration-apis-at-a-glance/configuration-api/configuring-survivorship-rules-in-the-l3)

### Inferences
- Resolution order for a given attribute + user is: non-default group mapping for the user's role →
  default group mapping → `LUD`. This is three-level inheritance, per attribute, not per record.
- There are therefore **two** override axes stacked: which *group* applies (by role/ruleset) and
  which *attribute* entry within it. An implementation with a single global default plus a flat
  per-field override table covers only one of the two.

### Gaps
- The exact wrapper key name for the groups array in L3 (I saw the prose term "survivorship groups" and the `"default":true` flag, but the fetch of the L3 page reported that `survivorshipGroups` as a JSON key was "not mentioned" on that page). Do not assume the literal key spelling.

---

## Q3. Advanced survivorship strategies, dependent survivorship rules, survivorship groups, value-based priority

### Takeaway
These are four distinct mechanisms. "Advanced" is an opt-in flag (`survivorshipAdvancedBehavior`)
that changes tie behaviour from *first-crosswalk-wins* to *all-tied-crosswalks-win*. "Dependent"
rules are conditional rules gated by a `filter` on another attribute's value. "Groups" are
role-scoped rule sets per entity type. `ValueBasedPriority` is a value allow-list ranking.

### Cited Findings

**Advanced survivorship strategies** — an opt-in behaviour change, not new strategies:
- Configured with `"survivorshipAdvancedBehavior"`, an array of strategy short names. Verbatim example:
  ```json
  "survivorshipAdvancedBehavior": [
    "LUD",
    "OldestValue",
    "SRC_SYS",
    "MyOwnRecordBase1"
  ]
  ```
  — [Advanced Survivorship Strategies](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/advanced-survivorship-strategies)
- Four strategies support advanced behaviour: **`LUD`**, **`OldestValue`**, **`SRC_SYS`**, **`RecordBase`** (the last is referenced by a user-defined name in the example above). — [Advanced Survivorship Strategies](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/advanced-survivorship-strategies)
- Advanced `LUD`: "If some values have many crosswalks with the same most recent date, then all of those crosswalks become winners." Contrast the non-advanced behaviour below in Q7. — [Advanced Survivorship Strategies](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/advanced-survivorship-strategies)
- Advanced `OldestValue`: selects values with the oldest creation date; multiple crosswalks sharing that date all become winners. — [Advanced Survivorship Strategies](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/advanced-survivorship-strategies)
- Advanced `SRC_SYS`: prioritizes values from specified source systems; falls back to most recently updated values when priority sources are unavailable. — [Advanced Survivorship Strategies](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/advanced-survivorship-strategies)
- Advanced `RecordBase`: "If there is any value...with a crosswalk with winner source type, then only crosswalks with this source type become winners." — [Advanced Survivorship Strategies](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/advanced-survivorship-strategies)

**Dependent survivorship rule** — conditional rule selection driven by another attribute's value:
- "You can identify a source to calculate Operational Value based on the value of another attribute." / you "define a survivorship rule in a manner where you provide precedence to certain data sources based on the value of another attribute." — [Dependent Survivorship Rule](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/dependent-survivorship-rule)
- Worked example, verbatim (the `Type = Episode` case):
  ```json
  {
    "attribute": "configuration/relationTypes/ProductToCountry/attributes/Language/attributes/Overview",
    "sourcesUriOrder": [
      "configuration/sources/A",
      "configuration/sources/B",
      "configuration/sources/C",
      "configuration/sources/D",
      "configuration/sources/E",
      "configuration/sources/Reltio",
      "configuration/sources/F"
    ],
    "survivorshipStrategy": "SRC_SYS",
    "filter": {
      "equals": [
        {
          "URI": "configuration/relationTypes/ProductToCountry/attributes/Type",
          "value": "Episode"
        }
      ]
    }
  }
  ```
  A second rule in the same page repeats the shape with a different `sourcesUriOrder` (C, A, B, D, E, Reltio, F) gated on `Type` = `Documentary`. — [Dependent Survivorship Rule](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/dependent-survivorship-rule)
- Field semantics on that page: `attribute` = the nested attribute the rule applies to; `sourcesUriOrder` = ordered source precedence; `filter` = conditional logic determining when this rule activates. "Each rule evaluates independently with its own fallback strategy." — [Dependent Survivorship Rule](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/dependent-survivorship-rule)
- Related hard dependency for `OtherAttributeWinnerCrosswalk`: "If the primary attribute does not have winner crosswalks, then the dependent attribute also does not have winner OV." — surfaced from [Survivorship Rules](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-rules)

**Survivorship groups** — role-scoped rule sets (see Q2 for inheritance):
- "non-default groups are applied for users with specific roles only. Roles are also defined in a group setting" — associated through Rulesets, "allowing different users to see different OV values based [on] their assigned roles." — [Survivorship Rules](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-rules)
- "The user's role can also influence which survivorship rule applies to an attribute." — [Design survivorship rules](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules)
- Worked example described on the groups page: `MinValue` applied to NPI identifiers and `OldestValue` applied to ME identifiers, with `LUD` as default for the rest — demonstrating conditional survivorship keyed on nested attribute values. — [Survivorship groups](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-groups)
- Data Modeler surfaces groups with UI fields "Survivorship groups", "Rules section", "Fallback criteria", "Filter", "Default field" (applicability to all users), "Role", "Number of rules". — [Manage Survivorship rules in Data Modeler](https://docs.reltio.com/en/objectives/model-data/data-modeling-at-a-glance/data-modeling-operation/define-entity-types-and-attributes/configure-survivorship-groups-in-data-modeler/manage-survivorship-rules-in-data-modeler)

**Value-based priority survivorship strategy**:
- Strategy identifier `ValueBasedPriority`; OV chosen from "a user-defined priority list of acceptable values." — [Value-Based Priority Survivorship Strategy](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/value-based-priority-survivorship-strategy)
- "You must define the `valuesPriorityOrder` list to indicate the preferred attribute values and their priority order." — [Value-Based Priority Survivorship Strategy](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/value-based-priority-survivorship-strategy)
- "If none of the attribute values match those in the list, all values will have their OV flag set to `false`." — [Value-Based Priority Survivorship Strategy](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/value-based-priority-survivorship-strategy)
- Verbatim examples:
  ```json
  {
    "attribute": "configuration/entityTypes/Location/attributes/Country",
    "valuesPriorityOrder": ["US", "CAN", "UK"],
    "lookupComparisonField": "lookupCode",
    "survivorshipStrategy": "ValueBasedPriority"
  }
  ```
  ```json
  {
    "attribute": "configuration/entityTypes/HCP/attributes/Status",
    "valuesPriorityOrder": ["Active", "Inactive", "Defunct"],
    "survivorshipStrategy": "ValueBasedPriority"
  }
  ```
  Configuration validation fails if `valuesPriorityOrder` is missing. — [Value-Based Priority Survivorship Strategy](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/value-based-priority-survivorship-strategy)

### Inferences
- `ValueBasedPriority` is the one strategy that can produce **no OV at all** by design (all values `ov=false` when nothing matches the allow-list). That is a distinctive, deliberately fail-closed semantic worth checking against a home-grown engine, which typically always emits a value.
- "Advanced" is best read as *tie-policy* configuration: non-advanced = deterministic single winner (first crosswalk), advanced = set-valued winner. Those are different contracts for downstream consumers, which is presumably why it is opt-in per strategy rather than global.
- Dependent rules are implemented purely as `filter` predicates on sibling rules in the same mapping, not as a separate rule type — so "dependent survivorship rule" is a documentation label for a usage pattern of `filter` + `sourcesUriOrder`, not a distinct `survivorshipStrategy` value.

### Gaps
- Whether two `filter`-gated dependent rules for the same attribute can both match, and which then wins, is not stated on the page I fetched. Ambiguous — do not assume.
- The `filter` grammar beyond `equals` (`{URI, value}` pairs) is not documented on that page; other operators may exist but I did not confirm any.

---

## Q4. Single source vs contested across multiple sources

### Takeaway
Yes — a single-source value is treated specially and short-circuits the strategy. Reltio explicitly
states that when a strategy is applied to only one value, that value and its crosswalk win
regardless of whether the source appears in the priority list.

### Cited Findings
- "If the strategy is applied to only one value, this value and its crosswalk become winners, irrespective of whether this crosswalk has a source from the priority list or not." — [Survivorship Rules](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-rules)
- Contested case under `SRC_SYS`: "If an attribute does not have values from sources of the priority list, priority of sources are taken from the source type configuration...If the business model also does not have a priority set for sources, there are no winners." — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- "If the business model does not specify source priorities and the priority list is also absent, no values are survived (all values have ov=false)." — surfaced from [Survivorship Rules](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-rules)
- Source restriction interacts with this: "If restrictions exist on sources for OV, only crosswalks (and values linked to them) from permitted source-systems are used to determine winners, all other crosswalks and values are ignored. So if there are no values from permitted sources, there are no winner values." (`sourcesForOv`) — surfaced from [Survivorship Rules](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-rules); also "If these restrictions exist, only crosswalks (and values linked to them) from permitted source-systems are used." — [legacy Survivorship Rules](https://docs.reltio.com/matchmerge/survivorshiprules.html)
- Two categories of crosswalk/value are excluded from the candidate set before any strategy runs: "Crosswalks with end date (delete date): Do not participate in OV calculation just as those crosswalks do not exist." and "Ignored values do not participate in OV calculation." — [Survivorship Rules](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-rules)
- Each attribute is calculated independently; when multiple values qualify, tie-breaking follows strategy-specific rules. — [legacy Survivorship Rules](https://docs.reltio.com/matchmerge/survivorshiprules.html) (paraphrase by fetch tool, not verbatim)
- `Aggregation` is the explicit exception: all values from all sources become winners regardless of how many sources contributed. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)

### Inferences
- The single-value short-circuit is a **pre-filter, not a fallback**: it fires before strategy
  evaluation, which is why `SRC_SYS` with an unlisted source still yields an OV for a
  single-source attribute but yields *no* OV when several unlisted sources contest it. An
  implementation that runs source-priority uniformly will diverge from Reltio exactly on
  single-source-unlisted attributes (Reltio keeps the value; naive priority drops it).
- The candidate set for any strategy is therefore: all values minus end-dated crosswalks, minus
  ignored values, minus values outside `sourcesForOv`. Pinned values bypass the whole thing (Q5).

### Gaps
- Whether the single-value short-circuit applies to every strategy or only to `SRC_SYS` is not stated unambiguously — the sentence appears in the `SRC_SYS` discussion but is phrased generically ("If the strategy is applied to only one value"). Flagging as ambiguous rather than inferring.

---

## Q5. Pinned and ignored attributes on merge

### Takeaway
Pinning is a manual steward override that **bypasses survivorship entirely** (pinned value is
always OV and always returned first); ignoring **removes a value from OV calculation entirely** and
always returns it last. Which flag survives when two same-valued attributes with different flags
merge is governed by the `pinIgnoredPriority` meta-configuration property, default
`["ignored", "pin", "neutral"]`.

### Cited Findings
- Pinned: "For a pinned value, survivorship rules are not applied to attribute values if one of those values is pinned. All pinned values become OVs, and all attribute's survivorship rules are just ignored. When getting attribute values, the pinned value is always returned first no matter which survivorship strategy is set for this attribute type." — [Pinned or Ignored Attributes Merges](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/pinned-or-ignored-attributes-merges)
- Ignored: "For an ignored value, the values are not participating in OV calculation, just as those attributes don't exist. When getting attribute values, the API always returns ignored values last, no matter which survivorship strategy is set for this attribute type." — [Pinned or Ignored Attributes Merges](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/pinned-or-ignored-attributes-merges)
- Summary line on the page: "Pinned values become OVs with survivorship rules ignored, while ignored values don't participate in OV calculation." — [Pinned or Ignored Attributes Merges](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/pinned-or-ignored-attributes-merges)
- `pinIgnoredPriority` property spec: Name `pinIgnoredPriority`; Required: No; Type: array of three strings; Allowed values `"ignored"`, `"pin"`, `"neutral"`; Default `["ignored", "pin", "neutral"]`. "The leftmost position indicates highest priority." "neutral" means neither pinned nor ignored flags are defined. — [Pinned or Ignored Attributes Merges](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/pinned-or-ignored-attributes-merges)
- Purpose: "You can be sure that an existing pinned/ignored flag is not removed during dataload or entities merge if it has a higher priority than the flag in the contributing entity." You define "whether pinned/ignored or unpinned/unignored statuses (flags) should survive when two attributes with the same value but with different flags get merged." — [Pinned or Ignored Attributes Merges](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/pinned-or-ignored-attributes-merges)
- Governed merge combinations: pinned + ignored; pinned + unflagged; ignored + pinned; ignored + unflagged. — [Pinned or Ignored Attributes Merges](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/pinned-or-ignored-attributes-merges)
- Worked examples from the page:
  - Example 1, default priority `["ignored", "pin", "neutral"]`: Profile A `Johnny` (Ignored) + Profile B `Johnny` (Pinned, alongside `Johnathan`) → `Johnny` survives as **ignored**; `Johnathan` becomes OV.
  - Example 2, reversed priority `["pin", "ignored", "neutral"]`: same inputs → `Johnny` survives as **pinned** (`OV=true`).
  — [Pinned or Ignored Attributes Merges](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/pinned-or-ignored-attributes-merges)
- Pin and Ignore are first-class API operations, not just UI state: there are dedicated **Pin Attribute** and **Ignore Attribute** endpoints in the Entity Management attribute APIs, and bulk update of attributes supports them. — [Pin Attribute](https://docs.reltio.com/en/developer-resources/entity-management-apis/entity-management-apis-at-a-glance/attribute-apis/pin-attribute); [Ignore Attribute](https://docs.reltio.com/en/developer-resources/entity-management-apis/entity-management-apis-at-a-glance/attribute-apis/ignore-attribute)

### Inferences
- The problem pinning solves is the *steward-override durability* problem: without it, the next
  load from a higher-priority source silently reverts a human decision. Reltio's answer is not a
  new strategy but an out-of-band flag that pre-empts all strategies, plus a documented conflict
  policy (`pinIgnoredPriority`) for the flags themselves when profiles merge.
- Note the ordering guarantee is also part of the contract: pinned first, ignored last, in the
  returned value list. A home-grown engine that only sets a boolean OV flag without a stable
  ordering is under-specified relative to this.

### Gaps
- Whether *multiple* pinned values on one attribute all become OV simultaneously is implied ("All pinned values become OVs") but the multi-pin ordering among themselves is not documented.

---

## Q6. Provenance — crosswalks, retention of losing values, OV vs all contributing values

### Takeaway
Provenance is the crosswalk. Every attribute value is bound to the crosswalk(s) that supplied it;
merge **aggregates** crosswalks and values rather than discarding losers; and the "operational
value" is just a per-value boolean `ov` flag, so all contributing values stay queryable. `ovOnly`
on read/stream/export selects between the OV projection and the full set.

### Cited Findings
- "Crosswalks are analogous to the Primary Key or Unique Identifier in RDBMS." They link Reltio objects to source-system identifiers and associate attribute values with their data providers. — [Crosswalks](https://docs.reltio.com/en/objectives/model-data/data-modeling-at-a-glance/data-modeling-operation/define-crosswalks-for-data-sources/crosswalks)
- Documented crosswalk fields: `type` ("Unique identifier of a source system"), `value` ("Assumed to represent a unique entity, often the source system's primary key"), `dataProvider` ("Determines whether attributes are being sourced from this crosswalk", defaults to true), `sourceTable` (used "to ensure uniqueness" when keys are unique only per table), `URL` ("URL of the value in the source system"). "Reltio associates each supplied attribute with data provider crosswalks." — [Crosswalks](https://docs.reltio.com/en/objectives/model-data/data-modeling-at-a-glance/data-modeling-operation/define-crosswalks-for-data-sources/crosswalks)
- Data providers vs non-data providers: "Data providers supply attribute values for an object and the attributes are associated with the crosswalk. Non-data providers are associated with an overall entity (or relationship). Supplied attributes will NOT be associated with this crosswalk." A crosswalk is made non-data-provider by setting `dataProvider` to `false`. — [Crosswalks API](https://docs.reltio.com/en/developer-resources/entity-management-apis/entity-management-apis-at-a-glance/crosswalks-api)
- "An attribute in the Reltio platform can hold multiple values, each associated with a contributing crosswalk." — [Crosswalks API](https://docs.reltio.com/en/developer-resources/entity-management-apis/entity-management-apis-at-a-glance/crosswalks-api)
- Date fields used by survivorship, named on the strategy page: `singleAttributeUpdateDate`, `updateDate`, `sourcePublishDate` (for `LUD`), and crosswalk create date (for `OldestValue`), plus end date / delete date for exclusion. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules); see also [Managing Crosswalk Dates](https://docs.reltio.com/en/objectives/model-data/data-modeling-at-a-glance/data-modeling-operation/define-crosswalks-for-data-sources/managing-crosswalk-dates)
- Merge aggregates rather than discards: the merge process "aggregates what's called the crosswalks, and the attribute values, relationships, and interactions from all the contributing profiles, and then combines them into the winning, or golden, profile." — [Reltio match and merge](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge)
- Losing values are retained and retrievable: non-OV values remain stored; the Hub shows the OV prominently with a badge indicating the count of additional non-OV values, and the **Sources** view shows all values whether OV or not. — [Survivorship Rules](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-rules) (paraphrase by fetch tool)
- "The Sources perspective displays source data and crosswalk details in a color-coded grid on the Profile page, where attributes are linked to their originating crosswalks via matching colors, allowing users to track provenance for each value." — surfaced from Reltio docs via search; primary page is the Profile "Sources" view. — [search result citing docs.reltio.com](https://docs.reltio.com/en/objectives/manage-profiles/profile-management-at-a-glance/profile-management-operation/view-operational-values-for-a-ruleset)
- `ovOnly` read semantics: "When `ovOnly` is absent or set to false, the system uses all mapped values, including values marked as `"ov":"true"` and `"ov":"false"`." / "When `ovOnly` is set to true, the system only uses values marked as `"ov":"true"`." — [Get Entity](https://docs.reltio.com/en/developer-resources/entity-management-apis/entity-management-apis-at-a-glance/entities-api/get-entity)
- Streaming: with `ovOnly` true, only attributes with `ov: true` appear in the attributes/deltas payload; "crosswalks are not filtered based on the OV status of their associated attributes, and a crosswalk may still appear in the crosswalks section even if all of its attributes have `ov: false`." — [Message streaming](https://docs.reltio.com/en/developer-resources/data-integration-apis/data-integration-apis-at-a-glance/events-api/message-streaming)
- Reversibility: "Reltio creates unique URIs for each entity before merging them. This enables you to unmerge them later if necessary." Caveat — with `matchBeforeCreate` set to true, "you cannot subsequently unmerge these entities as the original entities were not assigned unique URIs." — [Reltio match and merge](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge)

### Inferences
- The design principle is: **survivorship is a view, not a write.** The store holds
  (value, crosswalk, flags); OV is a computed boolean per value evaluated per read and per role.
  This is what makes role-scoped survivorship groups and unmerge possible at all. Any home-grown
  engine that resolves survivorship destructively at merge time cannot replicate role-scoped OV,
  cannot replay a rule change without a re-merge, and cannot unmerge losslessly.
- `dataProvider: false` is the documented escape hatch for "this system identifies the record but
  must not contribute values" — a distinction between *identity provenance* and *value provenance*
  that is easy to miss in a home-grown model with a single source_id column.

### Gaps
- The crosswalks concept page I fetched did not document `createDate`, `updateDate`, `deleteDate`, `singleAttributeUpdateDate`, or `sourcePublishDate`; those names come from the survivorship pages and the "Managing Crosswalk Dates" page title. I did not fetch "Managing Crosswalk Dates" itself, so precedence among the three LUD date fields is unverified beyond "the most recent update date among" them.
- I found no documented API for "explain why this value won" (a survivorship trace/lineage endpoint). Absent, not confirmed missing.

---

## Q7. Ordering semantics when strategies are chained; unresolved ties

### Takeaway
Chaining is a **narrowing filter with an explicit trigger condition**, not a scored vote and not
plain first-match-wins. Each strategy produces a winner *set*; if the set is too large (or empty,
depending on `fallbackUsingCriteria`) the next `fallbackStrategies` entry runs **over the previous
step's winners only**. Non-advanced strategies break residual ties by taking the first crosswalk;
advanced mode leaves all tied crosswalks as winners (multi-OV).

### Cited Findings
- Fallback strategies "serve as tiebreakers when primary survivorship rules produce multiple Operational Values (OVs)". "Fallback strategies can be any attribute survivorship rule" and multiple may be listed, "with preference given to earlier entries." — [Configuring Fallback Strategies](https://docs.reltio.com/en/developer-resources/system-administration-apis/system-administration-apis-at-a-glance/configuration-api/configuring-fallback-strategies-for-survivorship-rules)
- **Narrowing is explicit** for the default criterion: "If a strategy returns two or more winner values, its fallback strategy will use only the winner values and winner crosswalks from the strategy." (`MORE_THAN_ONE`, the default) — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- `fallbackUsingCriteria` allowed values and meanings:
  - `MORE_THAN_ONE` (default) — "Applies fallback only when the primary rule generates multiple OVs".
  - `ZERO_OR_MORE_THAN_ONE` — "Applies fallback when primary rule yields zero or more than one OV"; "the logic for determining values used by the fallback strategy is based on the retrieved number of winners."
  - `ZERO` — triggers only when no winners exist in the primary step.
  — [Configuring Fallback Strategies](https://docs.reltio.com/en/developer-resources/system-administration-apis/system-administration-apis-at-a-glance/configuration-api/configuring-fallback-strategies-for-survivorship-rules); [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- Tie behaviour per strategy (non-advanced):
  - `LUD`: "If there are many crosswalks with the same update date, only the first crosswalk becomes a winner."
  - `OldestValue`: "If there are many crosswalks with the same create date, only the first crosswalk becomes a winner."
  - `Frequency`: applies LUD as secondary resolution when values tie on crosswalk count.
  - `SRC_SYS`: when the optional `sortCrosswalksInSrcSYSNonAdvanced` parameter is true, crosswalks sort by Type, SourceTable, and Value (deterministic ordering for "the first crosswalk").
  — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- Tie behaviour in advanced mode is the opposite: all tied crosswalks win. "If some values have many crosswalks with the same most recent date, then all of those crosswalks become winners." — [Advanced Survivorship Strategies](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/advanced-survivorship-strategies)
- Fallbacks do not inherit: "Fallback Strategies are not inherited from other attributes. Each Attribute must have its own set of Fallback Strategies defined separately." — [Configuring Fallback Strategies](https://docs.reltio.com/en/developer-resources/system-administration-apis/system-administration-apis-at-a-glance/configuration-api/configuring-fallback-strategies-for-survivorship-rules)
- Deprecated predecessor: "DEPRECATION NOTICE: As of 04/11/2017, `fallbackStrategiesChain` is deprecated, and you now use `fallbackStrategies`." — [Configuring Fallback Strategies](https://docs.reltio.com/en/developer-resources/system-administration-apis/system-administration-apis-at-a-glance/configuration-api/configuring-fallback-strategies-for-survivorship-rules)
- A fallback step may carry its own `sourcesForOV` list, so the permitted-source restriction can differ between primary and fallback steps; undefined means the fallback evaluates over the full fallback context. — [Configuring Fallback Strategies](https://docs.reltio.com/en/developer-resources/system-administration-apis/system-administration-apis-at-a-glance/configuration-api/configuring-fallback-strategies-for-survivorship-rules); [Configuring Survivorship Rules in the L3](https://docs.reltio.com/en/developer-resources/system-administration-apis/system-administration-apis-at-a-glance/configuration-api/configuring-survivorship-rules-in-the-l3)
- Unresolved / empty outcome is a legitimate terminal state: with no source priority anywhere, "there are no winners" and "all values have ov=false"; likewise `ValueBasedPriority` with no matching value sets every OV flag to `false`. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules); [Value-Based Priority](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/value-based-priority-survivorship-strategy)

### Inferences
- Mechanically: `winners = strategy_0(candidates)`; then for each fallback *i*, if
  `trigger(fallbackUsingCriteria, |winners|)` then `winners = strategy_i(winners)` — a pipeline of
  set-narrowing operators, each seeded with the previous step's surviving *crosswalks and values*.
  This is materially different from a weighted score across rules; no rule contributes a partial
  score, and a later rule can never resurrect a value an earlier rule eliminated (except under
  `ZERO`/`ZERO_OR_MORE_THAN_ONE`, where the fallback runs because the previous step produced
  nothing and therefore has nothing to narrow).
- Two distinct tie contracts coexist in one product — first-crosswalk-wins (default) and
  all-tied-win (advanced, per strategy, via `survivorshipAdvancedBehavior`). An evaluation of a
  home-grown engine should ask *which* contract it implements and whether it is configurable;
  "we always pick one deterministically" is Reltio's non-advanced default, not a universal.
- `sortCrosswalksInSrcSYSNonAdvanced` exists because "the first crosswalk" is only meaningful with
  a total order. If a home-grown engine relies on "first" without a defined sort key, its output is
  non-deterministic across reloads — Reltio hit this and added an explicit sort.

### Gaps
- For `ZERO_OR_MORE_THAN_ONE`, the doc says the fallback's input set "is based on the retrieved number of winners" without spelling out the zero case's input (presumably the full original candidate set). **Ambiguous — stated explicitly rather than inferred.**
- The fallback-strategies page I fetched did not answer whether chaining narrows; I relied on the `MORE_THAN_ONE` sentence from the survivorship-rules page for that. The two pages are consistent but the narrowing rule is documented in only one place I could reach.
- Maximum fallback chain depth is not documented.

---

## Q8. Survivorship at the level of an entire attribute GROUP (source-coherent value sets)

### Takeaway
**Reltio's "survivorship groups" are NOT source-coherence groups** — they are role-scoped
collections of per-attribute rules. Reltio's actual mechanism for keeping a coherent set of values
from one source is **`OtherAttributeWinnerCrosswalk`** (and at record level
`WinnerEntityCrosswalk` / the `RecordBase` / `winnerSourceType` family), which makes one attribute
inherit *the winning crosswalks of another attribute* so their values come from the same source.
Nested attributes give a second, structural form of coherence.

### Cited Findings
- The groups page, asked directly about source coherence: "The documentation does not explicitly state that groups maintain coherent value sets from single sources across attribute groups." Groups are "a mapping of attribute URIs to survivorship strategies." — [Survivorship groups](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-groups)
- `OtherAttributeWinnerCrosswalk` is the crosswalk-coherence mechanism: "It gets all winner crosswalks from this primary attribute. If the current attribute has any crosswalks that are winners for the primary attribute, all of them and all values provided by them become winners." Requires `primaryAttributeUri`; cyclic dependencies are not allowed. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- Its failure mode is also coherence-preserving: "If the primary attribute does not have winner crosswalks, then the dependent attribute also does not have winner OV." — surfaced from [Survivorship Rules](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-rules)
- `WinnerEntityCrosswalk` extends coherence to whole-record scope: "The entity value of the current winning entity becomes the winner. All crosswalks from the winner entity also become winners." Winner via `explicitWinner`, default oldest entity. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- Record-values based source priority / `RecordBase` also yields a source-coherent block: "All values from the winner source type become winners" (`winnerSourceType`, optional `winnerSourceAttributes`); advanced `RecordBase`: "If there is any value...with a crosswalk with winner source type, then only crosswalks with this source type become winners." — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules); [Advanced Survivorship Strategies](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/advanced-survivorship-strategies)
- Custom strategies are built on the same idea: "All crosswalks from the winner source type become winners, and all values from those crosswalks also become winners" (`mainSourceType` + `parentStrategyURI`). — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- Nested attributes: rules "can be applied to simple, nested, sub-nested attributes separately", and "survivorship rules cannot be set for sub attributes of referenced attributes". For nested attributes under `MinValue`/`MaxValue` you supply `comparisonAttributeUri` pointing at a child attribute, so the parent nested value's winner is decided by a child field's comparison. — [Survivorship Rules](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-rules)
- `comparisonAttributeOvOnly` (boolean) exists alongside `comparisonAttributeUri`, i.e. the comparison child value can itself be restricted to OV values. — [Survivorship Rules](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- Groups example demonstrating per-nested-value rule targeting: `MinValue` on NPI identifiers, `OldestValue` on ME identifiers, `LUD` default for the rest. — [Survivorship groups](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-groups)

### Inferences
- There are three tiers of coherence scope in Reltio, and they are worth separating when evaluating
  a home-grown engine:
  1. **Structural** — a nested attribute (e.g. one Address, one Identifier) is a single value with
     sub-attributes; picking the nested value picks its sub-fields together. Coherence is free.
  2. **Declared cross-attribute** — `OtherAttributeWinnerCrosswalk` chains attribute B to the
     winning crosswalks of attribute A, pairwise, acyclic. Coherence across *flat* attributes is
     opt-in and must be wired attribute-by-attribute (a DAG rooted at one "primary" attribute).
  3. **Record/source-level** — `WinnerEntityCrosswalk`, `winnerSourceType`/`RecordBase`, and custom
     strategies push a whole source's crosswalk set to winner status.
- Reltio does **not** offer a "keep field set {X, Y, Z} from whichever single source is best for the
  group" primitive as a single declaration. You approximate it by designating one attribute as
  primary and pointing the rest at it with `OtherAttributeWinnerCrosswalk`. This is worth flagging
  in an evaluation: if the home-grown implementation has a true group-level "best source for this
  block of fields" rule, that is *more* expressive than Reltio here, not less — but Reltio's DAG
  approach has the compensating property that each dependent attribute still falls back
  independently.
- The nesting rule ("cannot be set for sub attributes of referenced attributes") is a real
  constraint: coherence across a reference boundary is not configurable.

### Gaps
- Whether `OtherAttributeWinnerCrosswalk` chains transitively (B→A, C→B) is not documented beyond "cannot create cyclic dependencies", which implies chains are allowed. **Inferred, not confirmed.**
- Whether a nested attribute's OV is computed as an atomic unit or per sub-attribute is genuinely ambiguous in the docs I read: the page states rules apply to nested and sub-nested attributes *separately*, which suggests per-sub-attribute computation, yet `comparisonAttributeUri` implies the parent nested value is selected as a whole using a child as the comparison key. I could not resolve this from the pages I reached and am flagging it rather than picking a reading. **This is the single most important open question for the evaluation and should be re-checked against a live tenant or Reltio support.**

---

## Sources fetched

- [Survivorship Rules (model/consolidate-data)](https://docs.reltio.com/en/model/consolidate-data/design-survivorship-rules/survivorship-rules)
- [Survivorship Rules (objectives tree)](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-rules)
- [Design survivorship rules (index)](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules)
- [Advanced Survivorship Strategies](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/advanced-survivorship-strategies)
- [Survivorship groups](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-groups)
- [Dependent Survivorship Rule](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/dependent-survivorship-rule)
- [Value-Based Priority Survivorship Strategy](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/value-based-priority-survivorship-strategy)
- [Pinned or Ignored Attributes Merges](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/pinned-or-ignored-attributes-merges)
- [Configuring Fallback Strategies for Survivorship Rules](https://docs.reltio.com/en/developer-resources/system-administration-apis/system-administration-apis-at-a-glance/configuration-api/configuring-fallback-strategies-for-survivorship-rules)
- [Configuring Survivorship Rules in the L3](https://docs.reltio.com/en/developer-resources/system-administration-apis/system-administration-apis-at-a-glance/configuration-api/configuring-survivorship-rules-in-the-l3)
- [Manage Survivorship rules in Data Modeler](https://docs.reltio.com/en/objectives/model-data/data-modeling-at-a-glance/data-modeling-operation/define-entity-types-and-attributes/configure-survivorship-groups-in-data-modeler/manage-survivorship-rules-in-data-modeler)
- [Crosswalks (data modeling)](https://docs.reltio.com/en/objectives/model-data/data-modeling-at-a-glance/data-modeling-operation/define-crosswalks-for-data-sources/crosswalks)
- [Crosswalks API](https://docs.reltio.com/en/developer-resources/entity-management-apis/entity-management-apis-at-a-glance/crosswalks-api)
- [Reltio match and merge](https://docs.reltio.com/en/reltio/what-does-reltio-do/what-reltio-does-at-a-glance/data-unification-and-mdm-at-a-glance/data-unification-and-mdm-in-detail/reltio-match-and-merge)
- [Get Entity (ovOnly)](https://docs.reltio.com/en/developer-resources/entity-management-apis/entity-management-apis-at-a-glance/entities-api/get-entity)
- [Message streaming (ovOnly)](https://docs.reltio.com/en/developer-resources/data-integration-apis/data-integration-apis-at-a-glance/events-api/message-streaming)
- [Survivorship Rules (legacy /matchmerge/)](https://docs.reltio.com/matchmerge/survivorshiprules.html)
- [Pin Attribute API](https://docs.reltio.com/en/developer-resources/entity-management-apis/entity-management-apis-at-a-glance/attribute-apis/pin-attribute)
- [Ignore Attribute API](https://docs.reltio.com/en/developer-resources/entity-management-apis/entity-management-apis-at-a-glance/attribute-apis/ignore-attribute)

### Pages I could not access
- Reltio Support KB "Survivorship: description of rules and additional information" (article 360026781592) — **HTTP 403 Forbidden**; would likely have resolved the nested-attribute atomicity question and the full tie-breaking notes.
- `docs.reltio.com/en/explore/get-going-with-apis-and-rocs-utilities/data-modelling/design-survivorship-rules/survivorship-rules` — **HTTP 404**; that path in the suggested-sources list does not exist.
- "Managing Crosswalk Dates" — not fetched (tool-call budget); precedence among `singleAttributeUpdateDate` / `updateDate` / `sourcePublishDate` therefore unverified beyond "the most recent among them".
