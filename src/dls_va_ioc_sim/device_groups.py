"""What every group of devices has in common.

A group is a device that stands for several others: SR21A-VA-GIONP-01 is the
eight ion pumps of the arc, and writing to its :START starts all of them.  The
real templates do this with `sel` records fanning readbacks in and `seq`
records fanning demands out, eight slots at a time, padding the unused ones by
repeating a member.  Here the aggregation is done in Python over however many
members there actually are, and the group publishes the same records the
template does.

**The one exception is the `sel` that names the members**, which is built as a
real database record with real links - `:SELSTA` on the gauge groups, `:MAXSTA`
and `:MINSTA` on the ion pump and valve groups.  Its value is then worked out
twice, which would be a reason to skip it like the rest, except that its link
fields are the only place a real IOC records which devices a group covers.  See
selectOverMembers.

The one thing to get right is that **a group's members may themselves be
groups**.  SR21C-VA-GIONP-01 in SR21C-VA-IOC-01.xml has SR21A-VA-GIONP-01 and
SR21S-VA-GIONP-01 as its members, and a whole straight is started by writing to
one PV.  That works here because a group publishes records under the same
attribute names its members use - a group's status is `statusPV` just as a
pump's is - so nothing that reads a member has to care which it has got.

The same goes for control.  Fanning a demand out calls a *method* on each
member rather than writing to its record, and each member's method recurses
into its own members' - which is what makes starting a group of groups reach
the pumps at the bottom.

Those methods do still write the member's demand record, because the real
templates fan out with CA links and a pump started by its group shows Start on
its own :START as well.  Doing that needs care: **on a running IOC `.set()` on
an output record fires that record's `on_update`**, which is not what
`interactive_ioc` or an unstarted IOC will show you.  A callback that writes
its own record therefore calls itself again, and with `always_update=True` it
never stops - one external caput was measured driving 88523 callbacks.  Take
demands through acceptDemand below, which writes the record and then throws
that write's own callback away.

The `delay` a real group staggers its members by is accepted and recorded but
not slept through: the templates spread the inrush current of eight supplies
over a few seconds, and the dispatcher this IOC runs on is single threaded, so
sleeping in a callback would stall every other device.  The :STARTING,
:OPENING and :SWITCHING flags a screen watches during the stagger are still
published, they just go up and down within the one call.
"""

from collections import deque
from typing import Any
from weakref import WeakKeyDictionary

from softioc import builder, device

# Plain database records, for the one thing a group cannot compute in Python -
# see selectOverMembers.  epicsdbbuilder publishes one factory per record type
# with setattr as the dbd is loaded, so a type checker sees an empty class; the
# annotation is the whole of the suppression for that, as in
# vacuum_space_records.
records: Any = builder.records

# A group template has eight slots whether or not it has eight members.
MEMBER_SLOTS = "ABCDEFGH"

# What a `sel` over a group's members scans at.  The templates give these
# records no SCAN of their own - each is processed by a PP link from the record
# above it, :STA's INP on the gauge groups and :CALSTA's on the other two, and
# both of those are computed in Python here - so nothing would ever process
# them.  This is the rate the templates poll the record above at.  It is the
# same deviation, for the same reason, as :CALSTA on a vacuum space.
SCAN_RATE = "1 second"


def readings(members, field):
    """Every member's value for a field, for the sel records to pick from."""
    return [getattr(member, field).get() for member in members]


def highest(members, field):
    """SELM "High Signal" - the worst pressure, the furthest on status."""
    return max(readings(members, field))


def lowest(members, field):
    """SELM "Low Signal" - the least ready member of the group."""
    return min(readings(members, field))


