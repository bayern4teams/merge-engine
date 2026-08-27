import { useEffect, useState } from "react";
import {
  splinkStatus,
  splinkRecommend,
  splinkTrain,
  splinkPredict,
  splinkExplain,
  splinkCluster,
  splinkEvaluate,
  splinkListModels,
  splinkGetModelVersion,
  splinkGetActiveConfig,
  splinkActivate,
  splinkRollback,
  splinkArchive,
  splinkCompare,
  SplinkStatus,
  SplinkModelVersionSummary,
} from "../api";

// B.4: the Splink workbench, a tuning surface over inline (profiled/sampled)
// records -- not a batch run. Design mirrors splink_workbench.py's own
// "model-first, never re-train to iterate" split: `recommend` is pure over
// records; `train` is the one step that writes a model version (S-N);
// predict/explain/cluster/evaluate all load that saved version instead of
// retraining. Cards are independent screens, like InvestigateScreen's, but
// share `records` and the trained model_name/version at this top level
// because the workbench's own steps are a pipeline, not four unrelated tools.

function parseJsonArray(text: string): { value: Record<string, any>[] | null; error: string | null } {
  if (!text.trim()) return { value: null, error: null };
  try {
    const parsed = JSON.parse(text);
    if (!Array.isArray(parsed)) return { value: null, error: "must be a JSON array" };
    return { value: parsed, error: null };
  } catch (e) {
    return { value: null, error: String(e) };
  }
}

function parseJsonObject(text: string): { value: Record<string, any> | null; error: string | null } {
  if (!text.trim()) return { value: null, error: null };
  try {
    const parsed = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return { value: null, error: "must be a JSON object" };
    }
    return { value: parsed, error: null };
  } catch (e) {
    return { value: null, error: String(e) };
  }
}

function recordLabel(r: Record<string, any>, i: number): string {
  const text = JSON.stringify(r);
  return `[${i}] ${text.length > 70 ? text.slice(0, 70) + "…" : text}`;
}

export default function SplinkWorkbenchScreen() {
  const [status, setStatus] = useState<SplinkStatus | null>(null);

  const [recordsText, setRecordsText] = useState("");
  const [semanticTypesText, setSemanticTypesText] = useState("");
  const records = parseJsonArray(recordsText);
  const semanticTypes = parseJsonObject(semanticTypesText);

  const [modelName, setModelName] = useState("");
  const [activeVersion, setActiveVersion] = useState<number | null>(null);

  useEffect(() => {
    splinkStatus()
      .then(setStatus)
      .catch((e) => setStatus({ available: false, version: null, reason: String(e) }));
  }, []);

  return (
    <div>
      <div className="card">
        <h3 className="card-title">Splink workbench (B.4)</h3>
        <p className="card-hint">
          A tuning surface for the optional Splink matching engine, over records you paste in
          (typically a sample from the Investigate screen or a table export) — never a full
          table. Train a model here, then predict/explain/cluster/evaluate against it; nothing
          here is the production merge path unless you also set the entity's matching engine to
          "splink" in Settings.
        </p>
        {status === null && <div className="muted">Checking availability…</div>}
        {status && !status.available && (
          <div className="banner banner-warn">
            Splink is not available{status.reason ? `: ${status.reason}` : ""}. The optional
            dependency may not be installed.
          </div>
        )}
        {status && status.available && (
          <div className="banner banner-success">
            Splink {status.version} available (required major: {status.required_major}).
          </div>
        )}
      </div>

      <RecordsCard
        recordsText={recordsText}
        onRecordsTextChange={setRecordsText}
        recordsError={records.error}
        semanticTypesText={semanticTypesText}
        onSemanticTypesTextChange={setSemanticTypesText}
        semanticTypesError={semanticTypes.error}
        recordCount={records.value?.length ?? 0}
      />

      <RecommendCard records={records.value} semanticTypes={semanticTypes.value} />

      <TrainCard
        records={records.value}
        modelName={modelName}
        onModelNameChange={setModelName}
        onTrained={(v) => setActiveVersion(v)}
      />

      <ModelsCard
        modelName={modelName}
        activeVersion={activeVersion}
        onSelectVersion={setActiveVersion}
      />

      <PredictCard records={records.value} modelName={modelName} version={activeVersion} />
      <ExplainCard records={records.value} modelName={modelName} version={activeVersion} />
      <ClusterCard records={records.value} modelName={modelName} version={activeVersion} />
      <EvaluateCard records={records.value} modelName={modelName} version={activeVersion} />
    </div>
  );
}

