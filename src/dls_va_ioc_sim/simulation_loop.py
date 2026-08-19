"""Start an IOC and step its devices, for as long as it runs.

Every simulation ends the same way: load the database, start the IOC without
pvAccess, and step a list of devices on a fixed period until it is killed.
That was written out three times - inside `generate_ioc`'s FOOTER as a string,
again as `parsedIoc.run`, and again as `startInstances` - and all three had to
be kept in step by hand.  A fix to how a failing device is reported had to be
made in each, and the copy inside the %-format template had to have every
literal `%` doubled: the one that was not took the device name out of the
message at exactly the moment a device had failed.

There is one copy now, which matters most for `enable_pva=False`.  That is the
one line standing between a simulated storage ring and the standard pvAccess
port, no `EPICS_CA_*` setting moves it, and it was three lines that each had to
remember it.

A generated instance calls `runSimulation` with the devices it assembled and its
own `globals()`, which is what puts the volumes in scope in the interactive
shell.  `start` has one thing to do between loading the database and starting
the IOC - size the callback queue for the number of records it merged - and
passes it as `beforeIocInit` rather than keeping a fourth copy of the rest.
"""

import asyncio
import logging

from softioc import asyncio_dispatcher, builder, softioc

from .epics_ports import setPortDefaults

# How often the simulated devices recalculate their readbacks.  One second
# matches the SCAN rate the real templates poll their hardware at.
SIMULATION_PERIOD = 1.0


async def stepForever(devices, period):
    """Step every device on a fixed period, reporting the first failure of each.

    A coroutine the dispatcher runs that raises is dropped without a word,
    which leaves the IOC up and every readback frozen - the worst failure this
    can have, because nothing about it looks wrong.  So a device that raises is
    logged, once, and the others carry on.
    """
    reported = set()
    while True:
        await asyncio.sleep(period)
        for device in devices:
            try:
                device.tick(period)
            except Exception:
                if device.prefix not in reported:
                    reported.add(device.prefix)
                    logging.exception("Simulation failed for %s", device.prefix)


def runSimulation(
    devices,
    period=SIMULATION_PERIOD,
    interactive=True,
    namespace=None,
    beforeIocInit=None,
):
    """Load the database, start the IOC and step `devices` forever.

    Blocks either way.  Interactive drops into the IOC shell with `namespace`
    in scope, which is where a leak is sprung by hand; non-interactive just
    serves, which is what a container wants and what stops the process exiting
    the moment it has come up.

    `beforeIocInit` is called between loading the database and starting the
    IOC, which is the only place some things can go: `start` sizes the callback
    ring there, because iocInit is where EPICS creates it.
    """
    # A generated instance has already settled these in its own header, before
    # softioc was imported.  This is for every other way in, and it is
    # setdefault, so saying it twice cannot move a port that is already set.
    setPortDefaults()

    builder.LoadDatabase()
    if beforeIocInit is not None:
        beforeIocInit()
    dispatcher = asyncio_dispatcher.AsyncioDispatcher()
    # enable_pva=False - see epics_ports.  A PVXS server would otherwise serve
    # these same records on the standard pvAccess port, where a client looking
    # for the real machine would find them whatever EPICS_CA_SERVER_PORT says.
    softioc.iocInit(dispatcher, enable_pva=False)

    # A coroutine *function* taking no arguments, which is what the dispatcher
    # wants: it inspects what it is given, so a lambda returning a coroutine is
    # not the same thing, and the versions differ over what extra arguments
    # they would pass.  Taking none at all behaves the same on all of them.
    async def simulate():
        await stepForever(devices, period)

    dispatcher(simulate)

    if interactive:
        softioc.interactive_ioc(namespace or {})
    else:
        softioc.non_interactive_ioc()
