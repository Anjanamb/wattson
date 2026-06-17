"""Entry point for `python -m wattson`.

Dispatches through the CLI so subcommands work the same way as the
`wattson` script installed by pyproject.toml.
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
