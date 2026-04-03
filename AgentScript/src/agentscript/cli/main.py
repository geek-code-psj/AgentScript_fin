"""Command-line interface for AgentScript."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sqlite3

from agentscript.compiler.errors import CompilerError
from agentscript.compiler.ir import format_ir, lower_file
from agentscript.compiler.lexer import lex_file
from agentscript.compiler.parser import parse_file
from agentscript.compiler.printer import format_ast
from agentscript.compiler.semantics import analyze_file, format_semantic_model
from agentscript.demo.legal_demo import build_demo_registry
from agentscript.observability.store import TraceStore
from agentscript.runtime import AsyncInterpreter, ToolRegistry, compile_runtime_file
from agentscript.runtime.tracing import SQLiteTraceRecorder, SQLiteTraceReplayer, format_replay


def _build_parser() -> argparse.ArgumentParser:
    cli_parser = argparse.ArgumentParser(prog="agentscript")
    subparsers = cli_parser.add_subparsers(dest="command", required=True)

    lex_parser = subparsers.add_parser("lex", help="Tokenize an AgentScript source file.")
    lex_parser.add_argument("path", type=Path, help="Path to a .as source file.")

    parse_parser = subparsers.add_parser(
        "parse",
        help="Parse an AgentScript source file and print the AST.",
    )
    parse_parser.add_argument("path", type=Path, help="Path to a .as source file.")

    check_parser = subparsers.add_parser(
        "check",
        help="Run semantic analysis on an AgentScript source file.",
    )
    check_parser.add_argument("path", type=Path, help="Path to a .as source file.")

    compile_parser = subparsers.add_parser(
        "compile",
        help="Lower an AgentScript source file into IR.",
    )
    compile_parser.add_argument("path", type=Path, help="Path to a .as source file.")

    run_parser = subparsers.add_parser(
        "run",
        help="Execute an AgentScript workflow with an optional tool profile.",
    )
    run_parser.add_argument("path", type=Path, help="Path to a .as source file.")
    run_parser.add_argument("--workflow", default=None, help="Workflow name to execute.")
    run_parser.add_argument("--agent", default=None, help="Optional agent policy to use.")
    run_parser.add_argument(
        "--arg",
        action="append",
        default=[],
        help='Workflow argument in key=value form. Example: --arg query="BNS theft appeal"',
    )
    run_parser.add_argument(
        "--trace",
        type=Path,
        default=None,
        help="Optional SQLite trace path for recording the run.",
    )
    run_parser.add_argument(
        "--replay-from",
        type=Path,
        default=None,
        help="Replay tool results from an existing trace file.",
    )
    run_parser.add_argument(
        "--replay-run-id",
        default=None,
        help="Optional run id within the replay trace file.",
    )
    run_parser.add_argument(
        "--demo",
        choices=["legal"],
        default=None,
        help="Use a built-in demo tool profile.",
    )
    run_parser.add_argument(
        "--mode",
        choices=["happy", "retry", "outage", "bad-model"],
        default="happy",
        help="Demo mode when using a built-in tool profile.",
    )

    replay_parser = subparsers.add_parser(
        "replay",
        help="Replay a recorded runtime trace from a SQLite trace file.",
    )
    replay_parser.add_argument("trace_path", type=Path, help="Path to a trace SQLite file.")
    replay_parser.add_argument(
        "--run-id",
        dest="run_id",
        default=None,
        help="Optional run id to replay. Defaults to the latest run in the trace DB.",
    )

    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="Serve or inspect the observability dashboard for a trace file.",
    )
    dashboard_parser.add_argument("trace_path", type=Path, help="Path to a trace SQLite file.")
    dashboard_parser.add_argument("--run-id", default=None, help="Optional run id to inspect.")
    dashboard_parser.add_argument("--host", default="127.0.0.1", help="Dashboard host.")
    dashboard_parser.add_argument("--port", type=int, default=8000, help="Dashboard port.")
    dashboard_parser.add_argument(
        "--dump-json",
        action="store_true",
        help="Print dashboard JSON instead of starting the server.",
    )
    return cli_parser


def main() -> int:
    cli_parser = _build_parser()
    args = cli_parser.parse_args()

    if args.command == "lex":
        try:
            for token in lex_file(args.path):
                print(
                    f"{token.line}:{token.column:<4} "
                    f"{token.type.name:<18} {token.lexeme!r} "
                    f"literal={token.literal!r}"
                )
        except CompilerError as error:
            cli_parser.exit(status=1, message=f"{error}\n")

    elif args.command == "parse":
        try:
            print(format_ast(parse_file(args.path)))
        except CompilerError as error:
            cli_parser.exit(status=1, message=f"{error}\n")

    elif args.command == "check":
        try:
            print(format_semantic_model(analyze_file(args.path)))
        except CompilerError as error:
            cli_parser.exit(status=1, message=f"{error}\n")

    elif args.command == "compile":
        try:
            print(format_ir(lower_file(args.path)))
        except CompilerError as error:
            cli_parser.exit(status=1, message=f"{error}\n")

    elif args.command == "run":
        try:
            print(json.dumps(asyncio.run(_run_workflow_command(args)), indent=2, sort_keys=True))
        except (CompilerError, RuntimeError, ValueError, sqlite3.Error) as error:
            cli_parser.exit(status=1, message=f"{error}\n")

    elif args.command == "replay":
        try:
            replayer = SQLiteTraceReplayer(args.trace_path)
            try:
                print(format_replay(replayer.replay(args.run_id)))
            finally:
                replayer.close()
        except (CompilerError, ValueError, sqlite3.Error) as error:
            cli_parser.exit(status=1, message=f"{error}\n")

    elif args.command == "dashboard":
        try:
            if args.dump_json:
                print(json.dumps(_dump_dashboard_payload(args.trace_path, args.run_id), indent=2, sort_keys=True))
            else:
                create_app = _load_dashboard_app_factory()
                import uvicorn

                uvicorn.run(
                    create_app(args.trace_path),
                    host=args.host,
                    port=args.port,
                    log_level="info",
                )
        except (CompilerError, ValueError, sqlite3.Error, RuntimeError) as error:
            cli_parser.exit(status=1, message=f"{error}\n")

    return 0


async def _run_workflow_command(args: argparse.Namespace) -> dict[str, object]:
    program = compile_runtime_file(args.path)
    registry: ToolRegistry
    demo_state: dict[str, object] | None = None
    demo_name = args.demo
    if demo_name is None and args.path.name == "legal_research.as":
        demo_name = "legal"

    if demo_name == "legal":
        registry, state = build_demo_registry(args.mode)
        demo_state = {
            "mode": state.mode,
            "search_calls": state.search_calls,
            "filter_calls": state.filter_calls,
            "summary_calls": state.summary_calls,
            "fallback_calls": state.fallback_calls,
        }
    else:
        registry = ToolRegistry()

    recorder = SQLiteTraceRecorder(args.trace) if args.trace is not None else None
    replay_replayer = (
        SQLiteTraceReplayer(args.replay_from)
        if args.replay_from is not None
        else None
    )
    replay_source = (
        replay_replayer.load_source(args.replay_run_id)
        if replay_replayer is not None
        else None
    )
    interpreter = AsyncInterpreter(
        program,
        tools=registry,
        trace_recorder=recorder,
        replay_source=replay_source,
    )
    workflow_name = args.workflow or next(iter(program.workflows))
    try:
        output = await interpreter.run_workflow(
            workflow_name,
            arguments=_parse_arguments(args.arg),
            agent_name=args.agent,
        )
        if demo_name == "legal":
            demo_state = {
                "mode": state.mode,
                "search_calls": state.search_calls,
                "filter_calls": state.filter_calls,
                "summary_calls": state.summary_calls,
                "fallback_calls": state.fallback_calls,
            }
        return {
            "workflow": workflow_name,
            "run_id": interpreter.last_run_id,
            "trace_path": None if args.trace is None else str(args.trace),
            "output": output,
            "demo_state": demo_state,
        }
    finally:
        if replay_replayer is not None:
            replay_replayer.close()
        if recorder is not None:
            recorder.close()


def _parse_arguments(values: list[str]) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"Invalid argument '{item}'. Expected key=value.")
        key, raw = item.split("=", 1)
        parsed[key] = _parse_scalar(raw)
    return parsed


def _parse_scalar(raw: str) -> object:
    text = raw.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _dump_dashboard_payload(trace_path: Path, run_id: str | None) -> dict[str, object]:
    store = TraceStore(trace_path)
    try:
        runs = store.list_runs(limit=10)
        chosen_run_id = run_id or (runs[0].run_id if runs else None)
        return {
            "runs": [run.to_dict() for run in runs],
            "selected_run": (
                None if chosen_run_id is None else store.dashboard_payload(chosen_run_id)
            ),
        }
    finally:
        store.close()


def _load_dashboard_app_factory():
    try:
        from agentscript.observability.server import create_app
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(
            "Dashboard dependencies are unavailable. Install them with "
            "`pip install -e .[dashboard]` and try again."
        ) from error
    return create_app


if __name__ == "__main__":
    raise SystemExit(main())
