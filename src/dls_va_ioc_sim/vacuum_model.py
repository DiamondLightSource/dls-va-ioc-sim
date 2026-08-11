"""The vacuum physics: volumes of gas, and the valves that join them up.

Nothing here publishes a PV.  This module is the simulation's own model of the
beam pipe - how much gas is in each length of it, what is pumping on it, and
which lengths are currently open to each other - and none of that exists on the
real machine, so none of it is served over Channel Access.  What a client sees
is what the gauges and ion pumps sat on a volume report, exactly as on the real
front end.  Keep it that way: no `import softioc` belongs in this file.

    vacuumVolume    -- one length of pipe, its gas load, and the devices on it
    volumeGroup     -- the volumes an open valve or two have joined together
    vacuumLayout    -- the running order of the pipe; works the groups out

A volume owns the pressure and the devices on it read it.  A gauge publishes
what its sensors make of the volume, and an ion pump both reads the volume and
pumps on it, which is where the interesting behaviour comes from: switch pumps
off and the pressure the gauges report rises, switch them back on and it falls
again, faster the more of them are running.

A valve between two volumes makes that more interesting still.  Closed, it
keeps them apart and each looks after itself.  Open, the two are one volume:
the gas mixes, and from then on the pair pump down together on their combined
gas load and combined pumping speed.  That composes along a run of pipe without
anything having to know the topology in advance - open both valves of

    volume -> VALVE-01 -> volume -> VALVE-02 -> volume

and all three equalise, because the layout re-derives the groups from the valve
positions on every tick.  Nothing is cached, so a valve closing splits a group
again just as readily.

The layout is written out in beam order and devices are named rather than
passed in, so it can be written next to a builder XML that says which devices
exist but nothing about which of them share a length of pipe:

    LAYOUT = vacuumLayout(
        vacuumVolume("upstream", litres=30.0, gasLoad=5.0e-7,
                     pumps=["FE99B-VA-IONP-01", "FE99B-VA-IONP-02"],
                     gauges=["FE99B-VA-GAUGE-01"]),
        gate("FE99B-VA-VALVE-01"),
        vacuumVolume("absorber vessel", ...),
    )

    LAYOUT.attach(everyDeviceTheIocBuilt)

`attach` is what turns those names into objects, and complains about any it
cannot find - which is the check that the layout and the IOC still agree.

To move a volume by hand from the IOC's interactive shell, either raise its
gasLoad (a leak, so the pumps hold it higher) or set forcedPressure, which pins
it and drags its whole group with it:

    >>> upstream.gasLoad = 1.0e-4
    >>> upstream.forcedPressure = 1.0e-3
    >>> upstream.forcedPressure = None      # back to the model
"""

from typing import Any

from .vacuum_sim import clampPressure, slideLog


class vacuumVolume:
    """One length of pipe, its gas load, and whatever is pumping on it.

    A volume on its own settles where its gas load balances its pumping speed -
    gas leaks and outgasses in at some rate, the pumps take it away at a speed
    that depends on how many of them are running, and the pressure ends up at
    the one divided by the other.  Turn half the pumps off and the volume
    settles twice as high, and gets there twice as slowly.

    Both of those are bounded.  basePressure is the floor: no amount of pumping
    speed will take a volume below the ultimate pressure of its surfaces.  With
    nothing pumping at all the volume is not heading for a balance point, so it
    creeps up towards ventPressure on its own time constant instead.

    The arithmetic itself lives in volumeGroup, not here, because a volume that
    has been valved onto its neighbour no longer has a pressure of its own to
    work out.  A volume that stands alone is simply a group of one.

    Pumps and gauges are named rather than passed in; vacuumLayout.attach turns
    the names into the objects.
    """

    def __init__(self, name, litres, gasLoad, basePressure=2.0e-10,
                 ventPressure=1.0e-6, timeConstant=20.0,
                 ventTimeConstant=120.0, pumps=(), gauges=()):
        self.name = name
        self.litres = litres
        self.gasLoad = gasLoad
        self.basePressure = basePressure
        self.ventPressure = ventPressure
        self.timeConstant = timeConstant
        self.ventTimeConstant = ventTimeConstant

        # Pin the volume here instead of letting the model decide, for driving
        # an interlock from the interactive shell.  None means the model.
        self.forcedPressure = None

        self.pressure = clampPressure(basePressure)

        self.pumpNames = list(pumps)
        self.gaugeNames = list(gauges)
        self.pumps = []
        self.gauges = []

    def __repr__(self):
        return f"<vacuumVolume {self.name} {self.pressure:.2e} mbar>"

    # -- what this volume brings to its group --------------------------------

    def pumpingSpeed(self):
        """Speed this volume's own pumps are contributing now, l/s."""
        return sum(pump.pumpingSpeed() for pump in self.pumps)

    def installedSpeed(self):
        """Speed this volume would see with every one of its pumps running."""
        return sum(pump.sizePV.get() for pump in self.pumps)

    def pumpsRunning(self):
        return sum(1 for pump in self.pumps if pump.pumpingSpeed() > 0.0)

    def pumpdownFactor(self):
        """How much slower this volume pumps down than it clears its own gas.

        Emptying a volume of the gas in it takes about volume over speed, which
        for a front end is a fraction of a second; a real pump down takes far
        longer than that, because the surfaces go on giving gas up long after
        the volume itself is empty.  timeConstant is the time constant that
        actually results, with all of this volume's own pumps running, so this
        ratio is the factor between the two - a few hundred, for UHV.

        Keeping it as a ratio rather than as the time constant itself is what
        lets a group work out a time constant of its own: a volume with no
        pumps adds capacity without adding speed, and correctly slows its
        neighbours down when it is valved into them.  A volume with no pumps at
        all has no pump down of its own to quote, and says so with a zero.
        """
        installed = self.installedSpeed()
        if installed <= 0.0:
            return 0.0
        return self.timeConstant * installed / self.litres


