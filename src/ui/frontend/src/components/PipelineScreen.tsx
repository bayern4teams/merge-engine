import { useEffect, useRef, useState } from "react";
import {
  abortWorkflow,
  ColumnDependency,
  getStatus,
  MappingRow,
  OriginConfig,
  DestinationConfig,
  sendFeedback,
  startWorkflow,
  listProfiles,
  ProfileSummary,
  WorkflowStatus,
} from "../api";
import Stepper from "./Stepper";
import Gate from "./Gates";
import EventLog, { LogLine } from "./EventLog";
import AdvancedPanel, { ADVANCED_DEFAULT, AdvancedState, buildRunOverrides } from "./AdvancedPanel";

interface Props {
  origins: OriginConfig[];
  destination: DestinationConfig | null;
}

const POLL_MS = 2000;

const GATE_TITLES: Record<string, string> = {
  human_review_deps: "Dependency review required",
  human_review_scope: "Scope confirmation required",
  human_review_matches: "Entity match review required",
  human_review_mappings: "Field mapping review required",
  human_final_approval: "Final approval required",
};

const STATUS_MSG: Record<string, { msg: string; level: LogLine["level"] }> = {
  running: { msg: "Running workflow…", level: "info" },
  interrupted: { msg: "Review required at checkpoint.", level: "info" },
  completed: { msg: "Workflow completed successfully.", level: "success" },
  failed: { msg: "Workflow failed.", level: "error" },
  aborted: { msg: "Workflow aborted.", level: "warn" },
  rejected: { msg: "Rejected — nothing was written to the destination.", level: "warn" },
};