def median(members, field):
    """SELM "Median Signal", as the ion pump group takes its calibration."""
    values = sorted(readings(members, field))
    return values[len(values) // 2]


def memberLinks(members, read="STA", modifier="MS"):
    """INPA..INPH naming every member, padded the way the builder XML pads.

    The XML fills all eight slots whatever the group holds, by repeating the
    last real member:

        ionp1="SR03S-VA-IONP-01" ionp2="SR03S-VA-IONP-03"
        ionp3..8="SR03S-VA-IONP-03"

    That padding is copied here rather than leaving the spare slots empty, and
    it is not cosmetic: an unset link is a constant reading 0, which a "Low
    Signal" sel would then pick every time and a group of two open valves would
    read Fault for ever.  It does mean anything reading these back has to fold
    out the duplicates - which is exactly what it has to do on the real IOC.
    """
    if len(members) > len(MEMBER_SLOTS):
        raise ValueError(f"a group template has {len(MEMBER_SLOTS)} slots, "
                         f"and this group has {len(members)} members")

    links = [f"{member.prefix}:{read}" for member in members]
    if modifier:
        links = [f"{link} {modifier}" for link in links]
    links += links[-1:] * (len(MEMBER_SLOTS) - len(links))
    return {f"INP{slot}": link
            for slot, link in zip(MEMBER_SLOTS, links, strict=True)}


def selectOverMembers(name, members, selm="High Signal", read="STA",
                      modifier="MS", **fields):
    """The template's `sel` over the members, built as the real record it is.

    A group aggregates in Python, so the *value* of one of these is worked out
    twice - here by the database and again in publish() - and on its own that
    would be a reason not to build it at all.  It is built for its link fields:
    **they are the only place a real IOC writes down which devices a group
    covers.** There is no record holding the member names, so a screen that
    lists a group's members off a real cell has to read them out of
    `GIONP-01:MAXSTA.INPA` and its neighbours, and has to get the same answer
    here.  Same bargain as a vacuum space's links; see vacuum_space_records.

    `read` and `modifier` are what the template puts on the far end of each
    link.  They vary: a Pirani group's SELSTA reads `$(pirg1):STA` with an
    empty modifier where every other one of these carries MS.
    """
    return records.sel(name, SELM=selm, SCAN=SCAN_RATE,
                       **memberLinks(members, read, modifier), **fields)


# Every demand this simulation has written to an output record and not yet seen
# come back, per record, in the order it was written.  See acceptDemand.
_echoes = WeakKeyDictionary()


def expectsEcho():
    """True once writing an output record calls that record's callback back.

    `iocInit` is what sets softioc's dispatcher, and it is the dispatcher that
    turns a record processing into an `on_update` call.  Before that - a unit
    test, or `dbdump` building the database - `.set()` only stores the value
    and nothing comes back, so there is no echo to wait for.  Expecting one
    that never arrives would leave it sat in front of a real demand.
    """
    return device.dispatcher is not None


def acceptDemand(record, value):
    """Show a demand on its own output record - False if it is our own echo.

    A group fanning a demand out wants the member's demand record to show what
    was asked for, the way the template's CA links leave it.  But .set() on an
    output record fires its on_update, so the callback that wrote the record is
    called again one dispatch later, carrying the value it wrote.

    Writing only on a change is not enough to settle that.  It settles *one*
    demand: the echo finds its own value already on the record, writes nothing,
    and stops.  Two demands in flight never settle.  Each echo finds the other
    demand's value on the record, so each one writes, and each write makes
    another echo, and the two feed each other for ever - a valve seen opening
    and closing once a second until the IOC was killed.  Two demands in flight
    is not exotic: a space opening every valve underneath it is seconds of work
    here, because each valve sleeps through its OPEN_DELAY, and anything an
    operator does in that time lands in the middle of it.

    So an echo is not a demand, and must do nothing at all.  Each write is
    remembered here in the order it was made, and the callback it causes is
    matched against what is expected, dropped, and answered False.  Anything
    else is a demand - an operator's caput, or a group passing one down -
    whatever value it carries and however many are already in flight, and is
    answered in the order it arrived.

    Every caller is the `on_update` of the record it passes, so every one of
    them uses the answer the same way:

        def setCon(self, value):
            if not acceptDemand(self.conPV, value):
                return
            ...

    Matching the value and not just counting the echoes is what keeps two
    demands in order.  A group told to close and then open writes 1 and then 0,
    and both echoes come back behind them; matching on value drops each echo
    against the write it belongs to, and the record ends up Open.  Counting
    would drop the first callback it saw - which is the operator's second
    demand, not an echo at all.
    """
    expected = _echoes.get(record)
    if expected and expected[0] == value:
        expected.popleft()
        return False

    if record.get() != value:
        if expectsEcho():
            _echoes.setdefault(record, deque()).append(value)
        record.set(value)
    return True


class deviceGroup:
    """Members, a name, and the tick that republishes what they add up to.

    Subclasses build their records in __init__ and recompute them in publish().
    The group is ticked like any other simulated device, and has to be ticked
    *after* its members so that what it publishes is this pass's readings
    rather than last pass's - and a group of groups after the groups below it.
    orderedGroups() puts a list into that order.
    """

    def __init__(self, prefix, members, delay=0.0):
        if not members:
            raise ValueError(f"{prefix} has no members")
        self.prefix = prefix
        self.members = list(members)
        self.delay = delay
        builder.SetDeviceName(prefix)

    def __repr__(self):
        return (f"<{type(self).__name__} {self.prefix} "
                f"of {len(self.members)}>")

    def depth(self):
        """How many groups deep this one goes, counting itself as one."""
        return 1 + max((member.depth() for member in self.members
                        if isinstance(member, deviceGroup)), default=0)

    def tick(self, delta):
        self.publish()

    def publish(self):
        raise NotImplementedError


def orderedGroups(groups):
    """Groups sorted so that a group comes after every group inside it.

    A group reads its members' records, so it has to run after them.  Sorting
    on depth is enough: a group is always deeper than anything it contains.
    """
    return sorted(groups, key=lambda group: group.depth())