// ── shared records input ────────────────────────────────────────────────────

function RecordsCard(props: {
  recordsText: string;
  onRecordsTextChange: (v: string) => void;
  recordsError: string | null;
  semanticTypesText: string;
  onSemanticTypesTextChange: (v: string) => void;
  semanticTypesError: string | null;
  recordCount: number;
}) {
  return (
    <div className="card">
      <h3 className="card-title">Records</h3>
      <p className="card-hint">
        Paste a JSON array of records (profiled/sampled rows — a real spool row shape, with its{" "}
        <span className="mono">_origin_id</span>/<span className="mono">_origin_table</span>/
        <span className="mono">_origin_id_row</span> lineage keys, works too and is stripped
        automatically). Every step below uses this same array.
      </p>
      <div className="field">
        <span className="section-label">Records (JSON array)</span>
        <textarea
          rows={6}
          value={props.recordsText}
          onChange={(e) => props.onRecordsTextChange(e.target.value)}
          placeholder='[{"name": "Ada Lovelace", "city": "London"}, {"name": "Ada Lovelase", "city": "London"}]'
        />
      </div>
      {props.recordsError && <div className="field-error">{props.recordsError}</div>}
      {!props.recordsError && props.recordCount > 0 && (
        <div className="muted">{props.recordCount} record(s) parsed.</div>
      )}
      <div className="field" style={{ marginTop: 10 }}>
        <span className="section-label">Semantic types (optional, JSON object: column → P-4 type)</span>
        <textarea
          rows={2}
          value={props.semanticTypesText}
          onChange={(e) => props.onSemanticTypesTextChange(e.target.value)}
          placeholder='{"name": "TEXT", "city": "TEXT"}'
        />
      </div>
      {props.semanticTypesError && <div className="field-error">{props.semanticTypesError}</div>}
    </div>
  );
}

// ── S-A/B/C/D + S-E — recommend ─────────────────────────────────────────────

