# Build an instance's database without starting it, and write it out.
#
#     dls-va-ioc-sim dbdump sr99c-va-ioc-01.py before.db
#     ...make the change...
#     dls-va-ioc-sim dbdump sr99c-va-ioc-01.py after.db
#     diff before.db after.db
#
# This is the check that is worth more than running the IOC, and it takes a
# second.  For a change to how an instance is *assembled* - extracting a
# module, generating the file from a builder XML, rewriting the parse - the
# database must not move at all, so the diff should be **empty**.  Both
# rewrites of builder_xml.py and the whole of generate_ioc.py were verified this
# way, at 2080 records each time.
#
# For a change to a device class the diff is meant to be non-empty, and it is
# then the record-by-record statement of what you actually changed.
#
# CLAUDE.md's "Verifying a change" has the cross-version variant, which is a
# different job: 4.0.1 against 4.7 to catch the silent builder field swaps.

import os

import epicsdbbuilder
from softioc import asyncio_dispatcher, builder, softioc


def dumpDatabase(instance, output):
    """Run one instance far enough to build its records, and write them out.

    The instance is a script rather than a module - it builds its records at
    import time and then starts an IOC - so it is exec'd with everything that
    would *start* an IOC stubbed out first.  Returns the number of records.

    This mutates the softioc module's attributes and cannot be undone, so it
    runs once per process: a caller wanting two dumps to compare should take
    them in two runs, which is what the CLI does.
    """
    # LoadDatabase has to be stubbed along with the rest: it *consumes* the
    # recordset, so leaving it in place makes the dump come out empty and it
    # looks as though the instance built no records at all.
    builder.LoadDatabase = lambda *arguments, **keywords: None
    softioc.iocInit = lambda *arguments, **keywords: None
    softioc.interactive_ioc = lambda *arguments, **keywords: None
    asyncio_dispatcher.AsyncioDispatcher = lambda *arguments, **keywords: (
        lambda *inner, **innerKeywords: None
    )

    path = os.path.abspath(instance)
    namespace = {"__name__": "__main__", "__file__": path}
    with open(path) as handle:
        source = handle.read()
    exec(compile(source, instance, "exec"), namespace)  # noqa: S102

    # WriteRecords rather than LoadDatabase, which writes to a temporary file
    # and then deletes it.
    epicsdbbuilder.WriteRecords(output)
    return epicsdbbuilder.CountRecords()
