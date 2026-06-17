"""Top-level CLI dispatch for the `wattson` command.

  wattson                      → live dashboard (Rich Live, Ctrl+C to quit)
  wattson live  [-i SEC]       → same, with optional refresh interval
  wattson tui                  → original Textual app (full screens)
  wattson status               → one-shot snapshot, print and exit
  wattson kill <pid>           → SIGTERM the given PID
  wattson nice <pid> <low|normal|high>
  wattson power <watts>        → set GPU0 power limit

The interactive-features-as-CLI-subcommands pattern is what lets the
Live dashboard stay tiny and snappy — actions don't need an event
loop, just an exit code and a printed result.
"""

from __future__ import annotations

import argparse
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


def _cmd_status(_args: argparse.Namespace) -> int:
    from . import live

    return live.status()


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
    p_status.set_defaults(func=_cmd_status)

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
