"""The ports every simulation serves on.

One rule, and these are the checks that no path can be written that breaks it:
a client looking for the real storage ring must never find a simulation
instead. Channel Access was moved and pvAccess was not, which is how an
instance safely serving `SR06A-VA-IONP-01:P` on 6064 was still answering
`pvget` for it on the standard pvAccess port.
"""

import os

import pytest

from dls_va_ioc_sim.epics_ports import PORTS, setPortDefaults

# What EPICS uses when nobody says otherwise. None of these may survive
# setPortDefaults, whichever protocol it belongs to.
REAL_MACHINE = {
    "EPICS_CA_SERVER_PORT": "5064",
    "EPICS_CA_REPEATER_PORT": "5065",
    "EPICS_PVAS_SERVER_PORT": "5075",
    "EPICS_PVAS_BROADCAST_PORT": "5076",
}


@pytest.fixture(autouse=True)
def noPortsSet(monkeypatch):
    """No EPICS ports going in, and none left in the environment coming out."""
    for name in PORTS:
        monkeypatch.delenv(name, raising=False)


def test_both_protocols_are_covered():
    """Serving one protocol on a safe port and the other on the real one is
    the bug this module exists for, so the set is asserted rather than the
    individual names."""
    assert set(PORTS) == set(REAL_MACHINE)


def test_no_simulation_port_is_a_real_machine_port():
    setPortDefaults()

    for name, real in REAL_MACHINE.items():
        assert os.environ[name] != real, f"{name} is the real machine's port"


def test_every_port_is_actually_set():
    setPortDefaults()

    for name in PORTS:
        assert os.environ[name].isdigit()


def test_the_environment_still_wins():
    """Two simulations run side by side by starting the second on other ports,
    and nothing has to be edited for that."""
    os.environ["EPICS_CA_SERVER_PORT"] = "6066"

    setPortDefaults()

    assert os.environ["EPICS_CA_SERVER_PORT"] == "6066"
    # The ones that were not overridden are still settled.
    assert os.environ["EPICS_CA_REPEATER_PORT"] == str(PORTS["EPICS_CA_REPEATER_PORT"])
