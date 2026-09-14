"""One command from a fresh clone to a running app (Part 1.6.C).

Cross-platform (Windows-first, per this repo's shell — see scripts/check.py's
own docstring), and deliberately does not assume conda: it creates a plain
venv with the stdlib `venv` module, so it works identically on a machine that
has never heard of conda or of this project's `na180` environment.

Steps:
    1. Verify the running interpreter satisfies pyproject.toml's
       requires-python (>=3.11) — fail loudly rather than build a venv that
       can't actually run the app.
    2. Create .venv/ if it doesn't exist.
    3. pip install -e ".[dev,perf]" into that venv.
    4. npm install && npm run build in src/ui/frontend, if npm is on PATH —
       skipped (with a clear message) if it isn't, matching check.py
       frontend's own graceful-skip behaviour.
    5. Copy .env.example -> .env if .env doesn't exist yet.
    6. Print the exact command to run the app.

Usage:
    python scripts/bootstrap.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = ROOT / ".venv"
FRONTEND_DIR = ROOT / "src" / "ui" / "frontend"
MIN_PYTHON = (3, 11)


def _venv_python() -> Path:
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _fail(msg: str) -> None:
    print(f"\nFAILED: {msg}", file=sys.stderr)
    sys.exit(1)


def check_python_version() -> None:
    if sys.version_info[:2] < MIN_PYTHON:
        _fail(
            f"Python {'.'.join(map(str, MIN_PYTHON))}+ required "
            f"(pyproject.toml's requires-python), found {sys.version.split()[0]}. "
            "Install a newer Python and re-run this script with it."
        )
    print(f"Python {sys.version.split()[0]} — OK")


def create_venv() -> None:
    if VENV_DIR.exists():
        print(f"{VENV_DIR} already exists — reusing it.")
        return
    print(f"Creating venv at {VENV_DIR} ...")
    result = subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)])
    if result.returncode != 0:
        _fail("python -m venv failed.")


def install_python_deps() -> None:
    py = _venv_python()
    print("Installing merge-engine[dev,perf,schema_matching,dq] ...")
    result = subprocess.run(
        [str(py), "-m", "pip", "install", "-e", f"{ROOT}[dev,perf,schema_matching,dq]"],
        cwd=ROOT,
    )
    if result.returncode != 0:
        _fail("pip install -e '.[dev,perf,schema_matching,dq]' failed — see output above.")


def build_frontend() -> None:
    npm = shutil.which("npm")
    if npm is None:
        print(
            "(npm not found on PATH — skipping frontend build. Install Node.js, "
            "then run `npm install && npm run build` in src/ui/frontend yourself, "
            "or re-run this script once npm is available.)"
        )
        return
    print("Installing frontend dependencies ...")
    if subprocess.run([npm, "install"], cwd=FRONTEND_DIR).returncode != 0:
        _fail("npm install failed in src/ui/frontend.")
    print("Building frontend ...")
    if subprocess.run([npm, "run", "build"], cwd=FRONTEND_DIR).returncode != 0:
        _fail("npm run build failed in src/ui/frontend.")


def copy_env_example() -> None:
    env_path = ROOT / ".env"
    example_path = ROOT / ".env.example"
    if env_path.exists():
        print(f"{env_path} already exists — leaving it alone.")
        return
    if not example_path.exists():
        print("(.env.example not found — skipping)")
        return
    env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Created {env_path} from .env.example — fill in LLM_API_KEY before running.")


def main() -> int:
    check_python_version()
    create_venv()
    install_python_deps()
    build_frontend()
    copy_env_example()

    py = _venv_python()
    print("\nDone. Run the app with:\n")
    print(f"    {py} -m src.main --mode gradio --port 7860\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
