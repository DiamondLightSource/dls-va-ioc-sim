"""The physics, which is the part with no EPICS in it at all.

vacuum_model publishes nothing and imports no softioc, so these run as plain
arithmetic.  What is worth pinning down is the behaviour the rest of the
framework leans on: where a volume settles, that opening a valve merges the
volumes either side of it, and that joining conserves P*V rather than
averaging pressures.
"""

import pytest

from dls_va_ioc_sim.vacuum_model import gate, vacuumLayout, vacuumVolume


class fakePump:
    """Enough of an ionPumpRecord for a volume to pump with."""

    def __init__(self, prefix, size, running=True):
        self.prefix = prefix
        self.size = size
        self.running = running
        self.volume = None

    def pumpingSpeed(self):
        return self.size if self.running else 0.0

    @property
    def sizePV(self):
        return self

    def get(self):
        return self.size


class fakeValve:
    def __init__(self, prefix, open_=False):
        self.prefix = prefix
        self.open_ = open_
        self.volume = None

    def isOpen(self):
        return self.open_


def settle(layout, seconds=10000.0, step=1.0):
    """Run the layout until nothing is moving any more."""
    for _ in range(int(seconds / step)):
        layout.tick(step)


def test_volume_settles_at_gas_load_over_speed():
    # 1.2e-6 mbar l/s against 1000 l/s is 1.2e-9 mbar, and no amount of
    # waiting moves it further - the equilibrium is the two constants.
    pump = fakePump("P-01", 1000.0)
    volume = vacuumVolume("v", litres=60.0, gasLoad=1.2e-6, pumps=["P-01"])
    layout = vacuumLayout(volume)
    layout.attach([pump])

    settle(layout)
    assert volume.pressure == pytest.approx(1.2e-9, rel=0.01)


def test_stopping_half_the_pumps_doubles_the_pressure():
    pumps = [fakePump("P-01", 500.0), fakePump("P-02", 500.0)]
    volume = vacuumVolume("v", litres=60.0, gasLoad=1.2e-6, pumps=["P-01", "P-02"])
    layout = vacuumLayout(volume)
    layout.attach(pumps)

    settle(layout)
    both = volume.pressure

    pumps[1].running = False
    settle(layout)
    assert volume.pressure == pytest.approx(2 * both, rel=0.01)


def test_base_pressure_is_a_floor():
    # A huge pumping speed cannot take a volume below its surfaces' ultimate.
    pump = fakePump("P-01", 1.0e6)
    volume = vacuumVolume(
        "v", litres=10.0, gasLoad=1.0e-7, basePressure=3e-10, pumps=["P-01"]
    )
    layout = vacuumLayout(volume)
    layout.attach([pump])

    settle(layout)
    assert volume.pressure == pytest.approx(3e-10, rel=0.01)


def test_an_unpumped_volume_creeps_to_its_vent_pressure():
    volume = vacuumVolume("dead leg", litres=10.0, gasLoad=1.0e-7, ventPressure=1.0e-6)
    layout = vacuumLayout(volume)
    layout.attach([])

    settle(layout)
    assert volume.pressure == pytest.approx(1.0e-6, rel=0.01)


def test_a_closed_valve_keeps_two_volumes_apart():
    pump = fakePump("P-01", 1000.0)
    valve = fakeValve("V-01", open_=False)
    good = vacuumVolume("good", litres=10.0, gasLoad=1.0e-6, pumps=["P-01"])
    bad = vacuumVolume("bad", litres=10.0, gasLoad=1.0e-4)

    layout = vacuumLayout(good, gate("V-01"), bad)
    layout.attach([pump, valve])

    settle(layout)
    assert good.pressure < 1e-8
    assert bad.pressure > 1e-7
    assert len(layout.groups()) == 2


def test_opening_a_valve_makes_one_volume_of_two():
    pump = fakePump("P-01", 1000.0)
    valve = fakeValve("V-01", open_=False)
    good = vacuumVolume("good", litres=10.0, gasLoad=1.0e-6, pumps=["P-01"])
    bad = vacuumVolume("bad", litres=10.0, gasLoad=1.0e-4)

    layout = vacuumLayout(good, gate("V-01"), bad)
    layout.attach([pump, valve])
    settle(layout)

    valve.open_ = True
    layout.tick(1.0)
    # One tick is enough: gas crosses an open valve far faster than the
    # simulation steps, so the two are at one pressure straight away.
    assert good.pressure == pytest.approx(bad.pressure)
    assert len(layout.groups()) == 1


def test_joining_conserves_pressure_times_capacity():
    # A litre of bad vacuum let into ninety-nine litres of good hardly moves
    # it; the mixed pressure is capacity weighted, not the average of the two.
    valve = fakeValve("V-01", open_=True)
    big = vacuumVolume("big", litres=99.0, gasLoad=1e-12)
    small = vacuumVolume("small", litres=1.0, gasLoad=1e-12)
    big.pressure, small.pressure = 1.0e-9, 1.0e-3

    layout = vacuumLayout(big, gate("V-01"), small)
    layout.attach([valve])
    mixed = layout.groups()[0].mixedPressure()

    assert mixed == pytest.approx((99 * 1.0e-9 + 1 * 1.0e-3) / 100)


def test_a_run_of_open_valves_equalises_end_to_end():
    valves = [fakeValve("V-01", open_=True), fakeValve("V-02", open_=True)]
    volumes = [vacuumVolume(f"v{n}", litres=10.0, gasLoad=1e-9) for n in range(3)]
    layout = vacuumLayout(
        volumes[0], gate("V-01"), volumes[1], gate("V-02"), volumes[2]
    )
    layout.attach(valves)

    assert len(layout.groups()) == 1

    # ...and closing the middle one splits it again, with nothing cached.
    valves[0].open_ = False
    assert sorted(len(g.volumes) for g in layout.groups()) == [1, 2]


def test_an_unpumped_neighbour_slows_the_pump_down():
    """Valving a dead leg onto a pumped volume adds capacity but no speed."""

    def timeToSettle(withDeadLeg):
        pump = fakePump("P-01", 100.0)
        valve = fakeValve("V-01", open_=withDeadLeg)
        pumped = vacuumVolume("pumped", litres=10.0, gasLoad=1.0e-7, pumps=["P-01"])
        dead = vacuumVolume("dead leg", litres=90.0, gasLoad=1.0e-9)
        pumped.pressure = dead.pressure = 1.0e-6
        layout = vacuumLayout(pumped, gate("V-01"), dead)
        layout.attach([pump, valve])

        for tick in range(10000):
            layout.tick(1.0)
            if pumped.pressure < 2.0e-9:
                return tick
        return None

    alone, joined = timeToSettle(False), timeToSettle(True)
    assert alone is not None and joined is not None
    assert joined > alone


def test_a_layout_must_start_and_end_with_a_volume():
    with pytest.raises(ValueError, match="no volume before it"):
        vacuumLayout(gate("V-01"), vacuumVolume("v", litres=1.0, gasLoad=0.0))

    with pytest.raises(ValueError, match="no volume after it"):
        vacuumLayout(vacuumVolume("v", litres=1.0, gasLoad=0.0), gate("V-01"))


def test_attach_refuses_a_name_the_ioc_does_not_build():
    volume = vacuumVolume("v", litres=10.0, gasLoad=1e-7, pumps=["P-99"])
    layout = vacuumLayout(volume)
    with pytest.raises(KeyError, match="P-99"):
        layout.attach([fakePump("P-01", 100.0)])