class volumeGroup:
    """The volumes that are all at one pressure, because valves join them.

    Most of the time a group is a single volume with its valves shut.  Open a
    valve and the two volumes either side of it become one group, which behaves
    as the one volume it physically now is: the gas already in each mixes, and
    the combined capacity is pumped by everything on any part of it, against
    the gas load of all of it.

    Where the members disagree about a limit, the group takes the worst of
    them.  A dirty section valved onto a clean one holds the whole group up: it
    cannot be cleaner than its dirtiest part.
    """

    def __init__(self, volumes):
        self.volumes = volumes

    # -- what the group adds up to -------------------------------------------

    def litres(self):
        return sum(volume.litres for volume in self.volumes)

    def gasLoad(self):
        return sum(volume.gasLoad for volume in self.volumes)

    def pumpingSpeed(self):
        return sum(volume.pumpingSpeed() for volume in self.volumes)

    def weighted(self, value):
        """A member property averaged over the group, weighted by capacity."""
        return volumeWeighted(self.volumes, value)

    # -- where it is heading and how fast ------------------------------------

    def target(self, speed):
        """The pressure the group is heading for at this pumping speed."""
        if speed <= 0.0:
            return max(volume.ventPressure for volume in self.volumes)
        return max(self.gasLoad() / speed,
                   max(volume.basePressure for volume in self.volumes))

    def timeConstant(self, speed):
        """How long the group takes to get there at this pumping speed.

        Capacity over speed for the group as a whole, scaled by how much slower
        than that its surfaces make it.  Volumes with no pumps of their own
        have no scaling to quote, so the scaling comes from the ones that do -
        but their capacity still counts in the total, which is what makes
        valving a dead leg into a pumped volume slow the pump down.
        """
        if speed <= 0.0:
            return self.weighted(lambda volume: volume.ventTimeConstant)

        pumped = [volume for volume in self.volumes
                  if volume.installedSpeed() > 0.0]
        factor = volumeWeighted(pumped, lambda volume: volume.pumpdownFactor())
        return factor * self.litres() / speed

    def forcedPressure(self):
        """The pressure a member is holding the group at by hand, or None.

        Forcing any volume of a group pins the lot, because they are one
        volume; if two members are forced to different pressures the higher
        wins, on the grounds that gas has to go somewhere.
        """
        forced = [volume.forcedPressure for volume in self.volumes
                  if volume.forcedPressure is not None]
        return max(forced) if forced else None

    # -- simulation ----------------------------------------------------------

    def mixedPressure(self):
        """The one pressure the group's volumes are at once the gas has spread.

        Gas crosses an open valve far faster than this simulation steps, so
        opening one equalises the volumes either side of it within a tick.
        What they equalise *to* is set by how much gas there was in total: it
        is pressure times capacity that is conserved, so a small vented section
        opened onto a big pumped one hardly moves it, while the other way round
        spoils the good vacuum completely.  A group of one comes out at the
        pressure it was already at.
        """
        return clampPressure(
            sum(volume.pressure * volume.litres for volume in self.volumes)
            / self.litres())

    def advance(self, delta):
        """Mix the group together, then move its pressure on by delta seconds."""
        speed = self.pumpingSpeed()
        forced = self.forcedPressure()

        if forced is not None:
            target = forced
            # Being driven by hand rather than by the pumps, so the time
            # constant is the one the volumes quote, not one worked out from a
            # pumping speed they may not have.
            timeConstant = self.weighted(lambda volume: volume.timeConstant)
        else:
            target = self.target(speed)
            timeConstant = self.timeConstant(speed)

        pressure = clampPressure(
            slideLog(self.mixedPressure(), clampPressure(target), delta,
                     timeConstant))
        for volume in self.volumes:
            volume.pressure = pressure


