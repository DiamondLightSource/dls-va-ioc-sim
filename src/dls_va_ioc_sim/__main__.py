"""The command line: ``dls-va-ioc-sim`` and ``python -m dls_va_ioc_sim``.

Three things to do with a builder XML, which is the only input this tool has:

    generate    write a simulation instance and its launcher, to be edited
    run         build the same devices and serve them, without writing a file
    dbdump      build an instance's records and dump them, without an IOC

`generate` is the usual way in.  The file it writes is the configuration: it
can be read by someone who has never seen the XML, and the one thing an XML
cannot say - which valve stands between which two lengths of pipe - is left in
it marked EDIT ME.  `run` is the same devices with that layout left as the
guess, for a look at a cell without committing to a file.  `dbdump` is the
check that a change to the framework has not moved a record it should not
have: dump before, dump after, diff.
"""

from argparse import ArgumentParser, Namespace
from collections.abc import Sequence

from . import __version__
from .generate_ioc import addArguments, generateCommand

__all__ = ["main"]


def runCommand(arguments: Namespace) -> int:
    """Build one XML's devices and serve them, with the layout left as a guess.

    Nothing is written, so the beam pipe cannot have been thought about: every
    domain becomes one volume with nothing joining it to the next, which is
    enough to watch a cell pump down and not enough to believe.  Generate an
    instance when the layout matters.
    """
    # Imported here rather than at module scope so that `generate`, which
    # builds no records at all, does not pay for softioc - and still runs on a
    # machine with no EPICS on it.
    from .parsed_ioc import iocFromXml  # noqa: PLC0415
    from .vacuum_model import gate, vacuumLayout, vacuumVolume  # noqa: PLC0415

    ioc = iocFromXml(arguments.xml, cell=arguments.cell)
    print(ioc.report())

    # The generated layout is Python, because its usual home is a generated
    # file.  Run it here to get the same one volume per domain.
    namespace: dict[str, object] = {
        "gate": gate,
        "vacuumLayout": vacuumLayout,
        "vacuumVolume": vacuumVolume,
    }
    exec(  # noqa: S102
        compile(ioc.layoutTemplate(), "<generated layout>", "exec"), namespace
    )
    ioc.attach(namespace["vacuum"])

    ioc.run(interactive=arguments.interactive, namespace=namespace)
    return 0


def dbdumpCommand(arguments: Namespace) -> int:
    """Build an instance's records without starting it, and write them out."""
    from .dbdump import dumpDatabase  # noqa: PLC0415

    count = dumpDatabase(arguments.instance, arguments.output)
    print(f"{arguments.instance}: {count} records -> {arguments.output}")
    return 0


def main(args: Sequence[str] | None = None) -> None:
    """Argument parser for the CLI."""
    parser = ArgumentParser(
        prog="dls-va-ioc-sim",
        description="Simulate a DLS vacuum IOC from the builder XML the real "
        "one is built from.",
    )
    parser.add_argument("-v", "--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    generate = subcommands.add_parser(
        "generate",
        help="write a simulation instance and its launcher from a builder XML",
    )
    addArguments(generate)
    generate.set_defaults(
        command=lambda arguments: generateCommand(arguments, error=generate.error)
    )

    run = subcommands.add_parser(
        "run",
        help="build one XML's devices and serve them, without writing a file",
    )
    run.add_argument("xml", help="the builder XML the real IOC is built from")
    run.add_argument(
        "cell",
        nargs="?",
        default="99",
        help="the two digit cell to rename into, so the simulation cannot be "
        "taken for the real machine (default 99)",
    )
    run.add_argument(
        "--no-interactive",
        dest="interactive",
        action="store_false",
        help="do not drop into the IOC shell once it is up",
    )
    run.set_defaults(command=runCommand)

    dbdump = subcommands.add_parser(
        "dbdump",
        help="build an instance's records without starting it, and dump them",
    )
    dbdump.add_argument("instance", help="a generated instance, as written by generate")
    dbdump.add_argument("output", help="the .db file to write")
    dbdump.set_defaults(command=dbdumpCommand)

    arguments = parser.parse_args(args)
    raise SystemExit(arguments.command(arguments))


if __name__ == "__main__":
    main()
