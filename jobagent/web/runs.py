"""Start jobagent commands from the web page and follow their output.

One command runs at a time (the stages write to the same tables). Output goes to
data/runs/<id>.log so the page can poll it, and survives a page reload.
"""
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from jobagent.config import ROOT

RUNS_DIR = ROOT / "data" / "runs"
KEEP = 30
_lock = threading.Lock()
_runs: dict[str, dict] = {}


def _meta_path(run_id: str):
    return RUNS_DIR / f"{run_id}.json"


def _save(run: dict) -> None:
    _meta_path(run["id"]).write_text(json.dumps({k: v for k, v in run.items() if k != "proc"}))


def load_runs() -> list[dict]:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    runs = []
    for path in sorted(RUNS_DIR.glob("*.json"), reverse=True)[:KEEP]:
        try:
            runs.append(json.loads(path.read_text()))
        except ValueError:
            continue
    return runs


def running() -> dict | None:
    for run in _runs.values():
        if run["proc"].poll() is None:
            return run
    return None


def start(argv: list[str], label: str) -> dict:
    """Launch `jobagent <argv>` in the background. Raises RuntimeError if one is already running."""
    with _lock:
        if (busy := running()):
            raise RuntimeError(f"a run is already in progress: {busy['label']}")
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        log_path = RUNS_DIR / f"{run_id}.log"
        log = open(log_path, "w")  # noqa: SIM115 - handed to the child process
        proc = subprocess.Popen(
            [sys.executable, "-m", "jobagent.cli", *argv], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            env={**__import__("os").environ, "PYTHONUNBUFFERED": "1"},
        )
        run = {"id": run_id, "label": label, "argv": argv, "status": "running", "exit_code": None,
               "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
               "log": str(log_path), "proc": proc}
        _runs[run_id] = run
        _save(run)
        threading.Thread(target=_wait, args=(run, log), daemon=True).start()
        return {k: v for k, v in run.items() if k != "proc"}


def _wait(run: dict, log) -> None:
    code = run["proc"].wait()
    log.close()
    run["status"] = "ok" if code == 0 else "failed"
    run["exit_code"], run["finished_at"] = code, datetime.now(timezone.utc).isoformat()
    _save(run)
    for old in sorted(RUNS_DIR.glob("*.json"))[:-KEEP]:  # keep the directory small
        old.unlink(missing_ok=True)
        old.with_suffix(".log").unlink(missing_ok=True)


def get(run_id: str, tail: int = 400) -> dict | None:
    run = _runs.get(run_id)
    meta = {k: v for k, v in run.items() if k != "proc"} if run else None
    if meta is None and _meta_path(run_id).exists():
        meta = json.loads(_meta_path(run_id).read_text())
    if meta is None:
        return None
    path = RUNS_DIR / f"{run_id}.log"
    lines = path.read_text(errors="replace").splitlines() if path.exists() else []
    meta["output"] = "\n".join(lines[-tail:])
    meta["elapsed_s"] = round(time.time() - datetime.fromisoformat(meta["started_at"]).timestamp()) \
        if meta["status"] == "running" else None
    return meta


def stop(run_id: str) -> bool:
    run = _runs.get(run_id)
    if run and run["proc"].poll() is None:
        run["proc"].terminate()
        return True
    return False
