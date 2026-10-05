"""Command line.

  jobagent list                          show the registered tools
  jobagent run <tool> --field value      run one tool, no LLM orchestration
  jobagent agents                        show the registered agents
  jobagent pipeline "<request>"          run the orchestrator graph
  jobagent pipeline --resume <thread>    continue a run that stopped part-way
  jobagent ui                            start the job review web page
"""
import argparse
import json
import logging
import sys

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
    ui = sub.add_parser("ui", help="start the job review web page")
    ui.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this machine only)")
    ui.add_argument("--port", type=int, default=8765)
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
        uvicorn.run("jobagent.web.app:app", host=args["host"], port=args["port"], log_level="warning")
        return 0
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
