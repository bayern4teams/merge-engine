"""Render every Mermaid block in the doc set (J3/J12): a block that does not
render fails the check. Bad Mermaid syntax otherwise ships silently until a
reader opens the page.

Usage: python scripts/check_mermaid.py [file.md ...]
Default files: the J12 doc set (missing files are skipped — prompt 2 creates
ARCHITECTURE.md / TECHNICAL_IMPLEMENTATION.md / GLOSSARY.md / docs/LESSONS.md).

Needs @mermaid-js/mermaid-cli (`mmdc`). It renders headless Chromium: if a
puppeteer browser is not installed, we look for a system chromium/chrome and
hand it to mmdc via a temporary puppeteer config; otherwise mmdc's own
resolution applies (CI installs one with
`npx puppeteer browsers install chrome-headless-shell`).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_FILES = [
    "README.md",
    "ARCHITECTURE.md",
    "TECHNICAL_IMPLEMENTATION.md",
    "GLOSSARY.md",
    "docs/LESSONS.md",
    "AGENTS.md",
]

_BLOCK_RE = re.compile(r"^```mermaid\s*\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)


def _puppeteer_args(tmp: Path) -> list[str]:
    exe = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
    )
    if exe is None:
        return []
    cfg = tmp / "puppeteer.json"
    cfg.write_text(
        f'{{"executablePath": "{exe}", "args": ["--no-sandbox", "--disable-gpu"]}}',
        encoding="utf-8",
    )
    return ["-p", str(cfg)]


def main(argv: list[str]) -> int:
    mmdc = shutil.which("mmdc")
    if mmdc is None:
        print(
            "check_mermaid: 'mmdc' not on PATH. Install with "
            "`npm install -g @mermaid-js/mermaid-cli` (J12 tool).",
            file=sys.stderr,
        )
        return 1

    files = [Path(a) for a in argv] if argv else [ROOT / f for f in DEFAULT_FILES]
    failures: list[str] = []
    total = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        extra = _puppeteer_args(tmp)
        for md in files:
            if not md.exists():
                continue
            text = md.read_text(encoding="utf-8")
            for i, match in enumerate(_BLOCK_RE.finditer(text), start=1):
                total += 1
                src = tmp / f"block_{md.stem}_{i}.mmd"
                out = tmp / f"block_{md.stem}_{i}.svg"
                src.write_text(match.group(1), encoding="utf-8")
                line_no = text.count("\n", 0, match.start()) + 1
                try:
                    done = subprocess.run(
                        [mmdc, *extra, "-i", str(src), "-o", str(out)],
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                except (subprocess.SubprocessError, OSError) as exc:
                    failures.append(f"{md}:{line_no}: mmdc could not run: {exc}")
                    continue
                if done.returncode != 0 or not out.exists():
                    tail = " | ".join((done.stderr or done.stdout or "").splitlines()[-3:])
                    failures.append(f"{md}:{line_no}: mermaid block #{i} did not render: {tail}")

    if failures:
        print(f"\n{len(failures)}/{total} mermaid block(s) failed to render:")
        for f in failures:
            print("  " + f)
        return 1
    print(f"check_mermaid: {total} block(s) render OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
