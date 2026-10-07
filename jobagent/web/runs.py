"""Start jobagent commands from the web page and follow their output.

A small queue with two lanes: "llm" (one at a time: the GPU serves one model call at a time)
and "default" (a couple in parallel: scoring, training, fetching). Output goes to
data/runs/<id>.log so the page can poll it and it survives a page reload. Queue state is in
data/runs/<id>.json; runs still queued when the server restarts are started again, runs that
were in progress are marked failed.
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from jobagent.config import ROOT

RUNS_DIR = ROOT / "data" / "runs"
KEEP = 60
LANES = {"llm": 1, "pipeline": 1, "default": 2}   # lane -> how many may run at once
# the chat lane is one at a time (one GPU); crawls get their own lane so a slow pull never blocks a chat
TIMEOUTS = {"llm": 15 * 60, "pipeline": 3 * 60 * 60, "default": 60 * 60}   # seconds before the watchdog stops a run
LLM_TOOLS = ("judge_", "enrich_", "job_chat")   # tool-name prefixes that call the shared chat model
_lock = threading.Lock()
_runs: dict[str, dict] = {}
_queue: list[str] = []                    # run ids waiting, in order


def lane_for(argv: list[str]) -> str:
    if argv[:1] == ["pipeline"]:
        return "pipeline"
    if argv[:1] == ["run"] and len(argv) > 1 and argv[1].startswith(LLM_TOOLS):
        return "llm"
    return "default"


def _meta_path(run_id: str):
    return RUNS_DIR / f"{run_id}.json"


def _public(run: dict) -> dict:
    return {k: v for k, v in run.items() if k not in ("proc", "log_fh")}


def _save(run: dict) -> None:
    _meta_path(run["id"]).write_text(json.dumps(_public(run)))


def load_runs() -> list[dict]:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    runs = []
    for path in sorted(RUNS_DIR.glob("*.json"), reverse=True)[:KEEP]:
        try:
            meta = json.loads(path.read_text())
        except ValueError:
            continue
        live = _runs.get(meta["id"])
        runs.append(_public(live) if live else meta)
    return runs


def running() -> dict | None:
    """The oldest run in progress, if any (the page shows it first)."""
    for run in _runs.values():
        if run["status"] == "running":
            return run
    return None


def active() -> list[dict]:
    return [_public(r) for r in _runs.values() if r["status"] in ("running", "queued")]


def start(argv: list[str], label: str, user_id: int | None = None, lane: str | None = None) -> dict:
    """Queue `jobagent <argv>`; it starts as soon as its lane has room."""
    with _lock:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")[:-3]
        run = {"id": run_id, "label": label, "argv": argv, "lane": lane or lane_for(argv), "user_id": user_id,
               "status": "queued", "exit_code": None, "queued_at": datetime.now(timezone.utc).isoformat(),
               "started_at": None, "finished_at": None, "log": str(RUNS_DIR / f"{run_id}.log")}
        _runs[run_id] = run
        _queue.append(run_id)
        _save(run)
        _pump()
        return _public(run)


def _pump() -> None:
    """Start queued runs whose lane has room. Caller holds _lock."""
    busy = {lane: sum(1 for r in _runs.values() if r["status"] == "running" and r["lane"] == lane) for lane in LANES}
    for run_id in list(_queue):
        run = _runs[run_id]
        if busy[run["lane"]] >= LANES[run["lane"]]:
            continue
        _queue.remove(run_id)
        busy[run["lane"]] += 1
        log_fh = open(run["log"], "w")  # noqa: SIM115 - handed to the child process
        run["proc"] = subprocess.Popen(
            [sys.executable, "-m", "jobagent.cli", *run["argv"]], cwd=ROOT, stdout=log_fh, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        run["log_fh"], run["status"], run["started_at"] = log_fh, "running", datetime.now(timezone.utc).isoformat()
        _save(run)
        threading.Thread(target=_wait, args=(run,), daemon=True).start()


def _wait(run: dict) -> None:
    limit = TIMEOUTS.get(run["lane"], 3600)
    try:
        code = run["proc"].wait(timeout=limit)
    except subprocess.TimeoutExpired:  # the watchdog: a run that overruns its lane's limit is stopped
        run["proc"].terminate()
        try:
            code = run["proc"].wait(timeout=15)
        except subprocess.TimeoutExpired:
            run["proc"].kill()
            code = run["proc"].wait()
        run["log_fh"].write(f"\nstopped by the watchdog after {limit // 60} minutes\n")
        run["timed_out"] = True
    run["log_fh"].close()
    with _lock:
        run["status"] = "ok" if code == 0 else "failed"
        run["exit_code"], run["finished_at"] = code, datetime.now(timezone.utc).isoformat()
        _save(run)
        for old in sorted(RUNS_DIR.glob("*.json"))[:-KEEP]:  # keep the directory small
            old.unlink(missing_ok=True)
            old.with_suffix(".log").unlink(missing_ok=True)
        _pump()


def get(run_id: str, tail: int = 400) -> dict | None:
    run = _runs.get(run_id)
    meta = _public(run) if run else None
    if meta is None and _meta_path(run_id).exists():
        meta = json.loads(_meta_path(run_id).read_text())
    if meta is None:
        return None
    path = RUNS_DIR / f"{run_id}.log"
    lines = path.read_text(errors="replace").splitlines() if path.exists() else []
    meta["output"] = "\n".join(lines[-tail:])
    meta["elapsed_s"] = round(time.time() - datetime.fromisoformat(meta["started_at"]).timestamp()) \
        if meta["status"] == "running" and meta.get("started_at") else None
    meta["ahead"] = _queue.index(run_id) if run_id in _queue else None
    return meta


def stop(run_id: str) -> bool:
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return False
        if run["status"] == "queued":
            _queue.remove(run_id)
            run["status"], run["finished_at"] = "failed", datetime.now(timezone.utc).isoformat()
            run["exit_code"] = -1
            _save(run)
            _pump()
            return True
        if run["status"] == "running" and run["proc"].poll() is None:
            run["proc"].terminate()
            return True
    return False


def recover() -> None:
    """On server start: runs left 'running' by a previous server are dead; queued ones start again."""
    for meta in load_runs():
        if meta["status"] == "running" and meta["id"] not in _runs:
            meta["status"], meta["exit_code"] = "failed", -1
            meta["finished_at"] = datetime.now(timezone.utc).isoformat()
            _meta_path(meta["id"]).write_text(json.dumps(meta))
        elif meta["status"] == "queued" and meta["id"] not in _runs:
            with _lock:
                _runs[meta["id"]] = meta
                _queue.append(meta["id"])
    with _lock:
        _queue.sort()
        _pump()
