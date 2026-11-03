"""Top-level CLI dispatch for the `wattson` command.

  wattson                      → live dashboard (Rich Live, Ctrl+C to quit)
  wattson live  [-i SEC]       → same, with optional refresh interval
  wattson tui                  → original Textual app (full screens)
  wattson status [--json]      → one-shot snapshot, print and exit
  wattson diagnose [--json]    → sample N seconds, explain bottlenecks
  wattson events [--json]      → recent watchdog alerts
  wattson kill <pid>           → SIGTERM the given PID
  wattson nice <pid> <low|normal|high>
  wattson power <watts>        → set GPU0 power limit

The interactive-features-as-CLI-subcommands pattern is what lets the
Live dashboard stay tiny and snappy — actions don't need an event
loop, just an exit code and a printed result.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__


def _cmd_live(args: argparse.Namespace) -> int:
    from . import live

    live.run(interval=args.interval)
    return 0


def _cmd_tui(_args: argparse.Namespace) -> int:
    from .app import main as tui_main

    tui_main()
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    if args.json:
        from . import diagnose

        summary = diagnose.summarize(diagnose.sample(duration=1.0, interval=1.0))
        print(json.dumps(summary, indent=2))
        return 0
    from . import live

    return live.status()


def _cmd_diagnose(args: argparse.Namespace) -> int:
    from . import diagnose

    if not args.json:
        print(f"wattson: sampling for {args.duration:g} s …", file=sys.stderr)
    rep = diagnose.run(duration=args.duration, interval=args.interval)
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        from rich.console import Console

        Console().print(diagnose.render_text(rep))
    return 0


def _cmd_events(args: argparse.Namespace) -> int:
    from .watchdog import Watchdog

    events = Watchdog().recent_events(limit=args.limit)
    if args.json:
        print(json.dumps(events, indent=2))
        return 0
    if not events:
        print("no watchdog events logged yet (they are recorded while `wattson tui` runs)")
        return 0
    for e in events:
        print(f"{e.get('ts', '?')}  {e.get('severity', '?'):<4}  {e.get('message', '')}")
    return 0


def _cmd_kill(args: argparse.Namespace) -> int:
    from .probes import processes

    result = processes.terminate(args.pid)
    print(result["message"])
    return 0 if result["ok"] else 1


def _cmd_nice(args: argparse.Namespace) -> int:
    from .probes import processes

    result = processes.set_priority(args.pid, args.level)
    print(result["message"])
    return 0 if result["ok"] else 1


def _cmd_power(args: argparse.Namespace) -> int:
    from .probes import gpu

    result = gpu.set_power_limit(args.gpu, args.watts)
    print(result["message"])
    return 0 if result["ok"] else 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wattson",
        description=(
            "your machine's personal assistant — system monitor for DL boxes"
        ),
    )
    p.add_argument(
        "--version", action="version", version=f"wattson {__version__}",
    )
    sub = p.add_subparsers(dest="command")

    p_live = sub.add_parser(
        "live", help="Rich Live dashboard (default; Ctrl+C to quit)",
    )
    p_live.add_argument(
        "-i", "--interval", type=float, default=1.0,
        help="redraw interval in seconds (default 1.0)",
    )
    p_live.set_defaults(func=_cmd_live)

    p_tui = sub.add_parser(
        "tui", help="full interactive TUI (textual; has the watchdog/trend/drill screens)",
    )
    p_tui.set_defaults(func=_cmd_tui)

    p_status = sub.add_parser(
        "status", help="print one snapshot and exit",
    )
    p_status.add_argument(
        "--json", action="store_true",
        help="machine-readable snapshot (for scripts and AI agents)",
    )
    p_status.set_defaults(func=_cmd_status)

    p_diag = sub.add_parser(
        "diagnose",
        help="sample the machine and explain DL training bottlenecks",
    )
    p_diag.add_argument(
        "-d", "--duration", type=float, default=10.0,
        help="seconds to sample (default 10; run it while training is going)",
    )
    p_diag.add_argument(
        "-i", "--interval", type=float, default=1.0,
        help="seconds between samples (default 1.0)",
    )
    p_diag.add_argument(
        "--json", action="store_true",
        help="emit findings + summary as JSON (for AI agents)",
    )
    p_diag.set_defaults(func=_cmd_diagnose)

    p_events = sub.add_parser("events", help="show recent watchdog alerts")
    p_events.add_argument(
        "-n", "--limit", type=int, default=50,
        help="max events to show (default 50)",
    )
    p_events.add_argument("--json", action="store_true", help="emit JSON")
    p_events.set_defaults(func=_cmd_events)

    p_kill = sub.add_parser("kill", help="SIGTERM a PID")
    p_kill.add_argument("pid", type=int)
    p_kill.set_defaults(func=_cmd_kill)

    p_nice = sub.add_parser("nice", help="set process priority")
    p_nice.add_argument("pid", type=int)
    p_nice.add_argument("level", choices=("low", "normal", "high"))
    p_nice.set_defaults(func=_cmd_nice)

    p_power = sub.add_parser("power", help="set GPU power-limit (watts)")
    p_power.add_argument("watts", type=int)
    p_power.add_argument(
        "--gpu", type=int, default=0,
        help="GPU index (default 0)",
    )
    p_power.set_defaults(func=_cmd_power)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # Default: live dashboard with 1 s interval.
        from . import live

        live.run(interval=1.0)
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
