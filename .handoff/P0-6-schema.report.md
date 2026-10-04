# P0-6-schema: BEFORE baseline for P1, P3, P17 (derive/baseline mode; no fix implemented)

Files: tests/part1/fixtures_schema.py (planted truth), tests/part1/baseline_schema.py (runner),
tests/part1/before_schema.json (numbers; run twice, byte-identical). Lint+format clean. No src/ edits.
Reproduce: `export TMPDIR=$PWD/.tmp; .venv311/bin/python -m tests.part1.baseline_schema`
No DB, no LLM: map_fields_impl(llm_config=None, connector=None); join_graph.create_connector and
analyze_dependencies.LLMClient replaced at runtime by in-memory stubs (LLM stub made 0 calls); destination is a recording stub.
2.4 grep over my three files: no hits.

## P1 - concatenated-lowercase columns (entry: map_fields_impl, heuristic tiers only)
Destination `org`: id + 11 delimited columns. 3 sets of 14 source fields, 11 should map, 3 planted must-stay-unmapped
(legacyflag, colorcode, presspoint = the report's ambiguous-split example).
| set | correct | missed | wrong target | false map |
| concat (orglabel...) | 7 | 4 | 0 | 0 |
| camel (orgLabel...)  | 11 | 0 | 0 | 0 |
| snake (org_label...) | 11 | 0 | 0 | 0 |
Missed (concat): foundingdate -> founded_date, taxregistration -> tax_registration_number,
contactemail -> primary_contact_email, postalcode -> mailing_postal_code. All have tokens == [the whole string]
and reason "No matching destination column found; destination schema is authoritative" (matches the report's wording).
Per-field tokens/score/reason for every field are in before_schema.json p1.*.fields.
SURPRISE: the current code ALREADY maps 7/11 concatenated fields (orglabel, legaltitle, postaladdress, phonenumber,
employeecount, websiteurl, parentorgref) at 0.85 via the `s.replace("_","") == d.replace("_","")` collapse rule in
_heuristic_match, which ignores the tokeniser. So "companyname never maps" only holds when the destination does NOT carry the
delimiter-stripped twin. The still-failing shape is: inflection differs (founding/founded) or source is a strict subset of the
destination's parts (taxregistration vs tax_registration_number). A fix verified only on the 7 already-passing fields proves nothing;
the acceptance set is the 4 MISSED + the 3 must-stay-unmapped (a naive split-everything fix could falsely map presspoint/legacyflag).

## P3 - child column not matching <parent>id (entries: _find_candidate_edges, build_join_graph, _join_graph_to_dependency_tree, analyze_dependencies node)
Fixture: origin o1, no declared PK/FK (Mongo/ClickHouse-style), tables org(_id 1..200, org_code), member(owner_ref in org_code),
note(org_id in org_code), ticket(orgid = 1..60, coincidentally inside org._id).
Planted TRUE edges: member.owner_ref->org.org_code; note.org_id->org.org_code. Planted FALSE edge: ticket.orgid->org._id.
Planted true tree: {org:[], member:[org], note:[org], ticket:[]}.
BEFORE:
- candidates proposed: note.org_id->org._id (wrong parent col), ticket.orgid->org._id. member.owner_ref NEVER proposed.
- note.org_id->org._id proposed then rejected by the inclusion check (overlap 0) = the report's companyexperience.companyid shape. True edge lost.
- Graph edges: exactly one, ticket.orgid->org._id verified at overlap 1.0 = the DECOY IS ACCEPTED as a verified edge.
- true edges in graph: 0 of 2. decoy accepted: yes.
- _join_graph_to_dependency_tree: tree = {member:[], note:[], org:[], ticket:[org]}, fully_covered=True, 1 confirmed edge. tree != truth.
- analyze_dependencies node (LLM stub, 0 calls): origin ALGORITHMIC, dependency_tree same as above, column_dependencies = [ticket.orgid->org._id].
SURPRISE / mismatch with report: dependency_tree is NOT `{}` here. It is a non-empty but WRONG tree (only the false edge), and the node declares
itself "fully covered" so the LLM is never consulted - worse than empty, since it is confident. The report's literal `{}` arises when
no coincidental decoy exists; I did not add a second fixture for that (acceptance can use `n_nonempty_dependencies` / tree == truth).
A fix must both find the 2 true edges and drop the decoy; value containment alone cannot distinguish the decoy (it IS contained in org._id) - the
report's cliff/RHS-uniqueness filters are what should do that, so this decoy is a real test of them. Note: org_code is unique and org._id is
also unique, so uniqueness alone will not reject the decoy either; I did not try to make the fixture "solvable" - the verifier/worker should judge.

## P17 - single-`id` destination table (entry: load_data._load_all_entities -> load_data_impl, recording stub destination)
Destination `topic_link(id)`; 40 source link rows each with org_ref and topic_ref (80 linkage values).
BEFORE:
- map_fields_impl: field_mapping {} ; both fields unmapped with reason "No matching destination column found; destination schema is authoritative"; schema_changes [].
- Load: 40 rows written to topic_link, every row shape == (id,) ; 0 non-id values persisted; 80 linkage values lost.
- load_results inserted=40; failed_entities=[]; errors=[]; defect_reported = False (nothing at load time names the table, missing columns or row count).
- SQL recorded: `CREATE TABLE IF NOT EXISTS ... (id TEXT PRIMARY KEY)` (ensure_table, a no-op on an existing table, issued once per entity) and one INSERT (id) ... DO NOTHING.
  0 ALTER / add_column / extend_schema_batch calls. So the "no new columns" rule holds; the failure is silent loading, not DDL.
LIMITS (stated, not hidden): not driven through load_data_node / the pre-load gate (needs state, spool, real connector); the stub's load_batch mirrors
PostgreSQLDestinationConnector.load_batch's column filtering (read from source, not run against Postgres). The report's "reported at the pre-load gate" part of P17
was therefore not exercised; the defect-report check for a fix must be added where the gate lives. Note that with an EMPTY mapping, _transform_records passes ALL
source fields through, and it is load_batch's column filter that discards org_ref/topic_ref - worth knowing for P17's design.

## Not reproduced / not attempted
- Real artifacts (state.json, gates.jsonl, score.json) not touched; live DBs not touched.
- Cross-origin `taken`/coalesce effects in P1 (single origin only).
