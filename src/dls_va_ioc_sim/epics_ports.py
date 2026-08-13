"""The ports a simulation serves on, and the one place they are decided.

Channel Access is served on a non-standard port for the same reason the cell
number in every device name is rewritten: a client looking for the real machine
must never find this instead.  Everything that starts an IOC settles the ports
first - a generated instance in its own header, `start` and `run` through
`setPortDefaults` here - so there is no path left that serves a simulation on
the real machine's port by forgetting a step.

pvAccess needed the same treatment and did not have it.  pythonSoftIOC 4.6 and
later start a PVXS/QSRV2 server beside the Channel Access one, serving the same
records under the same names, and it has never heard of `EPICS_CA_*`.  A
simulation whose Channel Access was safely on 6064 still answered
`pvget SR06A-VA-IONP-01:P` on the standard pvAccess port - and under
`hostNetwork` that is the accelerator network.

So there are two layers between a simulation and a client looking for the real
machine, and both were measured before being relied on:

    enable_pva=False    passed to `softioc.iocInit`, which is what stops the
                        pvAccess server existing at all - no listener anywhere
    the ports below     for if it is ever switched back on, so that it comes
                        up somewhere harmless rather than on 5075

The environment still wins over both, so a second simulation runs beside the
first without editing anything.
"""

import os

# The real machine's ports plus 1000, so that the arithmetic is obvious from
# either end and no simulation port is one typo away from a real one.
CA_SERVER_PORT = 6064  # EPICS default 5064
CA_REPEATER_PORT = 6065  # EPICS default 5065, and where CA beacons go
PVA_SERVER_PORT = 6075  # EPICS default 5075
PVA_BROADCAST_PORT = 6076  # EPICS default 5076

PORTS = {
    "EPICS_CA_SERVER_PORT": CA_SERVER_PORT,
    "EPICS_CA_REPEATER_PORT": CA_REPEATER_PORT,
    "EPICS_PVAS_SERVER_PORT": PVA_SERVER_PORT,
    "EPICS_PVAS_BROADCAST_PORT": PVA_BROADCAST_PORT,
}


def setPortDefaults():
    """Put the simulation's ports in the environment, unless they are set.

    EPICS reads these when it creates its servers, which is `iocInit` and not
    the import of softioc, so calling this from inside a function that the
    import has already run above is early enough.  That is what `start` does:
    `start_ioc` imports softioc at module scope and only then execs the
    instances, whose own headers set the same variables.

    setdefault, so the environment still wins and a second simulation can run
    beside the first:

        EPICS_CA_SERVER_PORT=6066 EPICS_CA_REPEATER_PORT=6067 \\
            dls-va-ioc-sim start sr99c-va-ioc-01.py
    """
    for name, port in PORTS.items():
        os.environ.setdefault(name, str(port))
