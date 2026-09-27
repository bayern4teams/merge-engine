# Survivorship and Golden-Record Construction as Taught by Profisee and Data Ladder

Scope note: all findings below come from vendor-published web content (blogs, solution
pages, guides, whitepapers). I flag **[MARKETING]** where a claim is a product boast with
no method behind it and **[TECHNICAL]** where it is actionable design guidance. Product
documentation behind a login (Profisee's own docs portal, DataMatch Enterprise's in-app
help) was not reachable; everything here is from the public web.

## Q1. What survivorship rule types does Profisee describe, with exact names? How does Profisee describe composing them?

### Takeaway
Profisee's public taxonomy is six rule types — **Source System Priority, Most Recent
Update, Most Complete Record, Data Quality Score, Conditional Rules, Hybrid Approaches** —
and its central composition doctrine is that rules must be defined **per attribute**, not
per record, and combined either as different rules per attribute or as **cascading
fallback logic**. That cascade language is the most directly reusable guidance Profisee
publishes. [TECHNICAL]

### Cited Findings
- Profisee's survivorship blog names these rule types verbatim: **"Source System
  Priority"** (a hierarchy declaring which system is most trustworthy for specific
  attributes), **"Most Recent Update"**, **"Most Complete Record"** (favors the record
  with the fewest incomplete or empty attributes), **"Data Quality Score"** (scores based
  on accuracy, completeness, conformity and consistency dimensions), **"Conditional
  Rules"** (if-then logic based on attribute value or context), and **"Hybrid
  Approaches"** — [Profisee, MDM Survivorship](https://profisee.com/blog/mdm-survivorship/)
