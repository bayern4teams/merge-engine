# ADR 0016: Splink workbench model versions are an immutable file store, not a database table

**Status:** accepted, part of the B.4 Splink workbench (ADR 0004's follow-on); recorded
separately because the storage design was left to us by the plan (`Branch_Plan.md` B.4,
`Splink.txt` §44-§46 name *what* S-N must do — version, compare, activate, roll back — not
*how* to store it).

## Context

Every S-G/H/I training run produces a trained Splink model: the `save_model_to_json()` payload
(16 keys in installed 4.0.16 — comparisons with their trained m/u, the prior, prediction
blocking rules, TF prefixes, link type, unique-id column) plus provenance Splink itself does not
record — the training session records, which blocking rules trained it, what labels it was
evaluated against, its accuracy results, its parent version, and a lifecycle status. Some way to
keep more than one of these around, compare them, and switch which one is "live" was needed
before S-J (predict) or S-O (cluster) could load a specific trained model rather than always
retraining in-request.

## Decision

Store each trained model as an immutable version under
`<store_root>/<model_name>/v<N>/{model.json, metadata.json}` — a plain file store, not a
database table, with four load-bearing properties:

* **One directory per version, two files.** `model.json` is the payload exactly as
  `save_model_to_json()` returned it (byte-stable — passed an `out_path` so the file on disk
  and the in-memory dict are provably the same object); `metadata.json` is everything Splink
  doesn't track (training records, blocking rules, labels, accuracy, parent version, status).
  This matches the project's existing convention of run artefacts living under `logs/`
  (`logs/spool/<run_id>.duckdb`) and needs no new dependency: a version is a file a human can
  `cat`, diff, and restore by hand if the app is unavailable. A database would be the wrong
  tool here — the payload is an opaque blob to any query engine, and the only queries this
  module needs (list a model's versions, diff two of them) are trivial directory/JSON
  operations.
* **Versions are immutable.** Nothing is ever edited in place: retraining or hand-editing a
  config both *create a new version* with `parent_version` set to the one that changed. This
  makes rollback a one-pointer move instead of a data-recovery exercise, and it is §46's
  reproducibility requirement made structural — every number in a version traces to a parent
  plus one change, never a silent in-place mutation.
* **Activation is a pointer, not a copy.** `activate_version` writes
  `<store_root>/<model_name>/active.json` (`{"version": N}`); there is exactly one active
  version per model at a time, and rollback is "re-activate the previous entry in
  `active_history.json`". The pointer file is the only thing in the whole store that is ever
  mutated after being written.
* **A version must prove it reloads before it can be activated.** `save_version` immediately
  calls `round_trip_verify`: reload the just-written payload into a fresh `Linker` over the
  same training data and confirm its predictions match the original bit-for-bit. A version
  that fails this is still written (nothing is silently discarded) but `activate_version`
  refuses it — a version that cannot be reloaded is a broken artefact, and the store does not
  bless one as the model a production merge runs on.

## Consequences

- Loading a version for `predict`/`cluster`/`explain`/`evaluate` rebuilds a live `Linker` from
  the saved payload plus the caller's records — cheap (one DuckDB `CREATE TABLE`), because the
  expensive part (EM training) already happened once at `train` time and is never repeated to
  iterate on thresholds or review a different pair.
- No module-level state (CLAUDE.md): `store_root` is a parameter on every public function in
  `splink_model.py`, never a cached path or handle — two concurrent workbench requests against
  different (or the same) model never share mutable state.
- The store has no built-in retention or garbage collection: every trained version is kept
  until a human archives it (`archive_version`, a status change, not a deletion). This is
  deliberate for a tuning workbench where "what did version 3 actually do" is exactly the
  question S-N exists to answer, but means disk usage grows monotonically with training runs —
  cleanup, if ever needed, is a separate, explicit decision, not something this module does on
  its own initiative.
