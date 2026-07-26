"""Run provenance capture -- answer "which code and which model produced this?"

Step 89 was run against an uncommitted working tree and its artifacts recorded
neither a commit nor an LLM model. The only record that it used gpt-4o (rather
than the o4-mini that ~/.metagpt/config2.yaml carried from 2026-07-09 onward)
lives in prose. That is not something a reviewer can verify.

Every driver under output2/ should call :func:`capture` and persist the result
next to its outputs.

What gets captured
------------------
    git.head            -- commit sha the run started from
    git.dirty           -- True if tracked files differ from HEAD
    git.diff_sha256     -- hash of `git diff HEAD` (identifies the exact
                           uncommitted state; 'clean' when there is no diff)
    git.untracked       -- untracked paths under the watched roots
    llm.model           -- pipeline model from ~/.metagpt/config2.yaml
    llm.judge_model     -- evaluator model, when the driver pins one
    flags               -- every AGENTLAYOUT_* env var actually set
    python              -- interpreter version

``dirty`` + ``diff_sha256`` together mean a dirty run stays identifiable:
re-apply that diff to ``head`` and you have the exact source. Strictly better
than the Step 89 situation, where neither existed.

No timestamp field on purpose: file mtime already carries it, and a clock value
would make two identical runs produce different bytes.

Usage::

    from provenance import capture, write, summary_line
    prov = capture(judge_model="gpt-4o")
    print(summary_line(prov))
    write(out_dir / "provenance.json", prov)
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG2 = Path.home() / ".metagpt" / "config2.yaml"
WATCHED = ("metagpt/ext/agentlayout", "layout_agent/output2")


def _git(*args: str) -> Optional[str]:
    """Run a git command; None if git is unavailable or the command fails."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _git_block() -> Dict[str, object]:
    head = _git("rev-parse", "HEAD")
    if head is None:
        return {"available": False}

    diff = _git("diff", "HEAD") or ""
    untracked: List[str] = []
    for root in WATCHED:
        listing = _git("ls-files", "--others", "--exclude-standard", "--", root)
        if listing:
            untracked.extend(listing.split())

    return {
        "available": True,
        "head": head.strip(),
        "branch": (_git("rev-parse", "--abbrev-ref", "HEAD") or "").strip() or None,
        "dirty": bool(diff.strip()),
        "diff_sha256": hashlib.sha256(diff.encode()).hexdigest() if diff.strip() else "clean",
        "untracked": sorted(untracked),
    }


def _llm_block(judge_model: Optional[str]) -> Dict[str, object]:
    """Pipeline model from config2.yaml, parsed without importing metagpt so
    provenance capture never fails on a config the runtime would reject."""
    model = None
    if CONFIG2.exists():
        try:
            import yaml

            model = (yaml.safe_load(CONFIG2.read_text()) or {}).get("llm", {}).get("model")
        except Exception as err:  # noqa: BLE001 -- provenance must never crash a run
            model = f"<unreadable: {type(err).__name__}>"
    return {"config_path": str(CONFIG2), "model": model, "judge_model": judge_model}


def capture(judge_model: Optional[str] = None) -> Dict[str, object]:
    """Snapshot the run environment. Never raises.

    ``judge_model`` is for drivers that pin an evaluator independent of the
    pipeline model (e.g. step92 pins gpt-4o while config2 says o4-mini).
    """
    return {
        "git": _git_block(),
        "llm": _llm_block(judge_model),
        "flags": {k: v for k, v in sorted(os.environ.items())
                  if k.startswith("AGENTLAYOUT_")},
        "python": platform.python_version(),
    }


def write(path: Path, prov: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prov, indent=2, ensure_ascii=False))


def summary_line(prov: Dict[str, object]) -> str:
    """One line for stdout, so a dirty run is visible while it happens."""
    g = prov.get("git", {})
    if not g.get("available"):
        return "[provenance] git unavailable"
    state = "DIRTY" if g.get("dirty") else "clean"
    n_untracked = len(g.get("untracked", []))
    llm = prov.get("llm", {})
    judge = llm.get("judge_model")
    model = f"model={llm.get('model')}" + (f" judge={judge}" if judge else "")
    extra = f", {n_untracked} untracked" if n_untracked else ""
    return f"[provenance] {str(g.get('head', '?'))[:8]} ({state}{extra})  {model}"


if __name__ == "__main__":
    _p = capture()
    print(summary_line(_p))
    print(json.dumps(_p, indent=2, ensure_ascii=False))