function RecommendCard(props: {
  records: Record<string, any>[] | null;
  semanticTypes: Record<string, any> | null;
}) {
  const [result, setResult] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    if (!props.records || props.records.length === 0) {
      setError("add records first");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await splinkRecommend({
        records: props.records,
        semantic_types: props.semanticTypes || undefined,
      });
      setResult(r);
    } catch (e) {
      setError(String(e));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  const fields: Record<string, any> = result?.fields || {};

  return (
    <div className="card">
      <h3 className="card-title">Recommend (S-A/B/C/D)</h3>
      <p className="card-hint">
        Read-only: measured completeness/uniqueness/collision rate per field, a recommended
        comparator, the observed similarity distribution, and a term-frequency call — all backed
        by numbers from your own records, never a universal default.
      </p>
      <button className="btn btn-primary" onClick={run} disabled={loading || !props.records?.length}>
        {loading ? "Analysing…" : "Get recommendations"}
      </button>
      {error && <div className="banner banner-error" style={{ marginTop: 10 }}>{error}</div>}
      {result && (
        <div style={{ marginTop: 14 }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Field</th>
                <th>Matching</th>
                <th>Blocking</th>
                <th>TF</th>
                <th>Comparator</th>
                <th>TF recommended</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(fields).map(([col, f]: [string, any]) => (
                <tr key={col}>
                  <td className="mono">{col}</td>
                  <td title={f.matching?.reason}>{"★".repeat(f.matching?.stars || 0)}</td>
                  <td title={f.blocking?.reason}>{"★".repeat(f.blocking?.stars || 0)}</td>
                  <td title={f.tf?.reason}>{"★".repeat(f.tf?.stars || 0)}</td>
                  <td className="mono">{f.comparison?.comparator}</td>
                  <td>{f.term_frequency?.recommended ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <details style={{ marginTop: 10 }}>
            <summary>Full recommendation (raw)</summary>
            <pre className="code-block">{JSON.stringify(result, null, 2)}</pre>
          </details>
        </div>
      )}
    </div>
  );
}

// ── S-G/H/I + S-N — train ────────────────────────────────────────────────────

function TrainCard(props: {
  records: Record<string, any>[] | null;
  modelName: string;
  onModelNameChange: (v: string) => void;
  onTrained: (version: number) => void;
}) {
  const [comparisonsText, setComparisonsText] = useState(
    '[{"comparator": "NameComparison", "field": "name"}, {"comparator": "ExactMatch", "field": "city"}]'
  );
  const [blockingRulesText, setBlockingRulesText] = useState("city = city");
  const [trainingRulesText, setTrainingRulesText] = useState("");
  const [deterministicRule, setDeterministicRule] = useState("");
  const [seed, setSeed] = useState(42);
  const [recall, setRecall] = useState(0.7);
  const [result, setResult] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const comparisons = parseJsonArray(comparisonsText);

  async function run() {
    if (!props.records || props.records.length === 0) {
      setError("add records first");
      return;
    }
    if (!props.modelName.trim()) {
      setError("enter a model name (the entity type this model is for)");
      return;
    }
    if (comparisons.error || !comparisons.value) {
      setError(comparisons.error || "comparisons must be a JSON array");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const config: Record<string, any> = {
        comparisons: comparisons.value,
        blocking_rules: blockingRulesText.split("\n").map((s) => s.trim()).filter(Boolean),
        seed,
        recall,
      };
      const trainingRules = trainingRulesText.split("\n").map((s) => s.trim()).filter(Boolean);
      if (trainingRules.length) config.training_rules = trainingRules;
      if (deterministicRule.trim()) config.deterministic_rule = deterministicRule.trim();

      const r = await splinkTrain({ model_name: props.modelName.trim(), records: props.records, config });
      setResult(r);
      props.onTrained(r.version);
    } catch (e) {
      setError(String(e));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <h3 className="card-title">Train (S-G/H/I) &amp; save (S-N)</h3>
      <p className="card-hint">
        The only step that writes anything: trains u/m via EM over the blocking rule(s) below and
        saves the result as a new, immutable model version. Comparisons accept either{" "}
        <span className="mono">{"{comparator, field}"}</span> (hand-picked) or{" "}
        <span className="mono">{"{semantic_type, field}"}</span> (S-B's recommendation shape) —
        both build through the same comparator builder.
      </p>
      <div className="form-grid">
        <label className="field">
          <span className="section-label">Model name (entity type)</span>
          <input value={props.modelName} onChange={(e) => props.onModelNameChange(e.target.value)} placeholder="Company" />
        </label>
        <label className="field">
          <span className="section-label">Seed</span>
          <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
        </label>
        <label className="field">
          <span className="section-label">Prior recall assumption</span>
          <input
            type="number"
            step="0.05"
            min={0}
            max={1}
            value={recall}
            onChange={(e) => setRecall(Number(e.target.value))}
          />
        </label>
      </div>
      <div className="field" style={{ marginTop: 10 }}>
        <span className="section-label">Comparisons (JSON array)</span>
        <textarea rows={3} value={comparisonsText} onChange={(e) => setComparisonsText(e.target.value)} />
      </div>
      {comparisons.error && <div className="field-error">{comparisons.error}</div>}
      <div className="field" style={{ marginTop: 10 }}>
        <span className="section-label">Prediction blocking rules (one per line, e.g. "city = city")</span>
        <textarea rows={2} value={blockingRulesText} onChange={(e) => setBlockingRulesText(e.target.value)} />
      </div>
      <div className="field" style={{ marginTop: 10 }}>
        <span className="section-label">Training blocking rules (optional — S-F; defaults to the rules above)</span>
        <textarea rows={2} value={trainingRulesText} onChange={(e) => setTrainingRulesText(e.target.value)} />
      </div>
      <div className="field" style={{ marginTop: 10 }}>
        <span className="section-label">Deterministic rule for the prior estimate (optional)</span>
        <input value={deterministicRule} onChange={(e) => setDeterministicRule(e.target.value)} placeholder='name = name' />
      </div>
      <div className="btn-row" style={{ marginTop: 12 }}>
        <button className="btn btn-primary" onClick={run} disabled={loading || !props.records?.length}>
          {loading ? "Training…" : "Train & save version"}
        </button>
      </div>
      {error && <div className="banner banner-error" style={{ marginTop: 10 }}>{error}</div>}
      {result && (
        <div style={{ marginTop: 14 }}>
          <div className="banner banner-success">
            Saved version {result.version} (round-trip verified: {String(result.round_trip_verified)}).
          </div>
          <details style={{ marginTop: 10 }}>
            <summary>Training record (raw)</summary>
            <pre className="code-block">{JSON.stringify(result.training, null, 2)}</pre>
          </details>
        </div>
      )}
    </div>
  );
}

// ── S-N — model management ──────────────────────────────────────────────────

function ModelsCard(props: {
  modelName: string;
  activeVersion: number | null;
  onSelectVersion: (v: number) => void;
}) {
  const [models, setModels] = useState<Array<{ model_name: string; versions: SplinkModelVersionSummary[] }>>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [compareResult, setCompareResult] = useState<Record<string, any> | null>(null);
  const [compareA, setCompareA] = useState<number | null>(null);
  const [compareB, setCompareB] = useState<number | null>(null);
  const [viewedPayload, setViewedPayload] = useState<{ label: string; data: Record<string, any> } | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await splinkListModels();
      setModels(r.models);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function doActivate(name: string, version: number) {
    try {
      await splinkActivate(name, version);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function doArchive(name: string, version: number) {
    try {
      await splinkArchive(name, version);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function doRollback(name: string) {
    try {
      await splinkRollback(name);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function doCompare(name: string) {
    if (compareA == null || compareB == null) return;
    try {
      setCompareResult(await splinkCompare(name, compareA, compareB));
    } catch (e) {
      setError(String(e));
      setCompareResult(null);
    }
  }

  async function viewPayload(name: string, version: number) {
    try {
      const v = await splinkGetModelVersion(name, version);
      setViewedPayload({ label: `${name} v${version} — saved payload + metadata`, data: v });
    } catch (e) {
      setError(String(e));
    }
  }

  async function viewActiveConfig(name: string) {
    try {
      const v = await splinkGetActiveConfig(name);
      setViewedPayload({ label: `${name} — active version's generated Splink configuration`, data: v });
    } catch (e) {
      setError(String(e));
    }
  }

  const current = models.find((m) => m.model_name === props.modelName);

  return (
    <div className="card">
      <h3 className="card-title">Model versions (S-N)</h3>
      <p className="card-hint">
        Every trained version is immutable and kept until archived. Exactly one version per model
        can be active; activation refuses a version that failed round-trip verification.
      </p>
      <button className="btn btn-ghost" onClick={load} disabled={loading}>
        {loading ? "Loading…" : "Refresh models"}
      </button>
      {error && <div className="banner banner-error" style={{ marginTop: 10 }}>{error}</div>}
      {models.length === 0 && !loading && <div className="muted" style={{ marginTop: 10 }}>No trained models yet.</div>}
      {models.map((m) => (
        <div key={m.model_name} style={{ marginTop: 14 }}>
          <div className="section-label">{m.model_name}</div>
          <table className="data-table">
            <thead>
              <tr>
                <th>Version</th>
                <th>Status</th>
                <th>Parent</th>
                <th>Active</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {m.versions.map((v) => (
                <tr key={v.version} className={props.activeVersion === v.version && props.modelName === m.model_name ? "chip-active" : ""}>
                  <td className="mono">{v.version}</td>
                  <td>{v.status}</td>
                  <td className="mono">{v.parent_version ?? "—"}</td>
                  <td>{v.is_active ? "✓" : ""}</td>
                  <td className="mono">{v.created_at}</td>
                  <td>
                    <div className="btn-row">
                      <button className="btn btn-ghost" onClick={() => props.onSelectVersion(v.version)}>
                        Use for predict/explain/cluster/evaluate
                      </button>
                      <button className="btn btn-ghost" onClick={() => doActivate(m.model_name, v.version)}>
                        Activate
                      </button>
                      <button className="btn btn-danger" onClick={() => doArchive(m.model_name, v.version)}>
                        Archive
                      </button>
                      <button className="btn btn-ghost" onClick={() => viewPayload(m.model_name, v.version)}>
                        View payload
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="btn-row" style={{ marginTop: 8 }}>
            <button className="btn btn-ghost" onClick={() => doRollback(m.model_name)}>
              Rollback to previous active
            </button>
            <button className="btn btn-ghost" onClick={() => viewActiveConfig(m.model_name)}>
              View active configuration
            </button>
          </div>
        </div>
      ))}
      {viewedPayload && (
        <div style={{ marginTop: 14 }}>
          <div className="section-label">{viewedPayload.label}</div>
          <pre className="code-block">{JSON.stringify(viewedPayload.data, null, 2)}</pre>
        </div>
      )}
      {current && current.versions.length >= 2 && (
        <div style={{ marginTop: 14 }}>
          <div className="section-label">Compare two versions of {current.model_name}</div>
          <div className="btn-row">
            <select value={compareA ?? ""} onChange={(e) => setCompareA(Number(e.target.value))}>
              <option value="">version A</option>
              {current.versions.map((v) => (
                <option key={v.version} value={v.version}>
                  v{v.version}
                </option>
              ))}
            </select>
            <select value={compareB ?? ""} onChange={(e) => setCompareB(Number(e.target.value))}>
              <option value="">version B</option>
              {current.versions.map((v) => (
                <option key={v.version} value={v.version}>
                  v{v.version}
                </option>
              ))}
            </select>
            <button className="btn btn-primary" onClick={() => doCompare(current.model_name)} disabled={compareA == null || compareB == null}>
              Compare
            </button>
          </div>
          {compareResult && <pre className="code-block" style={{ marginTop: 10 }}>{JSON.stringify(compareResult, null, 2)}</pre>}
        </div>
      )}
    </div>
  );
}

// ── shared model/version picker for the four saved-model steps ─────────────

function ModelVersionPicker(props: {
  modelName: string;
  version: number | null;
}) {
  return (
    <p className="card-hint">
      Uses model <span className="mono">{props.modelName || "(none)"}</span>, version{" "}
      <span className="mono">{props.version ?? "(none)"}</span> — train a version above or pick
      one with "Use for predict/explain/cluster/evaluate" in the versions table.
    </p>
  );
}

// ── S-J/S-P — predict ────────────────────────────────────────────────────────

function PredictCard(props: { records: Record<string, any>[] | null; modelName: string; version: number | null }) {
  const [matchThreshold, setMatchThreshold] = useState(0.5);
  const [excludeThreshold, setExcludeThreshold] = useState(0.2);
  const [result, setResult] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    if (!props.records?.length || !props.modelName || props.version == null) {
      setError("need records, a model name, and a trained version");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await splinkPredict({
        model_name: props.modelName,
        version: props.version,
        records: props.records,
        match_threshold: matchThreshold,
        exclude_threshold: excludeThreshold,
      });
      setResult(r);
    } catch (e) {
      setError(String(e));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <h3 className="card-title">Predict &amp; review (S-J/S-P)</h3>
      <ModelVersionPicker modelName={props.modelName} version={props.version} />
      <div className="form-grid">
        <label className="field">
          <span className="section-label">Match threshold (auto-match ≥)</span>
          <input type="number" step="0.05" min={0} max={1} value={matchThreshold} onChange={(e) => setMatchThreshold(Number(e.target.value))} />
        </label>
        <label className="field">
          <span className="section-label">Exclude threshold (auto-keep-apart ≤)</span>
          <input type="number" step="0.05" min={0} max={1} value={excludeThreshold} onChange={(e) => setExcludeThreshold(Number(e.target.value))} />
        </label>
      </div>
      <button className="btn btn-primary" onClick={run} disabled={loading}>
        {loading ? "Scoring…" : "Predict"}
      </button>
      {error && <div className="banner banner-error" style={{ marginTop: 10 }}>{error}</div>}
      {result && (
        <div style={{ marginTop: 14 }}>
          <div className="summary-grid">
            <div className="stat"><strong>{result.pairs_scored}</strong><span>pairs scored</span></div>
            <div className="stat"><strong>{result.pairs_above_threshold}</strong><span>above threshold</span></div>
            <div className="stat"><strong>{result.review_count ?? 0}</strong><span>queued for review</span></div>
          </div>
          <details style={{ marginTop: 10 }}>
            <summary>Full result (raw)</summary>
            <pre className="code-block">{JSON.stringify(result, null, 2)}</pre>
          </details>
        </div>
      )}
    </div>
  );
}

// ── S-J §33 — explain ────────────────────────────────────────────────────────

function ExplainCard(props: { records: Record<string, any>[] | null; modelName: string; version: number | null }) {
  const [indexA, setIndexA] = useState<number | null>(null);
  const [indexB, setIndexB] = useState<number | null>(null);
  const [result, setResult] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    if (!props.records?.length || !props.modelName || props.version == null) {
      setError("need records, a model name, and a trained version");
      return;
    }
    if (indexA == null || indexB == null) {
      setError("pick two records to compare");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await splinkExplain({
        model_name: props.modelName,
        version: props.version,
        records: props.records,
        record_a: props.records[indexA],
        record_b: props.records[indexB],
      });
      setResult(r);
    } catch (e) {
      setError(String(e));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <h3 className="card-title">Explain a pair (S-J §33)</h3>
      <ModelVersionPicker modelName={props.modelName} version={props.version} />
      <div className="btn-row">
        <select value={indexA ?? ""} onChange={(e) => setIndexA(Number(e.target.value))}>
          <option value="">record A</option>
          {(props.records || []).map((r, i) => (
            <option key={i} value={i}>
              {recordLabel(r, i)}
            </option>
          ))}
        </select>
        <select value={indexB ?? ""} onChange={(e) => setIndexB(Number(e.target.value))}>
          <option value="">record B</option>
          {(props.records || []).map((r, i) => (
            <option key={i} value={i}>
              {recordLabel(r, i)}
            </option>
          ))}
        </select>
        <button className="btn btn-primary" onClick={run} disabled={loading}>
          {loading ? "Explaining…" : "Explain"}
        </button>
      </div>
      {error && <div className="banner banner-error" style={{ marginTop: 10 }}>{error}</div>}
      {result && (
        <div style={{ marginTop: 14 }}>
          <div className="muted">
            {result.id_l} ↔ {result.id_r} — match probability {result.match_probability}
          </div>
          <table className="data-table" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>Field</th>
                <th>Level</th>
                <th>Bayes factor</th>
                <th>TF-adjusted</th>
              </tr>
            </thead>
            <tbody>
              {(result.fields || []).map((f: any) => (
                <tr key={f.field}>
                  <td className="mono">{f.field}</td>
                  <td>{f.level}</td>
                  <td className="mono">{f.bayes_factor}</td>
                  <td className="mono">{f.tf_adjusted_bayes_factor ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── S-O — cluster ────────────────────────────────────────────────────────────

function ClusterCard(props: { records: Record<string, any>[] | null; modelName: string; version: number | null }) {
  const [threshold, setThreshold] = useState(0.5);
  const [result, setResult] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    if (!props.records?.length || !props.modelName || props.version == null) {
      setError("need records, a model name, and a trained version");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await splinkCluster({
        model_name: props.modelName,
        version: props.version,
        records: props.records,
        match_probability_threshold: threshold,
      });
      setResult(r);
    } catch (e) {
      setError(String(e));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <h3 className="card-title">Cluster (S-O)</h3>
      <ModelVersionPicker modelName={props.modelName} version={props.version} />
      <div className="form-grid">
        <label className="field">
          <span className="section-label">Match probability threshold</span>
          <input type="number" step="0.05" min={0} max={1} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} />
        </label>
      </div>
      <button className="btn btn-primary" onClick={run} disabled={loading}>
        {loading ? "Clustering…" : "Cluster"}
      </button>
      {error && <div className="banner banner-error" style={{ marginTop: 10 }}>{error}</div>}
      {result && (
        <div style={{ marginTop: 14 }}>
          <div className="summary-grid">
            <div className="stat"><strong>{result.n_clusters}</strong><span>clusters</span></div>
            <div className="stat"><strong>{result.n_multi_record_clusters}</strong><span>multi-record clusters</span></div>
            <div className="stat"><strong>{result.largest_cluster_size}</strong><span>largest cluster</span></div>
            <div className="stat"><strong>{Math.round((result.largest_cluster_share || 0) * 100)}%</strong><span>largest cluster share</span></div>
          </div>
          {result.graph_metrics?.error && (
            <div className="banner banner-warn" style={{ marginTop: 10 }}>
              Graph metrics unavailable: {result.graph_metrics.error} (display extra only —
              cluster membership above is unaffected).
            </div>
          )}
          <details style={{ marginTop: 10 }}>
            <summary>Full result (raw)</summary>
            <pre className="code-block">{JSON.stringify(result, null, 2)}</pre>
          </details>
        </div>
      )}
    </div>
  );
}

// ── S-K/S-L/S-M — evaluate ───────────────────────────────────────────────────

function EvaluateCard(props: { records: Record<string, any>[] | null; modelName: string; version: number | null }) {
  const [labelledPairsText, setLabelledPairsText] = useState("");
  const [result, setResult] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const labelledPairs = parseJsonArray(labelledPairsText);

  async function run() {
    if (!props.records?.length || !props.modelName || props.version == null) {
      setError("need records, a model name, and a trained version");
      return;
    }
    if (labelledPairs.error || !labelledPairs.value) {
      setError(labelledPairs.error || 'labelled pairs must be a JSON array of [id_l, id_r, score]');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await splinkEvaluate({
        model_name: props.modelName,
        version: props.version,
        records: props.records,
        labelled_pairs: labelledPairs.value as any[],
      });
      setResult(r);
    } catch (e) {
      setError(String(e));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <h3 className="card-title">Evaluate (S-K/S-L/S-M)</h3>
      <ModelVersionPicker modelName={props.modelName} version={props.version} />
      <p className="card-hint">
        Labelled pairs use the record ids the Explain card just showed you (
        <span className="mono">id_l</span>/<span className="mono">id_r</span>), not the array
        index — e.g. <span className="mono">[["pg:t:1", "pg:t:2", 1.0]]</span> for a confirmed
        match, <span className="mono">0.0</span> for a confirmed non-match.
      </p>
      <div className="field">
        <span className="section-label">Labelled pairs (JSON array of [id_l, id_r, score])</span>
        <textarea rows={3} value={labelledPairsText} onChange={(e) => setLabelledPairsText(e.target.value)} />
      </div>
      {labelledPairs.error && <div className="field-error">{labelledPairs.error}</div>}
      <button className="btn btn-primary" onClick={run} disabled={loading} style={{ marginTop: 10 }}>
        {loading ? "Evaluating…" : "Evaluate"}
      </button>
      {error && <div className="banner banner-error" style={{ marginTop: 10 }}>{error}</div>}
      {result && (
        <div style={{ marginTop: 14 }}>
          <details open>
            <summary>Accuracy report</summary>
            <pre className="code-block">{JSON.stringify(result.accuracy, null, 2)}</pre>
          </details>
          <details style={{ marginTop: 10 }}>
            <summary>Error analysis (FP/FN)</summary>
            <pre className="code-block">{JSON.stringify(result.errors, null, 2)}</pre>
          </details>
        </div>
      )}
    </div>
  );
}
