# General vs. Over-fitted Entity-Resolution Rules

Scope note for the report writer: this file covers **dataset-independent mechanisms** for
(1) parent/subsidiary vs. duplicate, (2) blocking/tokenization key choice, (3) relationship-edge
derivation, and (4) anti-patterns / evaluation discipline. For every mechanism I state the
**INPUT it requires**, so it can be judged against a system whose only inputs are two flat
PostgreSQL sources with no declared foreign keys and no external reference data.

Accessibility problems encountered (stated rather than papered over):
- `helios2.mi.parisdescartes.fr/.../csur20-blockingfiltering.pdf` (Papadakis et al. blocking &
  filtering survey) — fetched but **not text-extractable**; no `pdftotext`/`pypdf`/`poppler` in
  this environment, so no claims are drawn from it.
- `vldb.org/pvldb/vol9/p312-papadakis.pdf` — fetch failed (ECONNRESET). Claims about that paper
  below come from **search-result excerpts only** and are flagged.
- `hpi.de/.../2009_rostin_a.pdf` (Rostin, ML foreign-key discovery) — fetched but not
  text-extractable; numbers below are from search excerpts and flagged.
- `link.springer.com/article/10.1007/s10844-019-00562-z` (HoPF, holistic PK/FK detection) —
  303 redirect to an auth IdP; **not read**.
- `docs.informatica.com` Hierarchy Manager pages — **HTTP 403**; Informatica claims below come
  from search-result excerpts of those pages, not from the pages themselves.

---

## Q1. Distinguishing parent/subsidiary/franchise/branch/division from a genuine duplicate — generally, not by a hardcoded suffix list

### Takeaway
The practitioner consensus is that this is **not a string-matching problem at all**: it is solved
by (a) modelling the relationship as a first-class, typed edge distinct from a merge, and (b)
resolving to an **external identifier authority** (LEI Level 2, DUNS linkage, company-registry
number) that publishes "who owns whom". A hand-written legal-suffix list is treated as a
*normalization* step that is acknowledged even by its advocates to be insufficient for the
parent/subsidiary problem — and the one source that tackles that problem head-on shows the
suffix/keyword approach is not merely incomplete but **inverted** on the hard cases.

### Cited Findings

