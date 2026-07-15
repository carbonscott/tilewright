"""tcb_min.cli — the unified `tcb` command.

A thin dispatcher: `tcb manifest ...` and `tcb register ...` forward the
remaining arguments to tcb_min.manifest / tcb_min.register, each of which
parses and documents its own options (`tcb manifest --help`)."""

import sys

from tcb_min import manifest, register

COMMANDS = {
    "manifest": (manifest.main, "validate a dataset YAML and generate Parquet manifests"),
    "register": (register.main, "register manifests into a running Tiled server over HTTP"),
}


def _usage():
    rows = "\n".join(f"  {name:<9}{help_}" for name, (_, help_) in COMMANDS.items())
    return (f"usage: tcb <command> [options]\n\ncommands:\n{rows}\n\n"
            "Run `tcb <command> --help` for command-specific options.")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(_usage())
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd not in COMMANDS:
        sys.stderr.write(f"tcb: unknown command {cmd!r}\n\n{_usage()}\n")
        return 2
    return COMMANDS[cmd][0](rest)


if __name__ == "__main__":
    sys.exit(main())
