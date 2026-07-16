"""tilewright.cli — the unified `tilewright` command.

A thin dispatcher: `tilewright manifest ...` and `tilewright register ...` forward
the remaining arguments to tilewright.manifest / tilewright.register, each of which
parses and documents its own options (`tilewright manifest --help`)."""

import sys

from tilewright import manifest, register

COMMANDS = {
    "manifest": (manifest.main, "validate a dataset YAML and generate Parquet manifests"),
    "register": (register.main, "register manifests into a running Tiled server over HTTP"),
}


def _usage():
    rows = "\n".join(f"  {name:<9}{help_}" for name, (_, help_) in COMMANDS.items())
    return (f"usage: tilewright <command> [options]\n\ncommands:\n{rows}\n\n"
            "Run `tilewright <command> --help` for command-specific options.")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(_usage())
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd not in COMMANDS:
        sys.stderr.write(f"tilewright: unknown command {cmd!r}\n\n{_usage()}\n")
        return 2
    return COMMANDS[cmd][0](rest)


if __name__ == "__main__":
    sys.exit(main())