class gate:
    """A valve in the running order, named rather than passed in.

    Only a marker: it says that whatever valve is called this separates the
    volume before it from the volume after it.  vacuumLayout.attach finds the
    object, and the layout pairs it with its neighbours.
    """

    def __init__(self, name):
        self.name = name
        # Filled in by vacuumLayout: the valve by attach(), the neighbours by
        # the layout's own constructor.  Typed loosely because what guarantees
        # they are set is attachLayout's check, which no type checker can see.
        self.valve: Any = None
        self.before: Any = None
        self.after: Any = None

    def __repr__(self):
        return f"<gate {self.name}>"

    def isOpen(self):
        return self.valve.isOpen()


class vacuumLayout:
    """The beam pipe in running order, and the groups the valves make of it.

    This is the thing the simulation steps, rather than the volumes
    themselves: which volumes share a pressure depends on where the valves
    are, so it has to be settled before any of them can be advanced.  It is
    worked out afresh every tick and nothing is remembered, so valves opening
    and closing need no bookkeeping - the groups simply come out differently
    next time round.

    Entries are given in beam order, alternating volumes and gates, and a
    gate's neighbours are the volumes either side of it in that order.  That
    is what replaces telling each valve which two volumes it separates: there
    is no second place for the topology to be written down and disagree.

    A valve with pipe on only one side of it - a fast valve protecting the
    ring, say - simply has no gate entry.  It is still a valve, it still has
    its records, and closing it isolates nothing because there is nothing on
    the far side to isolate from.  Add a volume beyond it if that has to
    change.
    """

    def __init__(self, *entries):
        self.prefix = "vacuum layout"
        self.volumes = []
        self.gates = []

        pending = []
        previous = None
        for entry in entries:
            if isinstance(entry, gate):
                if previous is None:
                    raise ValueError(
                        f"{entry.name} has no volume before it - a "
                        "layout starts with a volume")
                pending.append(entry)
            elif isinstance(entry, vacuumVolume):
                for waiting in pending:
                    waiting.before = previous
                    waiting.after = entry
                    self.gates.append(waiting)
                pending = []
                previous = entry
                self.volumes.append(entry)
            else:
                raise TypeError(
                    "a layout is made of vacuumVolume and gate entries, not "
                    f"{entry!r}")

        if pending:
            raise ValueError(
                ", ".join(waiting.name for waiting in pending)
                + " has no volume after it - a layout ends with a volume")

    # -- wiring up ------------------------------------------------------------

    def attach(self, devices):
        """Turn the names in the layout into the objects the IOC built.

        Anything named here and not built, or built and named twice, is a
        mistake worth stopping for: the layout is the only statement of which
        devices share a length of pipe, and a name that silently resolves to
        nothing would leave a pump pumping on air.
        """
        known = {}
        for device in devices:
            known.setdefault(device.prefix, device)

        missing = []

        def find(name):
            if name not in known:
                missing.append(name)
                return None
            return known[name]

        for volume in self.volumes:
            volume.pumps = []
            volume.gauges = []
            for name in volume.pumpNames:
                pump = find(name)
                if pump is not None:
                    pump.volume = volume
                    volume.pumps.append(pump)
            for name in volume.gaugeNames:
                gauge = find(name)
                if gauge is not None:
                    gauge.volume = volume
                    volume.gauges.append(gauge)

        for entry in self.gates:
            entry.valve = find(entry.name)

        if missing:
            raise KeyError(
                "the vacuum layout names devices this IOC does not "
                "build: " + ", ".join(sorted(set(missing))))

    # -- simulation -----------------------------------------------------------

    def groups(self):
        """Work out which volumes are currently at a common pressure.

        Every volume starts in a group of its own, and each open valve merges
        the groups either side of it.  Merging rather than pairing is what
        makes a run of volumes equalise end to end when the valves between them
        are all open, without this having to look at the layout as a whole.
        """
        leader = {volume: volume for volume in self.volumes}

        def groupOf(volume):
            while leader[volume] is not volume:
                leader[volume] = leader[leader[volume]]
                volume = leader[volume]
            return volume

        for entry in self.gates:
            if not entry.isOpen():
                continue
            before, after = groupOf(entry.before), groupOf(entry.after)
            if before is not after:
                leader[before] = after

        members = {}
        for volume in self.volumes:
            members.setdefault(groupOf(volume), []).append(volume)
        return [volumeGroup(volumes) for volumes in members.values()]

    def tick(self, delta):
        """Advance every group by delta seconds.

        Groups are stepped before the pumps and gauges on them, so that a gauge
        and an ion pump reading the same volume in the same pass agree with
        each other.
        """
        for group in self.groups():
            group.advance(delta)


def volumeWeighted(volumes, value):
    """A per-volume property averaged over volumes, weighted by capacity.

    Anything that describes what a length of pipe is like, rather than how much
    of it there is, has to be combined this way when volumes are joined, so
    that a big section counts for more than the short one next door.  With no
    volumes to average there is nothing to say, so the answer is 1 - the value
    every such property is a scaling on.
    """
    if not volumes:
        return 1.0
    litres = sum(volume.litres for volume in volumes)
    return sum(value(volume) * volume.litres for volume in volumes) / litres