- Definition Profisee uses: survivorship rules "govern which data values are saved (or,
  which ones 'survive') to the golden record in situations where there are duplicate or
  conflicting values in matching records"; rather than "picking one record wholesale to
  save, these rules tell your MDM tool to look at each attribute within matching or
  similar records and choose the best option for each attribute" — [Profisee, MDM
  Survivorship](https://profisee.com/blog/mdm-survivorship/)
- Composition, verbatim: "Most mature survivorship strategies combine multiple rule types
  based on need either by defining different rules per attribute or creating a cascading
  logic that allows the system to fall back on another rule should the preferred attribute
  not exist or does not meet standards." — [Profisee, MDM
  Survivorship](https://profisee.com/blog/mdm-survivorship/) [TECHNICAL]
- Worked hybrid example, verbatim: "A retail organization might use a hybrid approach for
  product master data by trusting the merchandising system for category and pricing
  attributes, applying recency rules for quickly-changing marketing campaign data and
  using quality scoring for supplier-provided attributes with variable data reliability."
  — [Profisee, MDM Survivorship](https://profisee.com/blog/mdm-survivorship/)
- Profisee's six stated best practices: define rules at attribute level; combine multiple
  rule types; document rules for transparency; monitor results with data quality metrics;
  involve business stakeholders in rule definition; review and update rules as systems
  change — [Profisee, MDM Survivorship](https://profisee.com/blog/mdm-survivorship/)
- A second Profisee article gives a *different, looser* list of survivorship approaches:
  **"Recency rule"**, **"System prioritization rule"**, **"Minimum/maximum value
  signals"**, **"Aggregated data"** from all sources combined, **"Most complete data"**
  prioritization, and **"Custom rules"** — [Profisee, What Is a Golden
  Record?](https://profisee.com/blog/what-is-a-golden-record/)
- Product-page framing: rules can be set "based on record completeness, recency, source
  trust or custom logic"; the platform "supports multiple rule types like source, date,
  completeness, and customer [sic — likely 'custom'] logic within a single survivorship
  strategy", with "attribute-level configuration, multiple rule types and full
  auditability" — [Profisee, Matching and
  Survivorship](https://profisee.com/solutions/initiatives/matching-and-survivorship/)
  [MARKETING]
- Auditability claim, verbatim: "Complete transparency into why records matched and how
  golden records were formed — so you can trust and audit every outcome." — [Profisee,
  Matching and
  Survivorship](https://profisee.com/solutions/initiatives/matching-and-survivorship/)
  [MARKETING]
- Manual/steward path: "stewards can choose 'winners' from the information in candidate
  records to survive the merge", with matching records displayed side-by-side in a match
  group — [Profisee, Matching and
  Survivorship](https://profisee.com/solutions/initiatives/matching-and-survivorship/)

### Inferences
- Profisee does **not** publish "most frequent", "longest", or "weighted scoring" as named
  rule types. The nearest equivalents are "Data Quality Score" (multi-dimension scoring,
  not field-value weighting) and "Minimum/maximum value signals". "Most frequent wins" is
  Data Ladder vocabulary, not Profisee's.
- The two Profisee articles' lists do not reconcile cleanly (e.g. "Aggregated data" and
  "Minimum/maximum value signals" appear only in the golden-record article; "Conditional
  Rules" and "Data Quality Score" only in the survivorship article). Treat the
  survivorship blog's six as the canonical taxonomy and the other as informal.
- The cascade formulation ("fall back on another rule should the preferred attribute not
  exist or does not meet standards") implies two distinct fallback triggers — *absence*
  and *failing a standard* — which is a stricter contract than a plain null-coalesce
  chain.

### Gaps
- Profisee's actual product documentation (rule editor option names, exact enumeration
  values) was not publicly reachable; only blog and solution-page prose.
- No public Profisee statement on weighted/trust-score *per-field* arithmetic, tie-breaking
  order, or what happens when a cascade exhausts all rules.

## Q2. What does Data Ladder's guide to data survivorship prescribe? Rule types and process steps in order.

### Takeaway
Data Ladder publishes the most explicit, enumerable prescription of the two: a six-rule
table with an "example" and a "best used when" column per rule, four named *approaches* to
selecting the golden record, and a five-part build process ending in conditional
overwriting and review. [TECHNICAL]

### Cited Findings
- Survivorship rules table, verbatim (rule | example | best used when):
  - **"Trusted source wins"** | ERP billing address replaces CRM address | "One system is authoritative"
  - **"Most complete wins"** | "Full company name replaces an abbreviation" | "Completeness is the priority"
  - **"Most recent wins"** | Latest verified phone number survives | "Values change frequently"
  - **"Verified value wins"** | Validated email replaces unverified email | "Verification is available"
  - **"Most frequent wins"** | Commonly occurring date of birth survives | "Several sources agree"
  - **"Conditional overwrite"** | "Replace only when the destination is empty" | "Existing values must be protected"
  — [Data Ladder, Guide to data
  survivorship](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/)
- Four named approaches to "selecting the golden 'surviving' record", verbatim:
  **"Manual review and selection"**, **"Contending data sources"**, **"Contending data
  attributes"**, **"Overwriting: Best of all worlds"** — [Data Ladder, Guide to data
  survivorship](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/)
- Process, in order, under "How to build survivorship rules to get the golden record?":
  1. Prerequisite: Groups of matching records
  2. Select best approach for golden record selection
  3. Create rules to select the best record as the golden record
  4. Build rules to conditionally overwrite the golden record
  5. Execute and review results
  — [Data Ladder, Guide to data
  survivorship](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/)
- Section order of the guide: "What is data survivorship?" → "Matching, deduplication,
  merging, and survivorship: What's the difference?" → "Common approaches for selecting the
  golden 'surviving' record" → "How to build survivorship rules to get the golden record?"
  — [Data Ladder, Guide to data
  survivorship](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/)
- The "Overwriting: Best of all worlds" approach: "assemble your own golden record by
  overwriting the most comprehensive values of data attributes onto the master record",
  so teams "retain the best information from all duplicate records" — [Data Ladder, Guide
  to data
  survivorship](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/)
  [TECHNICAL]
- Overwrite gating condition is expressed as a literal predicate — **"Overwrite to is
  empty"** — [Data Ladder, Guide to data
  survivorship](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/)
- Data Ladder's separate merge/purge guide gives a five-step outer process: (1)
  "Integrating Data from Multiple Sources", (2) "Identifying Duplicates", (3) "Data
  Matching to Merge and Purge", (4) "Knowing Which Records to Keep" — i.e. applying
  survivorship rules, (5) "Keep Optimizing Your List". It names the **"Most Recent Rule"**
  and frames golden-record construction as deciding "field by field, which source record
  should win" — [Data Ladder, Merge Purge
  Guide](https://dataladder.com/merge-purge-guide/)
- Data Ladder's data-merging article gives a six-step pipeline: **Profile Your Data
  Sources → Cleanse, Standardize, and Transform → Deduplicate before Merging → Choose Your
  Merge Method → Apply Survivorship Rules → Validate the Merged Dataset**, with
  survivorship strategies named **Most Recent Wins, Most Complete Wins, Source Priority,
  Trust Score/Confidence-Based** — [Data Ladder, Data
  Merging](https://dataladder.com/merging-data-from-multiple-sources/)
- DataMatch Enterprise's actual master-record selection options, as documented in Data
  Ladder's best-practices guide, are lower-level and mechanical: **"Longest"** (highest
  number of characters in the chosen data field), **"Shortest"** (lowest number of
  characters), **"Maximum"** (highest number value or latest date value), **"Minimum"**
  (lowest number value or oldest date value), **"Most Popular"** (most recurring value of
  the column). Default behaviour: "the first record in a match group is automatically
  selected as the master record" — [Data Ladder, Best practices for using DataMatch
  Enterprise](https://dataladder.com/guide/best-practices-for-using-datamatch-enterprise/)
  [TECHNICAL]

### Inferences
- Data Ladder operates at two vocabulary levels that the report should keep separate: a
  *business* vocabulary in the survivorship guide ("Trusted source wins", "Most frequent
  wins") and a *mechanical* vocabulary in the product guide ("Longest", "Maximum", "Most
  Popular"). "Most complete wins" in the guide maps to **"Longest"** in the product — i.e.
  character count.
- "Conditional overwrite" is the only rule in the table that is not a winner-selection
  predicate; it is a *write guard*. A faithful implementation therefore has two distinct
  rule stages: pick a winner, then decide whether the winner is allowed to overwrite what
  is already in the target field.
- The default "first record in a match group wins" is an implicit, order-dependent rule —
  worth flagging as a silent-default hazard when evaluating a home-grown implementation.

### Gaps
- Data Ladder does not publish a tie-breaking specification (what happens when two records
  tie on "Longest", or when "Most Popular" has no plurality).
- No public statement of whether survivorship rules are evaluated per-field independently
  or whether one winning record supplies all fields under "Contending data sources".

## Q3. How is a field present in only one source handled versus a field contested across sources?

### Takeaway
Neither vendor gives a formal single-source case. Both fold it into the overwrite/fill
mechanic: an uncontested value simply fills an empty target under "Conditional
overwrite" / "Overwrite to is empty", while contested values go to attribute-level rules
(Profisee) or the "Contending data attributes" approach (Data Ladder). The uncontested
case is under-specified in both vendors' published material. [TECHNICAL, partial]

### Cited Findings
- Data Ladder's "Contending data sources" approach handles cross-source contest by source
  quality: organizations "prioritize and choose data sources based on a quality score"
  measured against data quality metrics, applicable when "any data source have[s] an
  overall better data quality score" — [Data Ladder, Guide to data
  survivorship](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/)
- Data Ladder's "Contending data attributes" approach handles the within-source /
  same-source duplicate case: "you decide which record to select as the golden one
  depending on the values of certain data attributes" — [Data Ladder, Guide to data
  survivorship](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/)
- The single-source / fill case is expressed only as the "Conditional overwrite" rule,
  whose stated purpose is that "Existing values must be protected", gated by "Overwrite to
  is empty" — [Data Ladder, Guide to data
  survivorship](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/)
- Profisee's framing of the multi-source case in a comparison table: multiple sources give
  "high accuracy from best data across all systems" and "more complete records", whereas
  single-source gives "simplicity" but risks "mistakes from source may be validated into
  golden record" — [Profisee, What Is a Golden
  Record?](https://profisee.com/blog/what-is-a-golden-record/)
- Profisee describes the uncontested/complementary case as *enrichment*, not survivorship:
  "the customer email may exist alongside the name in the CRM, but the business locations
  and company name live in the ERP. Enrichment in MDM platforms brings these records
  together in a single, golden record." — [Profisee, What Is a Golden
  Record?](https://profisee.com/blog/what-is-a-golden-record/) [TECHNICAL]

### Inferences
- Profisee's vocabulary splits the two cases into different named activities:
  **enrichment** for a field that only one source has, **survivorship** for a field that
  several sources contest. That distinction is a usable evaluation axis — a home-grown
  implementation that runs the same contest logic over a single candidate is doing
  survivorship where enrichment was called for, and will report spurious "wins".
- Data Ladder's mechanic implies precedence between the two stages: conflicts resolve
  first (winner selection), then the overwrite guard decides whether to write, meaning an
  uncontested non-null never competes — it either fills a blank or is refused.

### Gaps
- Neither vendor publishes what happens to a single-source value when the target field is
  *already* populated by another source and the overwrite guard says "only when empty" —
  the incoming value's fate (dropped, queued, recorded) is unstated.

## Q4. How are several DIFFERENT source fields meaning the same thing consolidated into one target field? Is there a named concept?

### Takeaway
There is no single crisp vendor term. The closest named concepts are Data Ladder's
**"cross-column matching"**, its **"Suggest Mapping"** / automatic field mapping feature,
and **"source-to-target mapping"** as a governance artifact; Profisee calls the
complementary-field case **enrichment** and names no mapping concept. Neither vendor
publishes a term like "attribute consolidation" or "semantic alignment". [TECHNICAL,
partial]

### Cited Findings
- **Cross-column matching** is the named capability for differently-named fields: it
  allows "comparing different fields across datasets (e.g., matching Company Name in one
  source to Organization in another, or Email in one file to Contact Info in another)" —
  [Data Ladder, Dedupe software / multi-source
  integration](https://dataladder.com/dedupe-software-tools-for-multi-source-data-integration/)
  (surfaced via Data Ladder site search, page not directly fetched; see Gaps)
- DataMatch Enterprise's **"Suggest Mapping"** feature "intelligently maps all fields"
  between sources, as an alternative to manual mapping; automatic field mapping works "by
  analyzing value distributions" — [Data Ladder, Best practices for using DataMatch
  Enterprise](https://dataladder.com/guide/best-practices-for-using-datamatch-enterprise/)
- Source-to-target mapping is framed as a decision record, not a schema diff: it captures
  "a set of decisions" about value interpretation, defaults, transformations and
  assumptions. Related named concepts on that page: **"Code translation"** (mapping source
  domain values to standardized target domains) and **"Precedence logic"** (which sources
  take priority when conflicts occur) — [Data Ladder, Source-to-Target
  Mapping](https://dataladder.com/source-to-target-mapping-best-practices/) [TECHNICAL]
- Its best practices: "Start with business rules", not just schema alignment; "Treat
  mapping as a living artifact" with versioning; build validation in with reconciliation
  checks and domain-value coverage tests; "Design for traceability" so values trace back
  through transformations to origins; "Standardize naming, data types, and semantics
  early" — [Data Ladder, Source-to-Target
  Mapping](https://dataladder.com/source-to-target-mapping-best-practices/) [TECHNICAL]
- Warning, verbatim: "If your mapping document is older than your last pipeline change,
  it's already wrong." — [Data Ladder, Source-to-Target
  Mapping](https://dataladder.com/source-to-target-mapping-best-practices/)
- The data-merging article explicitly declines a name for this step: structural
  differences are handled by **"Data Parsing"** ("Break long data fields into smaller,
  standardized components to ensure uniformity across sources"), by "Structural
  Heterogeneity" fixes (parse and reformat columns into a consistent structure) and by
  "Lexical Heterogeneity" fixes ("Transform data values to follow a unified syntax") —
  and "the process doesn't use a specific named concept—it's simply called data
  standardization and transformation" — [Data Ladder, Data
  Merging](https://dataladder.com/merging-data-from-multiple-sources/)
- The merge/purge guide calls step 1 "combining databases from various sources into common
  structure" — again no dedicated term — [Data Ladder, Merge Purge
  Guide](https://dataladder.com/merge-purge-guide/)
- Profisee names no mapping concept; the many-source-fields-to-one-record case is
  described as **enrichment** (see Q3 citation) — [Profisee, What Is a Golden
  Record?](https://profisee.com/blog/what-is-a-golden-record/)

### Inferences
- Both vendors treat semantic alignment of differently-named fields as a **pre-matching
  step** (mapping/standardization), architecturally *upstream* of survivorship, not as a
  survivorship rule. A home-grown design that resolves `description` /
  `companyDescription` / `allDescriptions` inside its survivorship pass is collapsing two
  stages the vendors keep separate — which is exactly how a length-based tiebreak ends up
  substituting for a mapping decision.
- "Structural heterogeneity" vs "lexical heterogeneity" is the nearest thing either vendor
  offers to a vocabulary for the problem, and it is academic terminology rather than
  product terminology.

### Gaps
- The cross-column matching quote reached me via Data Ladder search-result synthesis
  rather than a direct page fetch, so its exact page-level wording is unverified. Treat
  the Company Name/Organization example as indicative, not verbatim.
- No vendor uses "attribute consolidation" or "semantic alignment" in the material I
  found. If the report needs a named concept, "cross-column matching" (Data Ladder) plus
  "source-to-target mapping" is the honest answer; do not attribute the other two terms.

## Q5. Is "most complete" ever defined as raw string LENGTH, and do these sources warn against that?

### Takeaway
Yes — explicitly, and in both vendors' orbit. Data Ladder's product-level rule
**"Longest"** is defined as character count, and its merging article defines "Most
Complete Wins" as "Non-null or longest value prevails". **Neither vendor warns that
length is a poor proxy for quality.** The only adjacent warning is Profisee's, that
completeness rules applied independently carry a "high risk of error". [TECHNICAL — and a
notable absence]

### Cited Findings
- **"Longest"** "selects the master record with the highest number of characters in the
  chosen data field"; **"Shortest"** selects the lowest character count — [Data Ladder,
  Best practices for using DataMatch
  Enterprise](https://dataladder.com/guide/best-practices-for-using-datamatch-enterprise/)
- "Most Complete Wins – Non-null or longest value prevails" — [Data Ladder, Data
  Merging](https://dataladder.com/merging-data-from-multiple-sources/)
- Data Ladder's survivorship guide illustrates "Most complete wins" with "Full company
  name replaces an abbreviation" — i.e. the canonical example is itself a
  longer-string-is-better case — [Data Ladder, Guide to data
  survivorship](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/)
- Profisee defines completeness *record*-wise, not string-wise: "Most Complete Record"
  "favors the record with the fewest incomplete or empty attributes" — a null count, not a
  character count — [Profisee, MDM
  Survivorship](https://profisee.com/blog/mdm-survivorship/)
- Profisee's caution: completeness rules applied independently carry a "high risk of
  error" — [Profisee, MDM Survivorship](https://profisee.com/blog/mdm-survivorship/)

### Inferences
- The two definitions of "most complete" are not the same rule and should not be conflated
  in an evaluation: Profisee = *fewest nulls across a record*; Data Ladder product =
  *longest string in a field*. A home-grown implementation that uses string length and
  calls it "most complete" is implementing Data Ladder's "Longest", and inherits every
  failure mode of that rule (a concatenated junk value, a padded value, a value with a
  duplicated clause, or boilerplate all beat a correct terse value).
- The absence of a warning is itself the finding worth reporting: I found **no** vendor
  text in this scope cautioning that character count is a poor quality proxy. Any such
  warning in the final report must be presented as the report's own analysis, not as
  vendor guidance.

### Gaps
- No Profisee or Data Ladder statement found on length-vs-quality risk, on truncation, or
  on length rules interacting with free-text fields specifically.

## Q6. Data Ladder's Tamr-alternative whitepaper: rule-based vs machine-learning claims

### Takeaway
The whitepaper's argument is entirely about **transparency, configurability without vendor
or data-science involvement, iteration speed, and auditability** — not about accuracy. It
never claims rules match better; it claims you can see, tune and defend them. This is
competitive marketing built on a substantive technical premise. [MARKETING with a
TECHNICAL core]

### Cited Findings
- Rule-based transparency, verbatim: "every rule and weight is visible and tunable"; "every
  match decision is traceable to a documented rule, weight, and threshold" — [Data Ladder,
  Best Tamr
  Alternative](https://dataladder.com/whitepapers/data-ladder-best-tamr-alternative/)
- Adjustability without retraining, verbatim Q&A: "Can matching logic be adjusted without
  ML retraining? Yes" — [Data Ladder, Best Tamr
  Alternative](https://dataladder.com/whitepapers/data-ladder-best-tamr-alternative/)
- Charges against the ML approach: "limited transparency into match logic"; changed
  matching logic "usually requires retraining or model reconfiguration" — [Data Ladder,
  Best Tamr
  Alternative](https://dataladder.com/whitepapers/data-ladder-best-tamr-alternative/)
  [MARKETING]
- Iteration-speed claim: "data stewards adjust rules directly, previews results, validates
  changes within hours" versus ML "model retraining (which may require data science support
  and labeled training data)" — [Data Ladder, Best Tamr
  Alternative](https://dataladder.com/whitepapers/data-ladder-best-tamr-alternative/)
- Audit claim: rule-based matching produces "audit-ready results" that are "reproducible by
  the customer's own data team — not by a vendor's ML explainability tool" — [Data Ladder,
  Best Tamr
  Alternative](https://dataladder.com/whitepapers/data-ladder-best-tamr-alternative/)
- Configurability claim: users "define exact match thresholds", "weight fields by
  importance" and "customize transformations" without vendor involvement — [Data Ladder,
  Best Tamr
  Alternative](https://dataladder.com/whitepapers/data-ladder-best-tamr-alternative/)
- On survivorship specifically, the whitepaper says only that "survivorship rules" and
  "survivorship logic" are "user-configurable rather than platform-locked", letting teams
  "adjust survivorship logic as business definitions evolve" — no rule names — [Data
  Ladder, Best Tamr
  Alternative](https://dataladder.com/whitepapers/data-ladder-best-tamr-alternative/)
- Data Ladder's non-competitive matching content is notably more even-handed: rule-based
  (deterministic) matching is "Fast, precise, audit-friendly" but "Fails with missing or
  inconsistent data"; ML/AI matching "Learns patterns and improves over time" and "Can
  handle multilingual and complex variations" but "Requires labeled training data" and is
  "Harder to govern and audit" — [Data Ladder, Definitive Guide to Data
  Matching](https://dataladder.com/definitive-guide-to-data-matching/) [TECHNICAL]
- A search-surfaced Data Ladder claim cites industry research that ML duplicate-detection
  accuracy runs 92–97% versus 74–81% for rule-based methods alone, with ML requiring
  quality training data and often functioning as a "black box" — [Data Ladder search
  synthesis, page not directly verified](https://dataladder.com/fuzzy-matching-101/)
- Data Ladder positions its own engine as hybrid: it "combines phonetic, string similarity,
  and rule-based algorithms" and lets users "tune thresholds, test match rules, perform
  clerical review, and understand how each match was determined" — [Data Ladder search
  synthesis across matching pages](https://dataladder.com/data-matching-software/)

### Inferences
- There is a real tension inside Data Ladder's own corpus: the Tamr whitepaper argues
  rules-over-ML on transparency grounds while the matching guide concedes rules "fail with
  missing or inconsistent data" and (per the accuracy figures) trail ML materially. The
  defensible synthesis for the report: the vendor argument is about **auditability and
  ownership of the logic**, not accuracy, and should be cited only for that.
- The generality argument the key question asks about is largely *absent*. The whitepaper
  does not argue that rules generalize better across datasets; it argues they are
  inspectable and quickly retunable. That is close to the opposite claim — it concedes
  rules need per-deployment tuning and sells the tuning loop as the feature.

### Gaps
- The 92–97% vs 74–81% figures reached me through search synthesis, not a direct page
  fetch, and the underlying "industry research" is uncited. Do not use these numbers in the
  report without a direct verification pass.
- The whitepaper is gated/marketing-formatted; I could not confirm whether the extracted
  Q&A lines are body text or a comparison-table rendering.

## Q7. Anti-patterns these vendors warn about in survivorship design

### Takeaway
Profisee names three real anti-patterns — record-wholesale source trust, recency without
reliable cross-system timestamps, and completeness used alone — and Data Ladder names two:
blind overwriting that destroys good values with empty ones, and manual review at scale.
**Neither warns about hardcoding rules per dataset or over-fitting to sample data**; that
absence is consistent across every page I checked. [TECHNICAL for what exists; the rest is
a documented absence]

### Cited Findings
- Against record-level source trust, verbatim: "Building logic that trusts full records
  from a single data source like 'trust the ERP for all customer data' can quickly lead to
  problems. Instead, build logic based on each attribute needed for a record: contact name,
  company name, company address, etc." — [Profisee, MDM
  Survivorship](https://profisee.com/blog/mdm-survivorship/) [TECHNICAL]
- Against blind recency: Profisee warns against recency-based rules "without reliable
  timestamps across systems" — [Profisee, MDM
  Survivorship](https://profisee.com/blog/mdm-survivorship/) [TECHNICAL]
- Against completeness alone: completeness rules applied independently carry a "high risk
  of error" — [Profisee, MDM Survivorship](https://profisee.com/blog/mdm-survivorship/)
- Against overwriting good data with blanks, verbatim: "you may end up overwriting valuable
  data with empty values", requiring "complex overwriting rules that are based on
  conditions" — [Data Ladder, Guide to data
  survivorship](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/)
  [TECHNICAL]
- Against manual review at scale, verbatim: "human bias and manual error can lead...to
  create inconsistent versions of the same golden record" — [Data Ladder, Guide to data
  survivorship](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/)
- Against permanent deletion of losing records: "Purging a list doesn't mean deleting it
  entirely", with a recommendation to maintain records of all changes rather than permanent
  deletion — [Data Ladder, Merge Purge
  Guide](https://dataladder.com/merge-purge-guide/) [TECHNICAL]
- Against silent arbitrary resolution: "Without explicit rules, your system either picks
  arbitrarily or fails the merge." — [Data Ladder, Data
  Merging](https://dataladder.com/merging-data-from-multiple-sources/) [TECHNICAL]
- Other merge-stage anti-patterns Data Ladder names: **"Cartesian Explosion"** (many-to-many
  merges multiplying records exponentially without safeguards), data-velocity loss without
  buffering, **synchronization drift** (needing CDC to keep sources aligned), scope creep
  from "last-minute changes", and over-reliance on a single tool ("Data merging usually
  requires more than one type of technology") — [Data Ladder, Data
  Merging](https://dataladder.com/merging-data-from-multiple-sources/)
- Profisee's golden-record anti-patterns: starting from a single source may create
  "systemic bias" and "less flexibility for scale"; validation carries "ongoing validation
  complexity" requiring "constant attention" rather than being "done once" — [Profisee,
  What Is a Golden Record?](https://profisee.com/blog/what-is-a-golden-record/)
- Publishing anti-pattern: for operational MDM, avoid publishing golden records directly
  to source systems like CRMs; instead "update the existing CRM records from the golden
  record", which prevents duplicates when golden records are later split or merged —
  [Profisee, When Should You Publish Golden
  Records?](https://profisee.com/blog/when-to-publish-golden-records/) [TECHNICAL]

### Inferences
- On **discarding losing values**: Profisee's survivorship blog is silent, and its
  solution page "focuses on selecting which information survives rather than managing
  rejected data". Data Ladder's only relevant guidance is at the *record* level (don't hard
  delete purged records), not the *attribute* level. So: no vendor in this scope prescribes
  retaining losing attribute values with provenance. An evaluation that expects that
  practice is holding the home-grown implementation to a bar these vendors do not publish —
  though Profisee's "audit every outcome" claim implies it internally.
- On **hardcoding per dataset and over-fitting to sample data**: checked across Profisee's
  survivorship blog, Data Ladder's survivorship guide, its DataMatch best-practices guide,
  and its definitive matching guide. All four are silent. Data Ladder's best-practices page
  in particular "focuses on positive recommendations rather than cautionary advice about
  common implementation pitfalls". Report this as a genuine gap in vendor literature, not
  as vendor endorsement of either practice.
- On **max/newest used blindly**: Profisee covers newest (timestamp reliability) but I
  found no vendor warning about max/min — and DataMatch ships "Maximum"/"Minimum" as
  first-class options with no stated caveat.

### Gaps
- No vendor text found on rule over-fitting, per-dataset hardcoding, or validating rules on
  held-out data. Given these are core concerns for evaluating a home-grown implementation,
  the final report will have to source them elsewhere (academic ER literature, or Reltio,
  which is out of this scope).
- No attribute-level provenance/"who won and why" retention guidance found beyond marketing
  assertions of auditability.

## Q8. How survivorship interacts with data-quality signals (e.g. disagreement as a quality alert)

### Takeaway
Both vendors connect data quality to survivorship only as an *input* — a quality score that
ranks sources or attributes — and as a *downstream monitoring* activity. Neither publishes
the pattern the question asks about: two sources disagreeing being raised as a quality alert
rather than silently resolved. The closest published mechanism is Profisee's separate
"validation by exception" model for data quality rules. [TECHNICAL for the input direction;
the alert direction is a documented gap]

### Cited Findings
- Quality as a survivorship input: Profisee's **"Data Quality Score"** rule "assigns scores
  based on accuracy, completeness, conformity, and consistency dimensions" — [Profisee, MDM
  Survivorship](https://profisee.com/blog/mdm-survivorship/)
- Quality as source ranking: Data Ladder's "Contending data sources" approach prioritizes
  sources "based on a quality score" measured against data quality metrics — [Data Ladder,
  Guide to data
  survivorship](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/)
- Quality as downstream monitoring: one of Profisee's six survivorship best practices is
  "Monitor results with data quality metrics" — [Profisee, MDM
  Survivorship](https://profisee.com/blog/mdm-survivorship/)
- The by-exception model, verbatim: "Data quality rules make life easier for data stewards
  by running data validation by exception, meaning stewards only need to manually review
  information that does *not* match the rules." Rules work by "Flagging poor quality data
  for manual review" rather than auto-correcting — [Profisee, Data Quality
  Rules](https://profisee.com/blog/data-quality-rules/) [TECHNICAL]
- Profisee's data quality rule functions include confirming "that the values in a column
  match their data type requirements", confirming values "match a specific format", and
  looking "for blank and empty fields" — [Profisee, Data Quality
  Rules](https://profisee.com/blog/data-quality-rules/)
- Profisee's cascade condition couples the two concerns: the system falls back to another
  rule "should the preferred attribute not exist **or does not meet standards**" — i.e. a
  quality standard can disqualify a winner — [Profisee, MDM
  Survivorship](https://profisee.com/blog/mdm-survivorship/) [TECHNICAL]
- Steward-in-the-loop conflict handling: matching records are displayed side-by-side in a
  match group where the steward can "choose 'winners'" — a manual exception path for
  conflicts, not an automated alert — [Profisee, Matching and
  Survivorship](https://profisee.com/solutions/initiatives/matching-and-survivorship/)
- Data Ladder's data-merging pipeline ends with "Validate the Merged Dataset — Conduct
  final profiling to catch errors", i.e. post-hoc profiling rather than in-flight conflict
  alerting — [Data Ladder, Data
  Merging](https://dataladder.com/merging-data-from-multiple-sources/) [TECHNICAL]

### Inferences
- The one genuinely transferable mechanism is Profisee's cascade disqualification clause:
  a value can win its rule and still be rejected for failing a quality standard, forcing
  fallback. That is the published hook for treating a suspect value as a quality problem
  mid-survivorship.
- "Validation by exception" is Profisee's answer to the general shape of the question, but
  it is applied to *rule violations* (wrong type, wrong format, blank), not to
  *inter-source disagreement*. Extending it to disagreement is an inference, not vendor
  guidance.
- Profisee's own survivorship blog was explicitly checked for "conflicting values signal a
  data quality problem" and does not address it. Treat any such framing in the report as
  original analysis.

### Gaps
- Neither vendor publishes anything on numeric disagreement thresholds, variance-based
  conflict alerts, or routing contested numeric fields to an exception queue instead of
  resolving them. I found no reliable source for this pattern in Profisee or Data Ladder
  material.
- Profisee's "assignment rules" (surfaced in search synthesis as a rule category alongside
  validation rules) were not confirmed on the data-quality-rules page itself; that page
  "doesn't explicitly categorize 'validation rules' vs. 'assignment rules' by those names".
  Do not cite "assignment rules" as Profisee vocabulary without verification.

## Access notes

- All primary target pages were reachable and fetched directly:
  profisee.com/blog/mdm-survivorship, profisee.com/blog/what-is-a-golden-record,
  profisee.com/solutions/initiatives/matching-and-survivorship,
  profisee.com/blog/when-to-publish-golden-records, profisee.com/blog/data-quality-rules,
  dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record,
  dataladder.com/whitepapers/data-ladder-best-tamr-alternative,
  dataladder.com/merge-purge-guide, dataladder.com/merging-data-from-multiple-sources,
  dataladder.com/source-to-target-mapping-best-practices,
  dataladder.com/guide/best-practices-for-using-datamatch-enterprise,
  dataladder.com/definitive-guide-to-data-matching.
- **Not directly fetched** (claims from these reached me only via search-result synthesis
  and are flagged inline): dataladder.com/dedupe-software-tools-for-multi-source-data-integration,
  dataladder.com/fuzzy-matching-101, dataladder.com/data-matching-software.
- Profisee's product documentation portal and DataMatch Enterprise's in-application help
  were not accessed; the exact enumeration of rule options in either product's UI is
  therefore only as good as the vendors' public guide pages.
