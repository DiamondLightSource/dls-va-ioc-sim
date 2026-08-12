# Run one or more generated instances as a single IOC.
#
#     dls-va-ioc-sim start sr06c-va-ioc-01.py
#     dls-va-ioc-sim start /epics/ioc/config/*.py
#
# An instance is a script: it builds its records and then starts an IOC.  To
# run several of them in one process the four things that *start* an IOC are
# stubbed out while each file is exec'd - which is what dbdump already does to
# build a database without starting anything - and the IOC is then started
# once over the merged recordset.  Nothing in the instances changes, and one
# file behaves exactly as running that file directly does.
#
# Why one process rather than one per cell: Channel Access is served on a
# port, not a name.  Under Kubernetes with hostNetwork every pod on a node
# shares the node's network stack, so 24 pods would be 24 attempts to bind
# 6064 and 23 failures - and giving each one a different port means every
# client has to know which cell is on which.  One process serves all of them
# on the one port, and costs a third of the memory of 24 interpreters.
#
# Each file gets its own namespace.  Every instance has a `vacuum`, an
# `upstream` and a `dispatcher` at module scope, so merging them into one
# namespace would have cells quietly overwriting each other.  The namespaces
# are handed to the IOC shell as `instances`, keyed by filename, which is
# where a leak is sprung when more than one cell is running:
#
#     >>> instances["sr06c-va-ioc-01.py"]["upstream"].gasLoad = 1.0e-4

import asyncio
import logging
import os

import epicsdbbuilder
from softioc import asyncio_dispatcher, builder, softioc
from softioc.imports import callbackSetQueueSize

SIMULATION_PERIOD = 1.0

# Every .set() from the tick loop queues a record process on EPICS's cbLow
# ring, which defaults to 2000 entries.  One cell never comes near that; 24
# cells put ~50000 on it in a single pass, and the overflow is silent - the
# records are simply not processed, so readbacks freeze and CA monitors miss
# updates, with nothing but `callbackRequest: ERROR cbLow ring buffer full` on
# stderr to say so.  Size the ring from the database instead of guessing: a
# ring entry is a pointer, so being generous costs almost nothing.
CALLBACK_QUEUE_PER_RECORD = 4
MINIMUM_CALLBACK_QUEUE = 20000


def _nothing(*arguments, **keywords):
    """Stand in for anything that would start an IOC while a file is read."""
    return None


def callbackQueueSize(records):
    """How many entries the cbLow ring needs for a database of this size."""
    return max(MINIMUM_CALLBACK_QUEUE, CALLBACK_QUEUE_PER_RECORD * records)


def loadInstances(paths):
    """Build every instance's records into the one recordset, and stop there.

    Returns the instances' namespaces, keyed by filename, in the order they
    were given.  Nothing is started: `LoadDatabase`, `iocInit`,
    `interactive_ioc` and the dispatcher are stubbed out for the duration and
    put back afterwards, so what each file does is build records.
    """
    saved = (
        builder.LoadDatabase,
        softioc.iocInit,
        softioc.interactive_ioc,
        asyncio_dispatcher.AsyncioDispatcher,
    )
    builder.LoadDatabase = _nothing
    softioc.iocInit = _nothing
    softioc.interactive_ioc = _nothing
    # The instance keeps whatever this returns and calls it to dispatch its own
    # tick loop; there is one loop here instead, over all of them.
    asyncio_dispatcher.AsyncioDispatcher = lambda *a, **k: _nothing

    instances = {}
    try:
        for path in paths:
            name = os.path.basename(path)
            if name in instances:
                raise ValueError(
                    f"{name} was given twice - two instances of one cell would "
                    "build the same record names"
                )
            namespace = {"__name__": "__main__", "__file__": os.path.abspath(path)}
            with open(path) as handle:
                source = handle.read()
            exec(compile(source, name, "exec"), namespace)  # noqa: S102
            if "simulatedDevices" not in namespace:
                raise ValueError(
                    f"{name} defines no simulatedDevices - it does not look "
                    "like an instance written by `dls-va-ioc-sim generate`"
                )
            instances[name] = namespace
    finally:
        (
            builder.LoadDatabase,
            softioc.iocInit,
            softioc.interactive_ioc,
            asyncio_dispatcher.AsyncioDispatcher,
        ) = saved

    return instances


def tickList(instances):
    """Everything to step, in the order it has to be stepped in.

    Each instance's own order is what matters and is preserved - its layout
    first, then devices, then groups, then spaces - and one instance never
    reads another, so the lists simply follow one another.
    """
    return [
        device
        for namespace in instances.values()
        for device in namespace["simulatedDevices"]
    ]


def startInstances(paths, period=None, interactive=True, queueSize=None, log=None):
    """Build every instance given, then serve them all as one IOC.  Blocks.

    Interactive drops into the IOC shell, where the instances are in scope and
    a leak can be sprung by hand.  Non-interactive just serves, which is what
    a container wants and what stops the process exiting the moment it is up -
    an IOC shell reads its stdin, and a container that is not attached to has
    none.
    """
    period = SIMULATION_PERIOD if period is None else period
    report = log or (lambda message: print(message, flush=True))

    instances = loadInstances(paths)
    devices = tickList(instances)

    # Before LoadDatabase, which consumes the recordset.
    records = epicsdbbuilder.CountRecords()
    report(
        f"{len(instances)} instance(s), {records} records, {len(devices)} devices: "
        + ", ".join(instances)
    )

    builder.LoadDatabase()
    # Has to be before iocInit, which is where EPICS creates the ring.
    callbackSetQueueSize(queueSize or callbackQueueSize(records))

    dispatcher = asyncio_dispatcher.AsyncioDispatcher()
    softioc.iocInit(dispatcher)

    async def simulate():
        # No arguments: the dispatcher hands extra ones to the coroutine
        # differently depending on the pythonSoftIOC version, and passing none
        # at all behaves the same on both.
        reported = set()
        while True:
            await asyncio.sleep(period)
            for device in devices:
                try:
                    device.tick(period)
                except Exception:
                    # A dispatched coroutine that raises is dropped without a
                    # word, which leaves the IOC up but every readback frozen.
                    # Say so, once per device, and keep the others going.
                    if device.prefix not in reported:
                        reported.add(device.prefix)
                        logging.exception("Simulation failed for %s", device.prefix)

    dispatcher(simulate)

    if interactive:
        softioc.interactive_ioc({"instances": instances, "devices": devices})
    else:
        softioc.non_interactive_ioc()
