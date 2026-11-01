"""Command line.

  jobagent list                          show the registered tools
  jobagent run <tool> --field value      run one tool, no LLM orchestration
  jobagent agents                        show the registered agents
  jobagent pipeline "<request>"          run the orchestrator graph
  jobagent pipeline --resume <thread>    continue a run that stopped part-way
  jobagent score [--user N]              the per-user stages only (learn from new marks, scope, fast score)
  jobagent secret-key                    print a new SECRET_KEY for .env
  jobagent users                         list accounts
  jobagent passwd --user N [--email E]   set a user's password (prompts), e.g. to claim the admin account
  jobagent ui                            start the job review web page
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

from jobagent.tools import TOOLS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobagent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show the registered tools")
    run = sub.add_parser("run", help="run one tool").add_subparsers(dest="tool", required=True)
    for spec in TOOLS.values():
        p = run.add_parser(spec.name, help=spec.description, description=spec.description)
        p.add_argument("--json", dest="_json", help="all arguments as one JSON object")
        for name, field in spec.input_model.model_fields.items():
            p.add_argument(f"--{name.replace('_', '-')}", dest=name, required=False, help=field.description)
    sub.add_parser("agents", help="show the registered agents")
    pipe = sub.add_parser("pipeline", help="run the orchestrator graph")
    pipe.add_argument("request", nargs="?", default="Daily pull of all configured targets.")
    pipe.add_argument("--resume", metavar="THREAD", help="thread id of a run to continue")
    score = sub.add_parser("score", help="run the per-user stages (learn from new marks, scope, fast score) with no orchestrator")
    score.add_argument("--user", type=int, default=None, help="user id (default: DEFAULT_USER_ID)")
    sub.add_parser("secret-key", help="print a new SECRET_KEY for .env")
    sub.add_parser("users", help="list accounts")
    pw = sub.add_parser("passwd", help="set a user's password (and optionally email); prompts for the password")
    pw.add_argument("--user", type=int, required=True, help="user id (1 = the admin created by the migration)")
    pw.add_argument("--email", default=None, help="also change the account's email")
    pw.add_argument("--password", default=None, help="the new password (prompted for if omitted)")
    ui = sub.add_parser("ui", help="start the job review web page")
    ui.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this machine only)")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-reload", action="store_true", help="production: do not watch the code for changes")
    ui.add_argument("--proxy-headers", action="store_true",
                    help="behind a reverse proxy: trust X-Forwarded-* from FORWARDED_ALLOW_IPS (default: localhost)")
    args = vars(parser.parse_args(argv))
    # progress goes to stderr, so stdout stays clean JSON / the final report
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("jobagent").setLevel(logging.INFO)

    if args["cmd"] == "agents":
        from jobagent.agents import AGENTS
        for a in AGENTS.values():
            print(f"{a.name} [{a.kind}]\n    {a.description}\n    tools: {', '.join(a.tools)}")
        return 0
    if args["cmd"] == "ui":
        import uvicorn
        print(f"Job review: http://{args['host']}:{args['port']}  (Ctrl-C to stop)")
        # one worker process on purpose: the run queue lives in the server process. reload=True
        # restarts it when the code changes (development); --no-reload for the service.
        uvicorn.run("jobagent.web.app:app", host=args["host"], port=args["port"], log_level="warning",
                    reload=not args["no_reload"], reload_dirs=None if args["no_reload"] else [str(Path(__file__).parent / "web")],
                    proxy_headers=args["proxy_headers"],
                    forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1") if args["proxy_headers"] else None)
        return 0
    if args["cmd"] == "secret-key":
        from jobagent.secrets import new_key
        print(f"SECRET_KEY={new_key()}")
        return 0
    if args["cmd"] == "users":
        from sqlalchemy import select
        from jobagent.db import session
        from jobagent.models import User
        with session() as s:
            for u in s.scalars(select(User).order_by(User.id)):
                print(f"{u.id:>3}  {u.email:<32} {u.role:<7} {u.status:<9} {'password set' if u.password_hash else 'NO PASSWORD'}  {u.name or ''}")
        return 0
    if args["cmd"] == "passwd":
        import getpass
        from jobagent.auth import AuthError, set_password
        password = args["password"] or getpass.getpass("New password: ")
        if not args["password"] and password != getpass.getpass("Again: "):
            print("passwords differ", file=sys.stderr)
            return 1
        try:
            set_password(args["user"], password, args["email"])
        except AuthError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"password set for user {args['user']}" + (f", email {args['email']}" if args["email"] else ""))
        return 0
    if args["cmd"] == "score":
        from jobagent.agents.graph import run_user_stages
        from jobagent.users import resolve_user
        reports = run_user_stages(resolve_user(args["user"]))
        for r in reports:
            print(f"{r['agent']}: {r['status']}" + (f" - {r['error']}" if r["error"] else ""))
            for c in r["tool_calls"]:
                print("  " + json.dumps({c["tool"]: c["result"]})[:600])
        return 1 if any(r["status"] != "ok" for r in reports) else 0
    if args["cmd"] == "pipeline":
        from jobagent.agents.graph import run_pipeline
        thread, state = run_pipeline(args["request"], thread_id=args["resume"], resume=bool(args["resume"]))
        print(state.get("final_report", "(no report)"))
        print(f"\nthread: {thread}")
        return 0

    if args["cmd"] == "list":
        for spec in TOOLS.values():
            print(f"{spec.name}\n    {spec.description}")
        return 0
    spec = TOOLS[args["tool"]]
    kwargs = json.loads(args["_json"]) if args["_json"] else {
        k: v for k, v in args.items() if k in spec.input_model.model_fields and v is not None
    }
    result = spec.call(**kwargs)
    print(result.model_dump_json(indent=2))
    return 1 if getattr(result, "error", None) else 0


if __name__ == "__main__":
    sys.exit(main())