**Typed relationship edges, not merges (the core general mechanism)**
- "Matching is not merging" — resolution (grouping) is kept architecturally separate from
  survivorship and golden-record creation — [insightful-data-lab](https://insightful-data-lab.com/2026/03/15/entity-resolution/)
- Merge and split should be treated as "two separate decisions rather than one," because the
  errors have asymmetric cost: "A false split leaves one entity as two records… and is fixable
  later by merging. A false merge combines two entities into one, which propagates a wrong
  identity into every downstream consumer and is fixable only by reconstructing what was
  combined." — [logiciel.io](https://logiciel.io/blog/entity-resolution)
- MDM vendors implement this as *hierarchy management*: Informatica's Hierarchy Manager "defines
  the relationships, affiliations, and hierarchies among various entities (such as individual to
  organization, organization to organization, or individuals within households)" and supports
  configurable **relationship types** — [Informatica MDM docs, Hierarchy Manager](https://docs.informatica.com/master-data-management/multidomain-mdm/10-3/data-steward-guide/using-the-hierarchy-manager/hierarchy-manager-functionality.html)
  and [Hierarchy Manager Relationship Types](https://docs.informatica.com/master-data-management/multidomain-mdm/10-3/data-director-implementation-guide/manual-idd-configuration/hierarchy-manager-configuration/hierarchy-manager-relationship-types.html)
  (**both pages returned 403 to me; quoted from search excerpts**)
- Reltio exposes hierarchies/relationships through the "Reltio Intelligent Data Graph," listed
  alongside — and therefore separate from — "identity resolution" and "dynamic survivorship"
  — [Reltio MDM](https://www.reltio.com/master-data-management/)
- "Linkages in a corporate organizational hierarchy represent links between business locations
  and provide insight into how firms interact with one another within their 'family'" —
  [DZone, Hierarchy in MDM](https://dzone.com/articles/hierarchy-in-mdm-what-and-why)

**Multi-class framing, with a published class list**
- D&B's own patent enumerates **8 business classifications**: single-location subsidiary,
  headquarters, branch, division, subsidiary, parent, domestic ultimate, global ultimate — and
  **two distinct linkage relation types**: branch→headquarters and subsidiary→parent. It also
  states that "other types of family relationships… occur which are not linked in a file,
  because the affiliated company has no legal obligation for the debts of the other company"
  — [US Patent 8,036,907, "Method and system for linking business entities using unique identifiers"](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/8036907)
  (from search excerpt of the patent text)
- D&B's operational definition of linkage is **legal/financial responsibility, not name
  similarity**: "linkage occurs… when one business entity has financial or legal responsibility
  for another business, creating a Headquarters/Branch or Parent/Subsidiary relationship"; a
  headquarters is "a business that has other branches or divisions that reports to it, and it is
  financially responsible for those locations" — [D&B Guide: Understanding Corporate Linkage](https://dnbguide.desk.com/customer/en/portal/articles/2270656-understanding-dun-bradstreet%E2%80%99s-corporate-linkage)
  (vendor-adjacent help site, not D&B's primary docs — treat as indicative)
- The identifier mechanism: each site carries up to **four DUNS numbers** — its own, its
  headquarters', its domestic ultimate's and its global ultimate's — so the hierarchy is
  *carried in the record as keys*, not inferred from the name. D&B is described as holding
  "over 18 million linked records representing about 4 million Global Ultimate records"
  — same source as above (unverified against D&B primary material)

**External identifier authority: GLEIF LEI Level 2**
- Level 2 data exists specifically to answer "who owns whom." Entities with an LEI report their
  **direct accounting consolidating parent** and their **ultimate accounting consolidating
  parent** — [GLEIF, Level 2 Data: Who Owns Whom](https://www.gleif.org/en/lei-data/access-and-use-lei-data/level-2-data-who-owns-whom)
- Parenthood is defined by an **accounting consolidation standard**, i.e. a legal/financial test,
  not an ownership-percentage or name test — same GLEIF page.
- Format: Relationship Record Common Data File (RR-CDF); v1.1 published May 2017, **v2.1 May
  2021**. There is a separate **Reporting Exceptions** format for entities that legitimately do
  not report a parent — [GLEIF RR-CDF 2.1](https://www.gleif.org/en/lei-data/access-and-use-lei-data/level-2-data-relationship-record-rr-cdf-2-1-format),
  [GLEIF Reporting Exceptions 2.1](https://www.gleif.org/en/lei-data/access-and-use-lei-data/level-2-data-reporting-exceptions-2-1-format)
- The exception list is policy-defined by the Regulatory Oversight Committee and includes
  binding legal constraints, legal obstacles, disclosure concerns and lack of consent —
  [GLEIF, ROC Policy on Level 2 Data](https://www.gleif.org/en/lei-data/access-and-use-lei-data/level-2-data-who-owns-whom/roc-policy-on-level-2-data)
- Access is free: LEI Search, concatenated files, Golden Copy and Delta Files, and the GLEIF API
  — [GLEIF, LEI Data: Access & Use](https://www.gleif.org/en/lei-data/access-and-use-lei-data)
- Reltio ships D&B enrichment that materialises hierarchies and relationships onto profiles —
  [Reltio docs, Hierarchies and Relationships (D&B enrichment)](https://docs.reltio.com/en/applications/data-integrations/data-enrichment-integrations-at-a-glance/reltio-enrichment-with-db-at-a-glance/reltio-enrichment-with-db-set-up/working-with-reltio-enrichment-with-db/enriching-profiles/hierarchies-and-relationships)
  — i.e. the vendor pattern is *buy the hierarchy, don't derive it from names*.

**Is a hand-written suffix/keyword list acceptable practice, or an anti-pattern?**
The sources split, and the split is informative:
- *Treated as acceptable, for normalization only*: "Legal suffixes like Inc., Corp., LLC, Ltd.,
  GmbH, and S.A. serve legal purposes but add noise… For most operational uses, they should be
  stripped" — [Openprise, 9 company name normalization rules](https://www.openprisetech.com/blog/company-name-normalization-rules-and-best-practices)
- Even the pro-list sources require a **hand-maintained exception list**, e.g. "The Limited"
  (the retailer) is not "The" — [Openprise](https://www.openprisetech.com/blog/company-name-normalization-rules-and-best-practices);
  and the approach degrades on non-Latin scripts: "For Chinese, Russian, transliterated, or
  non-Latin-script company names, it gets complicated quickly. The reference-list approach hits
  its limits when transliteration variants, mixed scripts, and locale-specific suffixes pile
  up." — [Tilores/Medium, How to Normalize Company Names](https://medium.com/tilo-tech/how-to-normalize-company-names-for-deduplication-and-matching-21e9720b30ba)
- A **statutory** precedent that suffix-stripping is a legitimate *normalization* rule: UK
  Companies House name-matching "automatically excludes legal suffixes like Ltd, Limited, PLC,
  and LLP" and "disregards case sensitivity, spaces, hyphens, and apostrophes" —
  [Companies House name matching rules](https://mycompanyregistration.uk/blogs/our-ultimate-guides/what-are-companies-house-name-matching-rules-in-the-uk)
  (secondary source restating the rules, not the Companies House site itself)
- *Treated as inadequate for exactly the parent/subsidiary problem*: "Normalization only fixes
  legal forms such as Ltd, Inc and GmbH. It does nothing for common business-language tokens
  like Group, Holdings, International, Services and Solutions." Those tokens appear in **5–25%
  of records** and are sometimes discriminative and sometimes not — "a Solutions subsidiary is
  different from an Industries subsidiary" — [Tilores, Company Name Normalization Isn't Enough for Fuzzy Matching](https://tilores.io/content/company-name-normalization-isnt-enough-for-fuzzy-matching/)
- The decisive worked example from the same source: standard matchers score
  **"Acme Group Ltd" vs "Beta Group Ltd"** (different companies, should reject) *higher* than
  **"Acme Group Ltd" vs "Acme Holdings Ltd"** (plausibly the same entity, should accept), so
  "Lower the threshold to catch pair B and you also accept pair A as a false positive." Their
  recommended fix is data-derived token weighting rather than a longer list:
  `weight(token) = log(N / count(token))` (IDF), which "automatically derives domain-specific
  token importance from your actual dataset, eliminating reliance on manual rule maintenance"
  — [Tilores](https://tilores.io/content/company-name-normalization-isnt-enough-for-fuzzy-matching/)

**INPUT REQUIRED (judgeability against two flat PostgreSQL sources)**
| Mechanism | Input it needs | Available with 2 flat PG sources, no external data? |
|---|---|---|
| Typed relationship edge separate from merge (`related_to`, `parent_of`) instead of a merge decision | Only a data model that can express a non-merge link | **Yes** — pure design choice, costs nothing |
| GLEIF LEI Level 2 (`direct`/`ultimate accounting consolidating parent`) | An LEI on the record, or a reliable join key (name+registered address) to the free GLEIF Golden Copy | **No** unless an LEI or registry number column exists; needs an external download |
| DUNS corporate linkage (own / HQ / domestic ultimate / global ultimate DUNS) | A licensed D&B feed and a DUNS on the record | **No** — commercial |
| Company registry number (Companies House no., EIN, VAT/CVR/SIREN) as the identity key | The column must exist in the source | Checkable per-source; **if present it dominates name matching** |
| Registered address as discriminator (same name, different registered address ⇒ branch/subsidiary candidate, not duplicate) | An address column, plus address normalization | **Yes, if address columns exist** — this is the strongest purely-internal signal found |
| Multi-class classifier (duplicate / branch-of / subsidiary-of / unrelated) | **Labelled examples of each class** — the expensive input | **No** without a labelling programme; this is where reward hacking is most likely to hide |
| IDF token weighting (`log(N/count(token))`) | Only the corpus itself | **Yes** — derives the "suffix list" from the data instead of hardcoding it |

### Inferences
- The suffix-list question is best answered by *where the list sits*. As a **normalization**
  step before comparison it has statutory precedent (Companies House) and vendor endorsement. As
  the **discriminator between a duplicate and a subsidiary** it is an anti-pattern: the only
  source that tests that use directly shows the ordering is wrong on the hard pairs (Tilores),
  and every authority that actually maintains corporate hierarchy at scale (GLEIF, D&B) defines
  the relation by legal/financial consolidation and carries it as an *identifier*, never as a
  name feature.
- IDF token weighting is the cheapest general replacement for a hardcoded keyword list: it needs
  no external data, no labels, and it *re-derives itself per corpus*, which is precisely the
  property a reward-hacked hardcoded list lacks. This is an inference from the Tilores mechanism,
  not a benchmarked claim.
- A system with only two flat sources cannot honestly claim to *distinguish* subsidiary from
  duplicate; the general, defensible move is to *decline to merge* and emit a typed
  "related/possible-hierarchy" edge for review — which is exactly the asymmetry argument in
  logiciel.io (a false split is recoverable, a false merge is not).

### Gaps
- I found **no published precision/recall for any subsidiary-vs-duplicate classifier**. No
  academic benchmark for this specific multi-class problem surfaced.
- GLEIF publishes coverage statistics for Level 2 in "quarterly Global LEI System Business
  Reports," but the page I read gave **no percentage**; I did not retrieve a report, so I cannot
  state what fraction of LEIs have a reported parent. This matters: if coverage is low, LEI L2 is
  a partial oracle, not a complete one.
- Informatica and Profisee hierarchy-management docs were not directly readable (403). I could
  not confirm whether Informatica's Hierarchy Manager *prevents* a match rule from merging a
  branch into its HQ, or merely models the relationship alongside.
- No Profisee-specific documentation was retrieved.

---

## Q2. Choosing blocking / tokenization keys generally

### Takeaway
There are two well-documented, dataset-independent families: **schema-agnostic (token) blocking**,
which needs nothing but the attribute values and is explicitly designed for heterogeneous
sources, and **unsupervised blocking-key selection**, which scores every attribute on
*discriminability × coverage* and picks keys by that score. Both replace the hand-picked key.
The cost of schema-agnostic blocking is catastrophic precision, which is why it is always paired
with meta-blocking.

### Cited Findings

**Schema-agnostic / token blocking — precise definition**
- "Schema-agnostic blocking (also called Token Blocking) creates blocks by using parts of any
  attribute value in each entity as signatures, rather than relying on predefined schema
  knowledge. This approach requires only the raw entity profiles themselves as input — no domain
  expertise or structural metadata." — [Generalized Supervised Meta-blocking (arXiv 2204.08801)](https://arxiv.org/html/2204.08801)
- Mechanically: the data is tokenized into a bag of tokens and **all entities sharing a token go
  into the same block**; attribute names and attribute boundaries are ignored entirely
  — [Papadakis et al., VLDB vol. 9](https://doi.org/10.14778/2856318.2856326)
  (**from search excerpt; the PDF fetch failed**)
- Why it suits heterogeneous sources: it "require[s] no domain knowledge and applies to data of
  any structuredness and schema heterogeneity" — works uniformly over relational, RDF and
  semi-structured data — [arXiv 2204.08801](https://arxiv.org/html/2204.08801)
- Contrast with schema-based: "schema-based configuration relies on schema information to select
  signatures of high distinctiveness and low noise, the schema-agnostic one treats every token
  from all attribute values as a signature" — and schema-agnostic "requires no fine-tuning by
  human experts" — [Papadakis et al., VLDB vol. 9](https://doi.org/10.14778/2856318.2856326)
  (**search excerpt only**)
- Stated experimental conclusion: "schema-agnostic signatures outperform schema-based ones,
  without requiring domain or schema knowledge" — same source, **search excerpt only; I could
  not read the numbers and do not have them.**

**The precision cost, and meta-blocking as the general fix**
- Token blocking puts every entity in many blocks, "thus yielding numerous redundant and
  superfluous comparisons" — [arXiv 2204.08801](https://arxiv.org/html/2204.08801)
- Measured on real datasets: recall is "almost perfect… significantly exceeds 90%" but
  "precision is consistently quite low, as its highest value is lower than **0.003**," with F1
  "far below 0.1 across all datasets" — [arXiv 2204.08801](https://arxiv.org/html/2204.08801)
- Meta-blocking repairs this in two dataset-independent stages:
  (a) **weighting** from co-occurrence statistics — "the more blocks they share and the more
  distinctive (i.e., infrequent) the corresponding signatures are, the more likely they are to
  match"; (b) **edge pruning** — weight-based (global or local threshold) or cardinality-based
  (top-K candidates per entity) — [arXiv 2204.08801](https://arxiv.org/html/2204.08801)

**Unsupervised blocking-key selection**
- "The harmonic mean of discriminability and coverage is computed for all valid attributes, with
  the one having the maximum score selected for defining blocking keys" —
  [Unsupervised Blocking Key Selection for Real-Time Entity Resolution (Springer, 2015)](https://link.springer.com/content/pdf/10.1007/978-3-319-18032-8_45.pdf)
- A later generalization uses **multi-criterion** selection: "key coverage, block sizes, and
  blocks distribution… more comprehensive than approaches that only consider coverage" —
  [A generalized approach to perform unsupervised blocking key selection for entity resolution (Information Sciences)](https://www.sciencedirect.com/science/article/abs/pii/S0020025525010898)
- Blocking-key discovery has also been cast as a **Fisher feature-selection problem** —
  [An unsupervised blocking technique for more efficient record linkage (QUB open access)](https://pureadmin.qub.ac.uk/ws/files/175528318/An_Unsupervised_Blocking_Technique_For_More_Efficient_RL.pdf)
- Learned/active variants exist for when some labels are affordable —
  [An Unsupervised Algorithm for Learning Blocking Schemes](https://www.researchgate.net/publication/271556427_An_Unsupervised_Algorithm_for_Learning_Blocking_Schemes),
  [Active Blocking Scheme Learning for Entity Resolution](https://www.researchgate.net/publication/325863656_Active_Blocking_Scheme_Learning_for_Entity_Resolution)
- An alternative that sidesteps key choice entirely: approximate-nearest-neighbour blocking over
  vectorised records — [BlockingPy (arXiv 2504.04266)](https://arxiv.org/pdf/2504.04266)

**How a mature tool lets users configure keys without hardcoding one**
- Splink's documented posture is a **union of several rules**, not one chosen key: "specify
  multiple blocking rules such that it becomes highly implausible that a true match would not
  meet at least one of these blocking criteria… Generally between about 3 and 10 rules are
  recommended" — [Splink, What are Blocking Rules?](https://moj-analytical-services.github.io/splink/topic_guides/blocking/blocking_rules.html)
- The rules are *measured*, not guessed: `count_comparisons_from_blocking_rule()` reports the
  pair count a candidate rule generates, and the recommended workflow is to "get a model up and
  running with strict blocking rules and incrementally loosen them to see the impact on runtime
  and quality" — [Splink, Blocking Rule Performance](https://moj-analytical-services.github.io/splink/topic_guides/blocking/performance.html)
- Splink deliberately separates **model-training** blocking rules from **prediction** blocking
  rules, because training rules are chosen to shrink the training set — [Splink, Model Training Blocking Rules](https://moj-analytical-services.github.io/splink/topic_guides/blocking/model_training.html),
  [Splink, Prediction Blocking Rules](https://moj-analytical-services.github.io/splink3_legacy_docs/topic_guides/blocking/predictions.html)

**INPUT REQUIRED**
| Mechanism | Input | Available with 2 flat PG sources? |
|---|---|---|
| Token blocking (schema-agnostic) | Attribute *values* only; no schema, no names, no labels | **Yes, immediately.** This is the single most directly applicable general mechanism found |
| Attribute-clustering blocking (restricts tokens to similar attributes across schemas) | Values from both schemas | Yes (see Gaps — definition not verified) |
| Meta-blocking weighting + pruning | The blocks produced above; co-occurrence counts. Unsupervised variants need no labels | **Yes** |
| *Supervised* meta-blocking | Labelled matching/non-matching pairs | No, without labels |
| Unsupervised blocking-key selection (discriminability × coverage harmonic mean) | Per-attribute value distributions; a notion of "valid attribute" | **Yes** — computable from `pg_stats`-style statistics or a single scan |
| Multi-criterion variant (coverage + block size + block distribution) | Same, plus a block-size budget | Yes |
| ANN/embedding blocking (BlockingPy) | Records vectorisable to text/embeddings | Yes, with a model dependency |

### Inferences
- The `recall > 0.90 / precision < 0.003` figure is the key number for judging a home-grown
  implementation: a hand-picked blocking key that yields *high* candidate precision is doing the
  work meta-blocking is supposed to do, and is therefore the most likely place for a key to have
  been fitted to one dataset's known matches. A general pipeline expects garbage-heavy candidate
  sets and prunes them statistically.
- The Splink pattern (3–10 rules whose *union* makes a missed true match implausible, each
  measured by generated-pair count) is the configurability answer: keys are *parameters with
  measurable cost*, not code. A system with one hardcoded key has no such dial.
- Discriminability × coverage and IDF token weighting (Q1) are the same idea applied at different
  granularity — both replace a human's guess about which tokens matter with a count taken from
  the corpus at hand. Both survive a source swap; a hardcoded list does not.

### Gaps
- I could not read the Papadakis VLDB paper or the CSUR survey, so I have **no quantitative
  schema-agnostic-vs-schema-based comparison** — only the qualitative conclusion from search
  excerpts. The report should not assert numbers here.
- **Attribute Clustering Blocking** was in my target list but I found no readable definition; do
  not describe it in detail without a source.
- The discriminability and coverage formulas themselves (exact definitions) were not retrieved —
  only the fact that their harmonic mean is the selection criterion.

---

## Q3. Deriving relationship edges generally, from arbitrary schemas

### Takeaway
There is a mature, purely data-driven pipeline for this and it needs **only the column values**:
discover **inclusion dependencies (INDs)** with SPIDER/BINDER/FAIDA, then **classify which INDs
are foreign keys** using structural features (Rostin et al.). Junction-table detection on top of
that is the weakest link — the widely-implemented "table with exactly two FKs" heuristic is
documented in the wild as producing **false positives on payload-carrying domain tables**.

### Cited Findings

**Inclusion-dependency discovery — the general substrate**
- Definition: an IND `R ⊆ S` holds when "for every tuple in R there exists a tuple in S where the
  values match"; INDs are "primarily used in foreign key discovery, which aims to suggest
  possible joins over tables" — [Fast Discovery of Inclusion Dependencies with Desbordante (arXiv 2608.02213v1)](https://arxiv.org/html/2608.02213v1)
- "Inclusion dependency is a relation between attributes of tables that indicates possible
  Primary Key–Foreign Key references. Apart from the detection of foreign key relationships, INDs
  can help to perform data integration, query optimization, integrity checking, or schema
  (re-)design." — same source
- **Crucially for this project**: IND discovery "operates directly on raw data… without requiring
  pre-declared database constraints," motivated precisely by constraints being "lost during data
  export, or to not have been defined in the first place" — [arXiv 2608.02213v1](https://arxiv.org/html/2608.02213v1)
- SPIDER: "Single Pass Inclusion DEpendency Recognition… solves this task very efficiently by
  testing all attribute pairs in parallel," using a disk-based sort-merge join for
  single-column INDs — [Bauckmann & Leser, Efficiently Detecting Inclusion Dependencies](https://hpi.de/oldsite/fileadmin/user_upload/fachgebiete/naumann/publications/PDFs/2007_bauckmann_efficiently.pdf)
  (search excerpt; PDF not text-extracted)
- BINDER: divide-and-conquer, hash-based, handles **both unary and n-ary** INDs on very large
  datasets, and is "up to **26x faster than Spider** and more than **2500x faster than Mind**"
  — [Divide & conquer-based inclusion dependency discovery (PVLDB)](https://dl.acm.org/doi/10.14778/2752939.2752946)
- FAIDA: approximate, using HyperLogLog sketches plus inverted indexes; "guarantees completeness
  but permits false positives," and the authors report its "false positive rate is quite small"
  with experimental error rates described as "insignificant" — [arXiv 2608.02213v1](https://arxiv.org/html/2608.02213v1)
- Candidate explosion: "The number of potential IND candidates grows combinatorially with column
  count" — [arXiv 2608.02213v1](https://arxiv.org/html/2608.02213v1)

**IND → foreign key: classification with structural features**
- Rostin et al. "defined a set of characteristics and used them as features fed into a
  state-of-the-art classification algorithm to classify inclusion dependencies (INDs) as foreign
  key constraints (FKCs) or not," evaluated "using cross-validation on **six different databases
  from different domains** using **four different classification methods**" —
  [A Machine Learning Approach to Foreign Key Discovery (WebDB 2009)](https://hpi.de/fileadmin/user_upload/fachgebiete/naumann/publications/PDFs/2009_rostin_a.pdf)
- The feature families are: **column-name similarity**, **value coverage / dependent-vs-referenced
  ratios**, **value typicality** (do the values look like keys), **relative table sizes**, and
  **out-of-range detection** — same source (feature families confirmed, **exact formulas not
  extracted — the PDF was not text-extractable**)
- Reported outcome: "roughly identical precision and recall. The differences between precision
  and recall were rather small (for instance, only **5% on average for J48**)," and overall "the
  results show that foreign key discovery can be tackled quite successfully with machine learning
  techniques and that their set of features is well capable of separating true foreign keys from
  false positives" — same source, **via search excerpt; absolute precision/recall values were
  not obtained**
- A holistic, later alternative exists (HoPF, primary + foreign key detection jointly) —
  [Holistic primary key and foreign key detection, JIIS](https://link.springer.com/article/10.1007/s10844-019-00562-z)
  — **I could not read this paper (auth redirect); no claims drawn from it.**
- Another FK-identification line of work — [Foreign Key Constraint Identification in Relational Databases (CEUR Vol-1885)](https://ceur-ws.org/Vol-1885/106.pdf)
  — PDF, not read.
- Recent LLM-based FK detection exists — [LLM-FK (arXiv 2603.07278)](https://arxiv.org/pdf/2603.07278)
  — surfaced in search, not read; flagged only so the writer knows the line exists.

**Junction / associative table detection, including payload-carrying ones**
- The structural definition in circulation: "Junction (or associative) tables are non-prime
  tables whose primary key columns are foreign keys" — [DZone, Using Junction or Associative Tables in EF Core](https://dzone.com/articles/using-junction-or-associative-tables-in-entity-fra)
- Tooling does implement automatic detection: IntelliJ/JPA Buddy "can automatically detect
  junction tables and identify the relation cardinality between two tables whose ids are
  represented as foreign keys in the junction table as `@ManyToMany`" —
  [JetBrains IntelliJ IDEA docs, Reverse Engineering](https://www.jetbrains.com/help/idea/jpa-buddy-reverse-engineering.html);
  EF Core tracked the same feature — [dotnet/efcore issue #22475](https://github.com/dotnet/efcore/issues/22475)
- **The documented failure mode, filed as a bug**: `drizzle-kit pull` "treats domain tables with
  exactly two foreign keys as junction tables." The report states that "the presence of exactly
  two foreign keys is not enough to establish that a table is a junction, as a domain entity can
  legitimately reference exactly two other tables. Primary keys, unique constraints, non-FK
  columns, and the general table structure need to be considered," and concludes that
  "generating the direct foreign-key relations unconditionally would be safer than replacing
  them with an inferred through-relation" —
  [drizzle-orm issue #6253](https://github.com/drizzle-team/drizzle-orm/issues/6253)
- The same thread gives the discriminating signal practitioners actually use: a *pure* junction
  takes the **composite primary key of the two FKs**; the moment the table "accrues a lifecycle"
  (its own surrogate id, its own timestamps/payload, a unique-together constraint instead of a
  composite PK), it is a domain entity, not a join table — same issue.
- Relationship-aware ER (propagating evidence along discovered edges rather than scoring pairs
  independently): "a multi-relational approach performs simultaneous inference for all candidate
  pairs, and allows information to propagate from one candidate match to another via the
  attributes they have in common… based on conditional random fields… optimal solution… in
  polynomial time using a graph cut algorithm" — [Parag & Domingos, Multi-Relational Record Linkage (MRDM 2004)](https://homes.cs.washington.edu/~pedrod/papers/mrdm04.pdf)

**INPUT REQUIRED**
| Mechanism | Input | Available with 2 flat PG sources, no declared FKs? |
|---|---|---|
| IND discovery (SPIDER / BINDER / FAIDA) | **Column values only.** Explicitly motivated by missing/undeclared constraints | **Yes — this is the mechanism the project should be using.** No labels, no DDL, no schema knowledge |
| n-ary (multi-column) INDs | Same, more compute; BINDER is the scalable option | Yes |
| IND→FK classification, Rostin features | The INDs, plus **column names**, **table row counts**, **value distributions** | **Yes** for the features; the *classifier* needs FK labels from other databases (transfer assumption) |
| IND→FK by rule/heuristic instead of ML | Same features, hand-set thresholds | Yes — **but this is exactly where a threshold fitted to one schema pair hides** |
| Junction-table detection (pure) | Discovered FKs + PK/unique-constraint metadata | Partially — PG exposes PK/unique constraints even when FKs are absent |
| Junction-table detection (payload-carrying) | The above **plus** a rule over non-FK column count / surrogate-id presence | Yes, but see the drizzle bug: precision is the problem, and no published precision/recall exists |
| Multi-relational / collective ER (Parag & Domingos) | A derived relational graph + a pairwise model | Yes, once edges exist |

### Inferences
- The honest general chain for this project is: **PG catalogue (PK/unique only) → IND discovery on
  values → Rostin-style structural features → FK candidate scores → junction detection as a
  *report*, not a silent rewrite**. Every step's input is satisfied by two flat tables and their
  contents. A system that instead consults a list of known table names is doing at step 1 what
  the literature does at step 3, and will not transfer.
- The drizzle bug is the best available evidence that "two FK columns ⇒ junction" is a **known
  false-positive generator**, and its resolution advice ("generate the direct FK relations
  unconditionally" rather than inferring a through-relation) is the conservative default a
  general implementation should adopt. This is practitioner evidence, not a measured precision
  figure.
- FAIDA's trade (guaranteed completeness, small false-positive rate) is the right shape for an ER
  pre-pass: over-generate edges cheaply, then filter. The same asymmetry as blocking.

### Gaps
- **No junction-table-detection heuristic with published precision/recall was found.** The task
  asked for this specifically; the only quantified numbers I found anywhere in this area are
  BINDER's speedups (26x / 2500x) and Rostin's precision≈recall-within-5% statement. The report
  should say the precision/recall evidence for junction detection does not exist in the sources I
  could reach.
- Rostin's **absolute** precision and recall per database/classifier could not be extracted (PDF
  unreadable in this environment). Only the precision≈recall relation is citable.
- HoPF and the CEUR FK-identification paper were not readable; any comparison between FK-detection
  approaches would be unsupported.
- I found no source on IND discovery specifically **across two independent databases** (as opposed
  to within one schema). Cross-source INDs are a different, weaker signal — values overlapping
  across two systems may indicate a shared code list rather than a reference. Flagging this as an
  open question, not a finding.

---

## Q4. Anti-patterns and evaluation discipline — how to tell a general rule from one fitted to the data at hand

### Takeaway
The most directly relevant published warning is not about ML overfitting at all: it is that
**optimising a metric that only counts duplicates found systematically drives a system toward
over-merging**, because the good outcome is visible in the output and the bad one is not. That is
the precise mechanism of "reward hacking" in an ER system. Compounding it, Hand & Christen showed
the F-measure itself is **not comparable across methods**, so a rule set tuned to maximise F on
one labelled set cannot be defended by that F.

### Cited Findings

**The reward-hacking mechanism, stated explicitly**
- "A program measured on duplicates alone will push toward over-merging, because duplicates are
  easy to count from the output while wrong merges are not." — [insightful-data-lab](https://insightful-data-lab.com/2026/03/15/entity-resolution/)
- The asymmetry that makes this expensive: a false merge "propagates a wrong identity into every
  downstream consumer and is fixable only by reconstructing what was combined," whereas a false
  split "is fixable later by merging" — [logiciel.io](https://logiciel.io/blog/entity-resolution)
- Therefore merge and split should be "two separate decisions rather than one," with separate
  thresholds, "since errors carry different costs" — [logiciel.io](https://logiciel.io/blog/entity-resolution)

**The metric itself is not a safe optimisation target**
- Hand & Christen identify a conceptual flaw: "the relative importance assigned to precision and
  recall should be an aspect of the problem and the researcher or user, but not of the particular
  linkage method being used." They show the F-measure "can be expressed as a weighted sum of
  precision and recall, with weights which depend on the linkage method being used," so
  the weight given to recall "depends upon the outcome of the evaluated classification method."
  They propose alternatives. Published 2018, *Statistics and Computing* —
  [A note on using the F-measure for evaluating record linkage algorithms](https://link.springer.com/article/10.1007/s11222-017-9746-6)
  ([also via ACM](https://dl.acm.org/doi/10.1007/s11222-017-9746-6))
- A more recent treatment of the same problem area — [Consistently Evaluating Record Linkage Classifiers (ACM 10.1145/3786768)](https://dl.acm.org/doi/pdf/10.1145/3786768)
  — **surfaced in search but not read; listed as a pointer only.**

**Labelled-set pathologies specific to ER**
- The class imbalance is structural, not incidental: "1000 entities can create almost 2 million
  negative examples," so "the number of training instances with labels indicating that records do
  not belong to the same entity increases exponentially relative to the number of given
  entities" — [An Entity Resolution approach to isolate instances of Human Trafficking online (arXiv 1509.06659)](https://arxiv.org/pdf/1509.06659)
- Random negative sampling is the trap: "the selected samples are not likely to cover the most
  challenging training samples given the nature of entity resolution" — same source
- "Unbalanced training sets have been well documented to have a degrading effect on machine
  learning algorithm performance" — same source
- Rule-based and learned approaches fail differently, and the comparison depends on where you set
  the tier: "fixed weighted rules have higher F1 scores at chosen tiers while learned classifiers
  yield higher auto-accept precision at the cost of recall and coverage" —
  [entity-resolution PR #1 discussion](https://github.com/cbratkovics/entity-resolution/pull/1)
  (a GitHub PR, i.e. a single practitioner's measurement on one project — **weak source, use only
  as illustration**)

**Provenance as the audit mechanism**
- Rule-based matching's practical advantage is that the output carries "the rule number used to
  generate that match," where ML carries "a confidence score in each group" on a 0.0–1.0 scale —
  [insightful-data-lab](https://insightful-data-lab.com/2026/03/15/entity-resolution/)
- Survivorship must be declared, not emergent: "Which email survives a merge should be a rule,
  not an accident of ingestion sequence. Configure per attribute," on recency, source authority,
  or completeness — [logiciel.io](https://logiciel.io/blog/entity-resolution)

**Blocking-specific leakage risk, documented by a tool**
- Splink separates model-training blocking rules from prediction blocking rules precisely because
  training rules are chosen for a different objective — "to reduce the runtime of model training
  by choosing training rules that reduce the number of record pairs in the training set" —
  [Splink, Model Training Blocking Rules](https://moj-analytical-services.github.io/splink/topic_guides/blocking/model_training.html)
- Blocking choices are to be justified by a measured pair count, not by outcome quality on the
  labelled set — [Splink, Blocking Rule Performance](https://moj-analytical-services.github.io/splink/topic_guides/blocking/performance.html)
- Work on "deriving the method's control parameters using results of deterministic matching" for
  a *generalisable* pipeline — [GLADIS: a generalisable linkage pipeline](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10929359/)
  (surfaced in search; **abstract-level only, not read in full**)

**Anti-pattern catalogue (each item traced to a source above)**
1. **Scoring on duplicates found.** Drives over-merging because false merges are not visible in
   the output — [insightful-data-lab](https://insightful-data-lab.com/2026/03/15/entity-resolution/).
   *Tell*: the reported metric has no false-merge term.
2. **A single threshold for both merge and split.** Contradicts the cost asymmetry —
   [logiciel.io](https://logiciel.io/blog/entity-resolution). *Tell*: one number governs both.
3. **Comparing rule sets by F-measure.** The recall weight is method-dependent, so the comparison
   is not valid — [Hand & Christen](https://link.springer.com/article/10.1007/s11222-017-9746-6).
   *Tell*: "we tuned until F went up."
4. **A hardcoded keyword/suffix list as the duplicate-vs-subsidiary discriminator.** Documented to
   order the hard pairs wrongly — [Tilores](https://tilores.io/content/company-name-normalization-isnt-enough-for-fuzzy-matching/).
   *Tell*: the list names tokens that happen to occur in *this* corpus; swap the corpus and it is
   silent. The general replacement (`log(N/count(token))`) re-derives itself.
5. **"Exactly two FKs ⇒ junction table."** Filed as a bug against a shipping tool; PKs, unique
   constraints and non-FK columns must be considered —
   [drizzle-orm #6253](https://github.com/drizzle-team/drizzle-orm/issues/6253).
6. **Hand-picked blocking key with suspiciously high candidate precision.** The general
   (schema-agnostic) baseline has precision **below 0.003** at >90% recall —
   [arXiv 2204.08801](https://arxiv.org/html/2204.08801). *Tell*: a key that generates few, mostly
   correct candidates was almost certainly chosen with knowledge of the answers.
7. **Random negative sampling for rule/model validation.** Misses the hard negatives, which are
   the only ones that discriminate — [arXiv 1509.06659](https://arxiv.org/pdf/1509.06659).
8. **Tuning blocking against match quality on the labelled set.** Splink's separation of training
   vs prediction rules exists to keep these objectives apart —
   [Splink](https://moj-analytical-services.github.io/splink/topic_guides/blocking/model_training.html).

### Inferences
These are *my* inferences, not sourced claims, offered because the task asked how to tell a general
rule from a fitted one:
- The decisive test for this project is a **held-out source pair, not held-out rows**. Row-level
  holdout from the same two databases cannot detect a rule keyed to those databases' vocabulary,
  table names, or value conventions; only a third source can. This follows directly from
  schema-agnostic blocking's stated motivation (works "without requiring domain or schema
  knowledge") — a rule that needs the schema will fail the source swap and nothing else will
  reveal it.
- A mechanical smell test that needs no new data: **grep the rule set for literals drawn from the
  corpus**. Any constant that is a token, table name, column name, or value from the two source
  databases is a fitted parameter. Its general counterpart is a *count over the corpus*
  (IDF, discriminability × coverage, co-occurrence weight) that produces the same constant at
  runtime. Every general mechanism in Q1–Q3 has this shape; the hardcoded variants do not.
- Writing the scorer before the run (already the project's own stated discipline) maps exactly onto
  Hand & Christen's point: fix the precision/recall trade-off as a property of the *problem*
  before any method is evaluated, so no method can choose its own weighting.
- Rule-provenance output (which rule fired) is the cheap audit that makes over-fitting visible:
  if one rule accounts for most matches on source pair A and near-zero on source pair B, that rule
  is fitted. This is an inference from the provenance property insightful-data-lab describes, not a
  published method.

### Gaps
- **I found no paper specifically on over-fitting *hand-written ER rules* to a single ground-truth
  dataset.** The literature discusses overfitting of *learned* models and flaws in *metrics*; the
  hand-tuned-rules case is covered only by practitioner writing (logiciel.io,
  insightful-data-lab, Tilores) and by the tool-design choices in Splink. The report should be
  explicit that this specific worry is under-served by the formal literature.
- Several searches surfaced **US patents** on "automatic entity resolution with rules detection and
  generation" (US 11,816,078 / 10,997,134 / 12,566,739) which may bear on automatically *generating*
  rules rather than hand-writing them. I did not read them; patent claims are also poor evidence of
  practice.
- No train/test protocol standard for ER rule sets (analogous to a benchmark suite) was found.
- The two user-supplied pages (logiciel.io, insightful-data-lab) were read in full and **do not
  cover** blocking key selection, relationship-edge derivation, hierarchy/LEI approaches, or
  overfitting/evaluation discipline by name. Their contribution is confined to the error-asymmetry
  and matching-vs-merging arguments cited above. This should not be presented as broader coverage
  than it is.
