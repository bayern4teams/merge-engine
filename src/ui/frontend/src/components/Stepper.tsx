const GATES: Array<[string, string]> = [
  ["human_review_deps", "Dependencies"],
  ["human_review_scope", "Scope"],
  ["human_review_matches", "Entity Matches"],
  ["human_review_mappings", "Field Mappings"],
  ["human_final_approval", "Final Approval"],
];

interface Props {
  activeGate: string | null;
  skippedGates: Record<string, string>;
}

// Mirrors the old Gradio _get_gate_stepper_html: steps before the active
// one render "done", unless the pipeline routed around them (skipped),
// which render as a distinct dashed "⏭ auto" chip with the reason on hover.
export default function Stepper({ activeGate, skippedGates }: Props) {
  const names = GATES.map(([n]) => n);
  const activeIdx = activeGate ? names.indexOf(activeGate) : -1;
  if (activeIdx < 0) return null;

  return (
    <div className="stepper">
      {GATES.map(([node, label], i) => {
        let cls = "pending";
        let icon: string = String(i + 1);
        let title = "";
        let text = label;
        if (i < activeIdx) {
          if (skippedGates[node]) {
            cls = "skipped";
            icon = "⏭";
            text = `${label} (auto)`;
            title = skippedGates[node];
          } else {
            cls = "done";
            icon = "✓";
          }
        } else if (i === activeIdx) {
          cls = "current";
        }
        return (
          <div key={node} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div className={`step ${cls}`} title={title}>
              <span className="num">{icon}</span>
              <span>{text}</span>
            </div>
            {i < GATES.length - 1 && <div className="step-connector" />}
          </div>
        );
      })}
    </div>
  );
}
