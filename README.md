[![CI](https://github.com/DiamondLightSource/dls-va-ioc-sim/actions/workflows/ci.yml/badge.svg)](https://github.com/DiamondLightSource/dls-va-ioc-sim/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/DiamondLightSource/dls-va-ioc-sim/branch/main/graph/badge.svg)](https://codecov.io/gh/DiamondLightSource/dls-va-ioc-sim)
[![PyPI](https://img.shields.io/pypi/v/dls-va-ioc-sim.svg)](https://pypi.org/project/dls-va-ioc-sim)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)

# dls_va_ioc_sim

Simulate a DLS vacuum IOC, and the PLC behind it.

Everything a PLC would decide — valve interlocking, combining a gauge pair, the
pressure itself — is decided here instead, and served over Channel Access so
that a screen, an archiver or another IOC cannot tell it apart from the real
thing. The PV interface mirrors the DLS support-module templates device for
device.

An instance is **generated from the builder XML the real IOC is built from**,
so simulating a cell is a one-liner rather than a writing job.

What            | Where
:---:           | :---:
Source          | <https://github.com/DiamondLightSource/dls-va-ioc-sim>
PyPI            | `pip install dls-va-ioc-sim`
Docker          | `docker run ghcr.io/diamondlightsource/dls-va-ioc-sim:latest`
Releases        | <https://github.com/DiamondLightSource/dls-va-ioc-sim/releases>

## Generating a simulation

```console
$ dls-va-ioc-sim generate .../SR-BUILDER/etc/makeIocs/SR03C-VA-IOC-01.xml 99
wrote sr99c-va-ioc-01.py
```

One file, named after the IOC in lower case, and it is the IOC and its launcher
both. The `99` rewrites the cell number in every device name, so `SR03C` comes
up as `SR99C` and the simulation cannot be taken for the real machine — as does
the non-standard Channel Access port it serves on, which is set at the top of
the file itself.

```console
$ ./sr99c-va-ioc-01.py
```

A [PEP 723](https://peps.python.org/pep-0723/) header names the version of this
package that wrote it and a `uv run --script` shebang starts it, so an instance
runs wherever [uv](https://docs.astral.sh/uv/) does with nothing installed
first, and goes on running when the framework moves on. In an environment that
already has the package, `python sr99c-va-ioc-01.py` is the same thing.

Two simulations at once want two ports, and the environment still wins:

```console
$ EPICS_CA_SERVER_PORT=6066 EPICS_CA_REPEATER_PORT=6067 ./sr99c-va-ioc-01.py
```

Then drive it over Channel Access. Stopping pumps makes the pressure rise,
starting them brings it back, and opening a valve equalises two volumes:

```console
$ caput SR99A-VA-GIONP-01:STOP 1        # a whole group of supplies
$ camonitor SR99A-VA-GAUGE-01:P         # watch it come up
```

To look at a cell without committing to a file, `run` builds the same devices
straight from the XML:

```console
$ dls-va-ioc-sim run .../SR03C-VA-IOC-01.xml 99
```

## The one thing the XML cannot say

A builder XML says which devices exist and which groups they are in. It says
nothing about the **beam pipe** — which valve stands between which two lengths
of it, how many litres a section holds, what it outgasses. On the real machine
that comes off a P&ID.

So the generated file carries a vacuum layout marked `*** EDIT ME ***`: one
volume per domain, joined by nothing, with capacities and gas loads guessed from
the installed pumping speed. It runs out of the box and it is not the beam pipe.
Making it the machine means splitting a domain where a valve really divides it
and putting a `gate()` between the halves:

```python
vacuum = vacuumLayout(
    straight,
    gate("SR99A-VA-VALVE-01"),
    arc,
)
```

A gate's neighbours are simply the entries either side of it, so there is no
second place for the topology to disagree with itself.

**Generate, then edit.** The generator refuses to overwrite an existing pair
without `--force`, because the layout in the one you have is the only part that
took any thought.

## Three layers

The thing to hold onto is that **"space" and "volume" are different concepts
that used to share a name**:

- A **volume** is a length of beam pipe: it holds gas, it has a capacity in
  litres, and pumps and gauges sit on it. It publishes nothing — no real IOC
  has a record for how many litres a section holds.
- A **group** (`GIONP-nn`, `GVALV-nn`, …) is a device standing for several
  others. Writing to `GIONP-01:START` starts every pump underneath it, and a
  group's members may themselves be groups.
- A **space** (`SPACE-nn`) is what an operator is shown: a pressure, a status
  lamp and controls. It owns nothing — every record either reads a group or
  writes to one.

Pressure belongs to a volume, not to a device. A group of volumes settles where
its total gas load balances its total pumping speed, so the interesting
behaviour is emergent rather than scripted: opening a valve onto a vented
section spoils the good side and trips its ion pumps on high pressure, and a
tripped pump stays latched until its volume is back below its setpoint.

## Verifying a change

Build the database and diff it against the version before your change:

```console
$ dls-va-ioc-sim dbdump sr99c-va-ioc-01.py before.db
$ # ...make the change...
$ dls-va-ioc-sim dbdump sr99c-va-ioc-01.py after.db
$ diff before.db after.db
```

For a change to how an instance is *assembled*, expect that diff to be **empty**
— byte identical, not shape identical. For a change to a device class it is
meant to be non-empty, and is then the record-by-record statement of what you
changed. That is a far stronger check than running the IOC and a far cheaper
one. Diff before you run.
