# DSPy Optimizer Selection Decision & Rationale

**Branch:** `feat/dspy-prompt-layer` (B1)
**Date:** 2026-08-05 / 2026-08-06

## Selected Optimizers

1. **`LabeledFewShot` (Baseline)**
   - Used to generate initial few-shot demonstration sets directly from golden examples.
2. **`BootstrapFewShot` (Selected Optimizer)**
   - Viable and effective for small golden dataset sizes (~10-20 examples).
   - Generates compiled prompt JSON artifacts (`compiled_request_parsing.json`, `compiled_field_mapping.json`) checked directly into source control.

## Deferral Decision on `MIPROv2` and `GEPA`

> [!IMPORTANT]
> `MIPROv2` and `GEPA` optimizers are explicitly **DEFERRED** until golden datasets grow beyond ~200 examples.

### Rationale

- **Dataset Size:** The current golden evaluation datasets contain 14 cases (`request_parsing`) and 9 cases (`field_mapping`).
- **Data Requirements:** According to DSPy optimizer documentation and guidelines, `MIPROv2` requires 200+ examples and 40+ candidate instruction trials to generate statistically meaningful instruction proposals without overfitting.
- **Overfitting Risk:** Running multi-stage instruction search (`MIPROv2`/`GEPA`) on thin datasets risks severe overfitting to specific phrasing patterns rather than generalizing natural language intent.
- **Action Plan:** Maintain `BootstrapFewShot` compiled JSON programs as authoritative checked-in build artifacts. Reconsider `MIPROv2`/`GEPA` only when the prompt evaluation golden dataset scales past ~200 cases.
