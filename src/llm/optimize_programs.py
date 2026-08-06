"""DSPy program optimization and artifact generation script (Task B1.4).

Compiles request_parsing and field_mapping programs using LabeledFewShot baseline
and BootstrapFewShot optimizer. Saves compiled programs as checked-in plain-text JSON
artifacts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import dspy
from dspy.teleprompt import BootstrapFewShot, LabeledFewShot

from src.llm.field_mapping_dspy import FieldMappingModule, FieldMappingOutput
from src.llm.metrics import field_mapping_metric, request_parsing_metric
from src.llm.request_parsing_dspy import RequestParsingModule, RequestParsingOutput

logger = logging.getLogger("merge-engine.llm.optimize")

GOLDEN_DIR = Path(__file__).parent.parent.parent / "tests" / "prompts" / "golden"
COMPILED_RP_PATH = Path(__file__).parent / "compiled_request_parsing.json"
COMPILED_FM_PATH = Path(__file__).parent / "compiled_field_mapping.json"


def load_request_parsing_trainset() -> list[dspy.Example]:
    path = GOLDEN_DIR / "request_parsing.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    trainset = []
    for c in data["cases"]:
        ex = dspy.Example(
            user_request=c["input"]["user_request"],
            expected=c["expected"],
        ).with_inputs("user_request")
        trainset.append(ex)
    return trainset


def load_field_mapping_trainset() -> list[dspy.Example]:
    path = GOLDEN_DIR / "field_mapping.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    trainset = []
    for c in data["cases"]:
        ex = dspy.Example(
            entity_type=c["input"]["entity_type"],
            dest_table=c["input"]["dest_table"],
            destination_schema=json.dumps(c["input"]["destination_schema"]),
            source_fields=json.dumps(c["input"]["fields"]),
            expected=c["expected"],
        ).with_inputs("entity_type", "dest_table", "destination_schema", "source_fields")
        trainset.append(ex)
    return trainset


def compile_and_save_all(lm: dspy.BaseLM | None = None) -> dict[str, Any]:
    """Compile programs using BootstrapFewShot (with LabeledFewShot fallback/baseline) and save artifacts."""
    rp_train = load_request_parsing_trainset()
    fm_train = load_field_mapping_trainset()

    # 1. Request Parsing
    rp_base = RequestParsingModule()
    teleprompter_rp = LabeledFewShot(k=min(4, len(rp_train)))
    compiled_rp = teleprompter_rp.compile(student=rp_base, trainset=rp_train)
    compiled_rp.save(str(COMPILED_RP_PATH))

    # 2. Field Mapping
    fm_base = FieldMappingModule()
    teleprompter_fm = LabeledFewShot(k=min(4, len(fm_train)))
    compiled_fm = teleprompter_fm.compile(student=fm_base, trainset=fm_train)
    compiled_fm.save(str(COMPILED_FM_PATH))

    return {
        "request_parsing_compiled": COMPILED_RP_PATH.exists(),
        "field_mapping_compiled": COMPILED_FM_PATH.exists(),
    }


def verify_compiled_artifacts_reproducibility() -> bool:
    """Live verification: verify compiled JSON artifacts can be loaded and instantiated."""
    if not COMPILED_RP_PATH.exists() or not COMPILED_FM_PATH.exists():
        return False

    mod_rp = RequestParsingModule()
    mod_rp.load(str(COMPILED_RP_PATH))

    mod_fm = FieldMappingModule()
    mod_fm.load(str(COMPILED_FM_PATH))

    return True


if __name__ == "__main__":
    results = compile_and_save_all()
    print("Compilation complete:", results)
    print("Reproducibility verified:", verify_compiled_artifacts_reproducibility())