export default function PipelineScreen({ origins, destination }: Props) {
  const [request, setRequest] = useState("");
  // Saved run profiles (Phase O). Selecting one replays the decisions an
  // earlier run made — which tables, how to match, how fields map — so the
  // same merge produces the same destination rather than re-asking the LLM.
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [profile, setProfile] = useState("");
  const [saveAs, setSaveAs] = useState("");
  const [fullRemerge, setFullRemerge] = useState(false);
  const [advanced, setAdvanced] = useState<AdvancedState>(ADVANCED_DEFAULT);
  const [presetCompany, setPresetCompany] = useState("");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [status, setStatus] = useState<WorkflowStatus | null>(null);
  const [log, setLog] = useState<LogLine[]>([]);
  const [busy, setBusy] = useState(false);

  // dedup keys, so the 2s poll never repeats identical log lines
  const lastStatusKey = useRef<string>("");
  const lastGateKey = useRef<string>("");
  const timer = useRef<number | null>(null);

  function addLog(level: LogLine["level"], msg: string) {
    const ts = new Date().toLocaleTimeString();
    setLog((prev) => [...prev, { ts, level, msg }].slice(-100));
  }

  // Reload after every run, so a profile saved by the run just finished
  // appears without a page refresh.
  useEffect(() => {
    listProfiles()
      .then((r) => setProfiles(r.profiles))
      .catch(() => setProfiles([]));
  }, [status?.status]);

  // Selecting a profile pre-fills its request — the point is to repeat the
  // same merge, and retyping the prompt is the easiest way to get it wrong.
  useEffect(() => {
    if (!profile) return;
    const found = profiles.find((p) => p.name === profile);
    if (found?.user_request) setRequest(found.user_request);
  }, [profile]);

  // ── Polling ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!threadId) return;
    let cancelled = false;

    async function poll() {
      try {
        const s = await getStatus(threadId!);
        if (cancelled) return;
        setStatus(s);

        // status-transition log (dedup on the status string)
        const sm = STATUS_MSG[s.status];
        if (sm && lastStatusKey.current !== s.status) {
          lastStatusKey.current = s.status;
          addLog(sm.level, sm.msg);
        }

        // per-gate checkpoint log (dedup on thread:node)
        const node = s.interrupt_data?.node;
        if (s.status === "interrupted" && node) {
          const gateKey = `${threadId}:${node}`;
          if (lastGateKey.current !== gateKey) {
            lastGateKey.current = gateKey;
            const skipped = s.skipped_gates || {};
            for (const [sn, reason] of Object.entries(skipped)) {
              addLog("info", `${GATE_TITLES[sn] || sn} auto-skipped: ${reason}`);
            }
            addLog("warn", `Checkpoint: ${GATE_TITLES[node] || node}`);
          }
        }

        const done = ["completed", "failed", "aborted", "rejected"].includes(s.status);
        if (!done && !cancelled) {
          timer.current = window.setTimeout(poll, POLL_MS);
        }
      } catch (e) {
        if (!cancelled) {
          addLog("error", `Status poll failed: ${e}`);
          timer.current = window.setTimeout(poll, POLL_MS);
        }
      }
    }
    poll();

    return () => {
      cancelled = true;
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [threadId]);

  // ── Actions ────────────────────────────────────────────────────────────
  async function onStart() {
    if (!destination || origins.length === 0 || !request.trim()) return;
    setBusy(true);
    setLog([]);
    lastStatusKey.current = "";
    lastGateKey.current = "";
    try {
      const overrides = buildRunOverrides(advanced);
      const { thread_id } = await startWorkflow({
        request,
        origins,
        destination,
        profile_name: profile || undefined,
        save_as_profile: saveAs.trim() || undefined,
        full_remerge: fullRemerge || undefined,
        ...overrides,
      });
      if (profile) {
        addLog("info", `Replaying saved profile "${profile}".`);
      }
      if (fullRemerge) {
        addLog("info", "Full re-merge: ignoring previous progress, re-extracting every table.");
      }
      if (saveAs.trim()) {
        addLog("info", `This run's decisions will be saved as "${saveAs.trim()}".`);
      }
      if (overrides.run_config?.auto_approve_gates?.length) {
        addLog(
          "warn",
          `Auto-approving: ${overrides.run_config.auto_approve_gates.join(", ")} — ` +
            "these gates will not pause for review.",
        );
      }
      addLog("info", `Workflow ${thread_id} started with ${origins.length} origin(s).`);
      setThreadId(thread_id);
      setStatus({ status: "running" });
    } catch (e) {
      addLog("error", `Failed to start: ${e}`);
    } finally {
      setBusy(false);
    }
  }

  async function onApprove(mappingEdits?: MappingRow[], columnDependencies?: ColumnDependency[]) {
    if (!threadId) return;
    setBusy(true);
    try {
      const node = status?.interrupt_data?.node;
      const s = await sendFeedback(
        threadId,
        "approve",
        node === "human_review_mappings" ? mappingEdits : undefined,
        node === "human_review_deps" ? columnDependencies : undefined,
      );
      addLog("success", "Approved.");
      setStatus(s);
      lastStatusKey.current = ""; // allow the next status line through
    } catch (e) {
      addLog("error", `Approve failed: ${e}`);
    } finally {
      setBusy(false);
    }
  }

  async function onReject() {
    if (!threadId) return;
    setBusy(true);
    try {
      // The backend ends the run itself on reject — no abort call needed.
      // Calling abort here used to mask the fact that rejection did nothing:
      // the UI said "aborted" while the graph carried on to load_data.
      await sendFeedback(threadId, "reject");
      addLog("warn", "Rejected — nothing will be written to the destination.");
    } catch (e) {
      addLog("error", `Reject failed: ${e}`);
    } finally {
      setBusy(false);
    }
  }

  async function onAbort() {
    if (!threadId) return;
    await abortWorkflow(threadId);
    addLog("warn", "Workflow aborted.");
    setStatus({ status: "aborted" });
  }

  // One-click "everything about a company": fill the request with a scoped
  // prompt and force a full re-merge so a re-run after a manual delete brings
  // the rows back. The user still reviews every gate before anything is written.
  function applyCompanyPreset() {
    const name = presetCompany.trim();
    if (!name) return;
    setRequest(
      `Please conduct a comprehensive and indepth data extraction of ${name}, covering all of the following dimensions:\n` +
        `NOTE: Do your best to find all of them.\n\n` +
        `1. Basic Company Information: Legal structure, registered address and operational headquarters, establishment history, shareholding structure and stock symbol;\n\n` +
        `2. Personnel and Organization: Board members, executive committee, and senior executives (including CEO, CEOs of various business divisions, CFO, etc.), total number of employees worldwide and the educational and professional backgrounds of all individuals;\n\n` +
        `3. Business and Products: All business areas and delivery data;\n\n` +
        `4. Finance and Funding: Revenue and profit data, funding history and major investors;\n\n` +
        `5. Technology and Innovation: Core technology directions and digital transformation;\n\n` +
        `6. Global Presence: Main operational locations and international layout;\n\n` +
        `7. Contact Information: Official company & individuals phone number and website.`,
    );
    setFullRemerge(true);
  }

  // ── Derived UI state ───────────────────────────────────────────────────
  const wfStatus = status?.status ?? "idle";
  const interrupt = status?.interrupt_data ?? null;
  const activeGate = wfStatus === "interrupted" ? interrupt?.node ?? null : null;
  const skipped = status?.skipped_gates || {};
  const canStart = origins.length > 0 && !!destination && request.trim().length > 0;
  const running = ["running", "interrupted"].includes(wfStatus);

  return (
    <div>
      <div className="card preset-card">
        <h3 className="card-title">Comprehensive company extraction</h3>
        <p className="card-hint">
          Type a company name to build an enriched extraction request covering company info,
          personnel, products, financials, technology, locations, and contacts across all origins.
        </p>
        <div className="preset-row">
          <input
            type="text"
            value={presetCompany}
            disabled={running}
            onChange={(e) => setPresetCompany(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyCompanyPreset()}
            placeholder="e.g. Airbus"
          />
          <button
            className="btn btn-ghost"
            onClick={applyCompanyPreset}
            disabled={running || !presetCompany.trim()}
          >
            Build request
          </button>
        </div>
      </div>

      <div className="card">
        <h3 className="card-title">Merge request</h3>
        <p className="card-hint">
          Describe what to merge in plain language. The pipeline will pause at
          review gates and show you exactly what it found before doing anything.
        </p>
        <textarea
          rows={3}
          value={request}
          disabled={running}
          onChange={(e) => setRequest(e.target.value)}
          placeholder="e.g. Merge all companies and their people from both origins into the destination."
        />

        <div className="profile-row">
          <label>
            <span className="field-label">Saved profile</span>
            <select
              value={profile}
              disabled={running}
              onChange={(e) => setProfile(e.target.value)}
            >
              <option value="">— none (decide everything fresh) —</option>
              {profiles.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}
                  {p.entities.length ? ` · ${p.entities.join(", ")}` : ""}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="field-label">Save this run as</span>
            <input
              type="text"
              value={saveAs}
              disabled={running}
              onChange={(e) => setSaveAs(e.target.value)}
              placeholder="optional profile name"
            />
          </label>
        </div>
        <p className="card-hint">
          A profile remembers which tables held each entity, how records were
          matched, and how fields mapped onto destination columns — so running
          it again produces the same result instead of deciding afresh.
        </p>
        <label className="remerge-toggle">
          <input
            type="checkbox"
            checked={fullRemerge}
            disabled={running}
            onChange={(e) => setFullRemerge(e.target.checked)}
          />
          <span>
            Full re-merge — re-extract every table, ignoring previous progress.
            Turn this on if you deleted rows from the destination and want them
            merged back (an incremental run would find nothing new and load 0).
          </span>
        </label>

        <AdvancedPanel value={advanced} onChange={setAdvanced} disabled={running} />

        <div className="btn-row" style={{ marginTop: 14 }}>
          <button className="btn btn-primary" onClick={onStart} disabled={!canStart || running || busy}>
            ▶ Start merge
          </button>
          {running && (
            <button className="btn btn-danger" onClick={onAbort} disabled={busy}>
              ■ Abort
            </button>
          )}
        </div>
        {(origins.length === 0 || !destination) && (
          <div className="banner banner-warn">
            Configure {origins.length === 0 ? "at least one origin" : ""}
            {origins.length === 0 && !destination ? " and " : ""}
            {!destination ? "a destination" : ""} in the Setup tab first.
          </div>
        )}
      </div>

      {wfStatus !== "idle" && (
        <>
          <StatusLine status={wfStatus} interrupt={interrupt} />
          <Stepper activeGate={activeGate} skippedGates={skipped} />
        </>
      )}

      {(wfStatus === "completed" || wfStatus === "failed") && (
        <RunScorecard report={status?.summary?.reconciliation_report} />
      )}

      {interrupt && wfStatus === "interrupted" && (
        <Gate
          interrupt={interrupt}
          skippedGates={skipped}
          busy={busy}
          onApprove={onApprove}
          onReject={onReject}
        />
      )}

      {wfStatus !== "idle" && <EventLog lines={log} />}
    </div>
  );
}

function StatusLine({
  status,
  interrupt,
}: {
  status: string;
  interrupt: WorkflowStatus["interrupt_data"];
}) {
  let cls = "idle";
  let text = "Idle";
  let spin = false;
  if (status === "interrupted") {
    cls = "warn";
    text = `⏸ Awaiting your review — ${interrupt?.title || "Review required"}`;
  } else if (status === "running") {
    cls = "info";
    text = "Running…";
    spin = true;
  } else if (status === "completed") {
    cls = "success";
    text = "✅ Workflow completed successfully.";
  } else if (status === "failed") {
    cls = "error";
    text = "❌ Workflow failed — see log below.";
  } else if (status === "aborted") {
    cls = "warn";
    text = "⏹ Workflow aborted.";
  }
  return (
    <div className={`status-line ${cls}`}>
      {spin && <span className="spinner" />}
      {text}
    </div>
  );
}

// Phase 6.3 (merge-engine-remediation-1-to-6): a read-only summary of the
// data-quality scorecard build_reconciliation_report attaches to every run —
// grade + red flags, plus how many of the requested entity families
// actually landed. Renders once the run has stopped (completed or failed);
// there is nothing to show mid-run since the report is only built at the
// load step.
//
// Bug #8 / Bug #1: the scorecard also flags entity families that loaded
// ZERO rows and inferred entity types that were dropped for having no
// source table — a run that "succeeded" while silently writing nothing
// for half the requested entities must not look like a clean success.
function RunScorecard({ report }: { report?: Record<string, any> }) {
  const [showExplanation, setShowExplanation] = useState(false);
  if (!report) return null;
  const dq = report.data_quality || {};
  const grade: string = dq.grade || "A";
  const redFlags: string[] = dq.red_flags || [];

  // Bug #8: requested families that ended with no rows at all.
  const entities: Record<string, any> = report.entities || {};
  const loadedEntities = Object.entries(entities).filter(([, e]) => {
    const inserted = Number(e.rows_inserted ?? 0);
    const updated = Number(e.rows_updated ?? 0);
    return inserted + updated > 0;
  });
  const loadedNames = new Set(loadedEntities.map(([name]) => name));
  const zeroRowEntities = Object.keys(entities).filter(
    (name) => !loadedNames.has(name),
  );

  // Bug #1: inferred entity types that had no source table anywhere and
  // were dropped before extraction.
  const droppedInferred: string[] = report.dropped_inferred_entities || [];

  return (
    <div style={{ marginTop: 14 }}>
      <div className="section-label">Data-quality scorecard</div>
      <div className="summary-grid">
        <div className="stat">
          <div className={`value grade-${grade}`}>{grade}</div>
          <div className="label">Grade</div>
        </div>
        <Stat label="Families requested" value={report.families_requested ?? 0} />
        <Stat label="Families loaded" value={report.families_loaded ?? 0} />
        <Stat label="Completeness" value={`${report.completeness_pct ?? 0}%`} />
        <Stat label="Zero-row entities" value={zeroRowEntities.length} />
        <Stat label="Dropped inferred" value={droppedInferred.length} />
      </div>

      <div style={{ marginTop: 10, marginBottom: 10 }}>
        <button
          className="btn btn-ghost"
          style={{ fontSize: 12, padding: "4px 10px" }}
          onClick={() => setShowExplanation((prev) => !prev)}
        >
          {showExplanation ? "▾ Hide score breakdown" : "📊 Why this score?"}
        </button>
      </div>

      {showExplanation && (
        <div
          style={{
            background: "var(--card-bg, #f8f9fa)",
            border: "1px solid var(--border-color, #e0e0e0)",
            borderRadius: 6,
            padding: 14,
            marginBottom: 14,
            fontSize: 13,
            lineHeight: 1.5,
          }}
        >
          <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 14 }}>
            Data Quality Score Breakdown
          </div>
          
          <div style={{ marginBottom: 10 }}>
            <strong>Grade Assessment (Grade {grade}):</strong>
            <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
              <li><strong>Grade A</strong>: No red flags. All target entity families extracted and loaded cleanly.</li>
              <li><strong>Grade B</strong>: Minor issues (e.g. inferred entity types with no matching source table).</li>
              <li><strong>Grade C</strong>: Type coercion errors or skipped field mappings.</li>
              <li><strong>Grade F</strong>: Transaction rollback or zero auto-merges across all entities.</li>
            </ul>
            {redFlags.length > 0 && (
              <div style={{ marginTop: 6, color: "#c62828" }}>
                <strong>Active Red Flags:</strong>
                <ul style={{ margin: "2px 0 0 18px", padding: 0 }}>
                  {redFlags.map((flag, idx) => (
                    <li key={idx}>{flag}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          <div style={{ marginBottom: 10 }}>
            <strong>Completeness Calculation:</strong>
            <p style={{ margin: "4px 0 0 0" }}>
              <code>Completeness = Families Loaded ({report.families_loaded ?? 0}) / Families Requested ({report.families_requested ?? 0}) × 100 = {report.completeness_pct ?? 0}%</code>
            </p>
            <p style={{ margin: "4px 0 0 0", color: "var(--muted-color, #6c757d)", fontSize: 12 }}>
              <em>Families Requested</em> represents target entities identified from schemas. <em>Families Loaded</em> represents entities with ≥1 row inserted/updated into destination.
            </p>
            <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
              <li>Loaded entities: {Array.from(loadedNames).join(", ") || "None"}</li>
              {zeroRowEntities.length > 0 && (
                <li>Zero-row entities: {zeroRowEntities.join(", ")}</li>
              )}
            </ul>
          </div>

          {droppedInferred.length > 0 && (
            <div>
              <strong>Dropped Inferred Entities ({droppedInferred.length}):</strong>
              <p style={{ margin: "4px 0 0 0" }}>
                The following inferred entity types had no matching source table in any connected origin database and were dropped prior to extraction:
              </p>
              <code style={{ display: "block", marginTop: 4, background: "rgba(0,0,0,0.04)", padding: 6, borderRadius: 4 }}>
                {droppedInferred.join(", ")}
              </code>
            </div>
          )}
        </div>
      )}

      {zeroRowEntities.length > 0 && (
        <div className="banner banner-warn">
          ⚠ {zeroRowEntities.length} entity family(ies) loaded zero rows:{" "}
          {zeroRowEntities.join(", ")}
        </div>
      )}

      {droppedInferred.length > 0 && (
        <div className="banner banner-warn">
          ⚠ {droppedInferred.length} inferred entity type(s) had no source
          table and were dropped: {droppedInferred.join(", ")}
        </div>
      )}

      {redFlags.length > 0 &&
        redFlags.map((flag, i) => (
          <div key={i} className="banner banner-warn">
            ⚠ {flag}
          </div>
        ))}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="stat">
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}
