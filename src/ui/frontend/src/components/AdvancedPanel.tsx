import { RunConfig, GATE_LABELS, REVIEW_GATES } from "../api";

// Part 1.6.A: run-by-run overrides for a single merge. Nothing here touches
// config.yaml or restarts anything -- it rides straight on this run's
// /api/workflow/start body and takes effect immediately (see
// graph._apply_run_config_overrides / nodes/resolve_entities.py's
// "_default" matching-rules fallback).
export interface AdvancedState {
  model: string;
  temperature: string; // kept as a string so an empty field means "unset"
  batchSize: string;
  entityResolutionEnabled: boolean;
  fieldMappingEnabled: boolean;
  strictness: "" | "loose" | "normal" | "strict";
  autoApproveGates: string[];
}

export const ADVANCED_DEFAULT: AdvancedState = {
  model: "",
  temperature: "",
  batchSize: "",
  entityResolutionEnabled: true,
  fieldMappingEnabled: true,
  strictness: "",
  autoApproveGates: [],
};

/** Build the run_config / matching_rules bodies startWorkflow expects,
 * omitting anything left at its default so an untouched panel changes
 * nothing about how the run behaves. */
export function buildRunOverrides(state: AdvancedState): {
  run_config?: RunConfig;
  matching_rules?: Record<string, any>;
} {
  const runConfig: RunConfig = {};
  if (state.model.trim()) runConfig.model = state.model.trim();
  if (state.temperature.trim() !== "") {
    const t = Number(state.temperature);
    if (!Number.isNaN(t)) runConfig.temperature = t;
  }
  if (state.batchSize.trim() !== "") {
    const b = Number(state.batchSize);
    if (!Number.isNaN(b) && b > 0) runConfig.batch_size = Math.round(b);
  }
  if (!state.entityResolutionEnabled) runConfig.entity_resolution_enabled = false;
  if (!state.fieldMappingEnabled) runConfig.field_mapping_enabled = false;
  if (state.autoApproveGates.length > 0) runConfig.auto_approve_gates = state.autoApproveGates;

  const out: { run_config?: RunConfig; matching_rules?: Record<string, any> } = {};
  if (Object.keys(runConfig).length > 0) out.run_config = runConfig;
  if (state.strictness) out.matching_rules = { _default: { strictness: state.strictness } };
  return out;
}

interface Props {
  value: AdvancedState;
  onChange: (v: AdvancedState) => void;
  disabled: boolean;
}

export default function AdvancedPanel({ value, onChange, disabled }: Props) {
  function set<K extends keyof AdvancedState>(key: K, v: AdvancedState[K]) {
    onChange({ ...value, [key]: v });
  }

  function toggleGate(gate: string) {
    const has = value.autoApproveGates.includes(gate);
    set(
      "autoApproveGates",
      has ? value.autoApproveGates.filter((g) => g !== gate) : [...value.autoApproveGates, gate],
    );
  }

  return (
    <details className="advanced-panel">
      <summary>Advanced (run-by-run overrides)</summary>
      <div className="advanced-body">
        <p className="card-hint">
          These apply to this run only — nothing here touches config.yaml or
          needs a restart. Leave a field blank to use the platform default.
        </p>

        <div className="profile-row">
          <label>
            <span className="field-label">LLM model override</span>
            <input
              type="text"
              value={value.model}
              disabled={disabled}
              onChange={(e) => set("model", e.target.value)}
              placeholder="e.g. qwen3.5-flash (blank = platform default)"
            />
          </label>
          <label>
            <span className="field-label">Temperature</span>
            <input
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={value.temperature}
              disabled={disabled}
              onChange={(e) => set("temperature", e.target.value)}
              placeholder="blank = platform default"
            />
          </label>
        </div>

        <div className="profile-row">
          <label>
            <span className="field-label">Extraction batch size</span>
            <input
              type="number"
              min="1"
              value={value.batchSize}
              disabled={disabled}
              onChange={(e) => set("batchSize", e.target.value)}
              placeholder="blank = platform default"
            />
          </label>
          <label>
            <span className="field-label">Matching strictness</span>
            <select
              value={value.strictness}
              disabled={disabled}
              onChange={(e) => set("strictness", e.target.value as AdvancedState["strictness"])}
            >
              <option value="">— platform default —</option>
              <option value="loose">Loose</option>
              <option value="normal">Normal</option>
              <option value="strict">Strict</option>
            </select>
          </label>
        </div>

        <label className="remerge-toggle">
          <input
            type="checkbox"
            checked={value.entityResolutionEnabled}
            disabled={disabled}
            onChange={(e) => set("entityResolutionEnabled", e.target.checked)}
          />
          <span>LLM entity-match adjudication for borderline pairs (off = heuristic scores only)</span>
        </label>
        <label className="remerge-toggle">
          <input
            type="checkbox"
            checked={value.fieldMappingEnabled}
            disabled={disabled}
            onChange={(e) => set("fieldMappingEnabled", e.target.checked)}
          />
          <span>LLM field mapping for fields the heuristic can't map confidently</span>
        </label>

        <div className="section-label" style={{ marginTop: 16 }}>
          Auto-approve gates
        </div>
        <p className="card-hint">
          A checked gate resumes on its own instead of pausing for review — no
          human sees that checkpoint. Leave all unchecked for the normal
          reviewed flow.
        </p>
        <div className="gate-toggle-grid">
          {REVIEW_GATES.map((gate) => (
            <label key={gate} className="remerge-toggle gate-toggle">
              <input
                type="checkbox"
                checked={value.autoApproveGates.includes(gate)}
                disabled={disabled}
                onChange={() => toggleGate(gate)}
              />
              <span>{GATE_LABELS[gate] || gate}</span>
            </label>
          ))}
        </div>
      </div>
    </details>
  );
}
