# dls-va-ioc-sim — a framework for simulating DLS vacuum IOCs

> Ported from `ioc/feSeqIOC` into this module, which is a
> [python-copier-template](https://github.com/DiamondLightSource/python-copier-template)
> package. The framework is unchanged: the port was verified by building the
> database before and after and diffing it — **byte identical, 2080 records for
> SR03C and 1119 for FE99B**. See *Integrating with the template* at the end for
> the three house-style decisions the move forced.

A **pythonSoftIOC framework** for standing in for a real vacuum IOC and the PLC
behind it. Everything a PLC would decide — valve interlocking, combining a gauge
pair, the pressure itself — is decided here instead, and served over Channel
Access so that a screen, an archiver or another IOC cannot tell it apart from the
real thing.

**The device modules are the deliverable; the files in `examples/` are
instances.** `FE99B-CS-IOC-01.py` is a made-up front end that exists to exercise
the framework and show how an IOC is assembled — it is not the point, and
nothing in the framework knows about it. Keep anything machine-specific out of
the modules.

An instance is **generated** from the builder XML a real IOC is built from,
plus a hand-written vacuum layout — see *Building an instance* below. That was
the direction of travel and it has arrived; all 24 `SR-BUILDER` cell XMLs
generate and run.

The PV interface mirrors the real DLS support-module templates device for device.
When adding or editing a device, the template it imitates is the specification;
each module's docstring carries the class → `*.template` mapping.

```bash
dls-va-ioc-sim generate <xml> <cell>   # the way in: writes one runnable file
dls-va-ioc-sim start <instance>...     # serve one or many as a single IOC
dls-va-ioc-sim run <xml> <cell>        # the same devices, no file, IOC shell
dls-va-ioc-sim dbdump <instance> <db>  # build the records, do not start an IOC
```

## Layout

The framework, in `src/dls_va_ioc_sim/`:

| File | What it is |
|---|---|
| `vacuum_model.py` | The physics: volumes of gas, the valves that join them. **No PVs** |
| `device_groups.py` | What every group of devices has in common |
| `vacuum_space_records.py` | `space.template` — the device an operator is shown |
| `ion_pump_records.py` | Digitel MPC controllers, the ion pumps on them, and groups |
| `gauge_records.py` | MKS 937B controllers, IMG/Pirani gauges, relays, and groups |
| `fe_seq_records.py` | Valves, absorbers, shutters, their interlock chain, and groups |
| `vacuum_sim.py` | Shared helpers: pressure range, log-space slide, noise |
| `builder_xml.py` | Reads a real IOC's builder XML. Plain data, **no records** |
| `generate_ioc.py` | Writes an instance out as Python, from that. The way in |
| `start_ioc.py` | Serves written instances, one or many, as a single IOC |
| `parsed_ioc.py` | Builds the records instead, for an instance that parses at start up |
| `dbdump.py` | Builds an instance's records without starting it |
| `__main__.py` | The CLI over the four of those that are commands |

The instances, in `examples/`:

| File | What it is |
|---|---|
| `FE99B-CS-IOC-01.py` | A made-up front end, written by hand. The short worked example |
| `SR99C-VA-IOC-01.py` | A storage ring cell from `SR03C-VA-IOC-01.xml`, hand-tuned layout |
| `SR99C-VA-IOC-01-fromXml.py` | The same, parsing its XML at start up rather than generated |

One module per device family, classes named after the template they mirror, and
composed the way the `iocbuilder` classes are: a child takes its parent object and
registers itself (`ionPumpRecord(mpc, …)`, `imgRecord(gctlr, …)`), so
controller-wide actions and controller-wide relay numbering work.

Read `examples/FE99B-CS-IOC-01.py` first — it is short, and it is the worked
example of the assembly order, which is the order a builder XML is in: devices,
then the groups over them, then the spaces over the groups, with the vacuum
layout in the middle. `SR99C-VA-IOC-01.py` is the same shape at real-machine
scale, and is what a generated file looks like once its layout has been thought
about.

**Module filenames are snake_case; everything inside them is not.** The classes
are named after the DLS template each mirrors (`ionPumpRecord` for
`digitelMpcIonp`, `spaceRecord` for `space.template`) and composed the way
`iocbuilder`'s own classes are, so ruff's `N801`/`N802`/`N803`/`N806` are
switched off in `pyproject.toml` with the reason written next to them. That
mapping is the specification when a device is edited; it is worth more than the
naming convention.

## The three layers

The thing to hold onto is that **"space" and "volume" are different concepts that
used to share a name**, and separating them is what this design is about.

- A **volume** (`vacuumModel.py`) is a length of beam pipe: it holds gas, it has a
  capacity in litres, and pumps and gauges sit on it. It is pure Python and
  **publishes nothing** — no real front end has a record for how many litres a
  section holds, so nothing here is served.
- A **group** (`GIONP-nn`, `GGAUG-nn`, `GIMG-nn`, `GPIRG-nn`, `GVALV-nn`) is a
  device standing for several others. Writing to `GIONP-01:START` starts every
  pump underneath it.
- A **space** (`SPACE-nn`, `space.template`) is what an operator is shown: a
  pressure, a status lamp, and controls. It owns nothing at all — every record
  either reads a group or writes to one.

So the beam pipe and the operator's view are decided independently. A space
covers whatever its five groups cover, which may be one gauge or a whole
straight; the volumes say only which devices are physically breathing the same
gas. That is exactly the split in a builder XML, where `vacuumSpace.spaceTemplate`
names five group devices and nothing anywhere names a length of pipe.

## The vacuum layout

Because a space takes groups rather than volumes, nothing in the PV interface
says which valve stands between which two lengths of pipe. The layout is where
that is written down, once, in beam order:

```python
vacuum = vacuumLayout(
    upstream,                          # a vacuumVolume
    gate(f"{dom}-VA-VALVE-01"),
    absorber,
    gate(f"{dom}-VA-VALVE-02"),
    downstream,
)
vacuum.attach(everyDeviceTheIocBuilt)
```

A gate's neighbours are simply the entries either side of it, so **there is no
second place for the topology to disagree with itself** — you cannot name a
volume that is not there. Devices are referenced by **PV name**, not by object,
and `attach` resolves them and raises on anything it cannot find. That is
deliberate: it means the layout can eventually be hand-written next to a builder
XML that says which devices exist but nothing about the pipe.

A valve with pipe on only one side simply has no `gate` entry — the fast valve
protects the ring, and there is no volume upstream of the first one to isolate
from.

## The vacuum model

Pressure belongs to a **volume**, not to a device. A gauge reports what its
sensors make of the volume it is on; an ion pump both reads its volume and pumps
on it.

- **`vacuumVolume`** — one length of pipe, its gas load, the devices on it.
- **`volumeGroup`** — the volumes currently at one pressure. **All the arithmetic
  lives here**, not on the volume, because a volume valved onto its neighbour has
  no pressure of its own to work out. A lone volume is a group of one; there is no
  separate code path for the isolated case.
- **`vacuumLayout`** — re-derives the groups from valve positions on **every
  tick** (each open valve merges the groups either side of it). Nothing is cached,
  so valves opening and closing need no bookkeeping, and a run of open valves
  equalises end to end without anyone knowing the topology.

A group settles where its total gas load balances its total pumping speed
(`ΣgasLoad / Σspeed`), floored at its worst member's `basePressure`; with nothing
pumping it creeps to its worst `ventPressure` instead. Joining conserves **P·V**,
so the mixed pressure is capacity-weighted.

The time constant is `pumpdownFactor × Σlitres / Σspeed`, where a volume's
`pumpdownFactor()` is `timeConstant × its installed speed / its litres`: a
dimensionless few hundred, capacity-weighted across only those volumes that
*have* pumps. **Keep that indirection.** It is what makes valving an unpumped dead
leg onto a pumped volume slow the pump down in proportion to the capacity added.
Rewriting it as `timeConstant` scaled by a speed ratio looks simpler and is wrong
— that form cannot see capacity growth at all.

Consequences worth knowing before you go looking for a bug: switching pumps off
makes gauges rise; opening a valve onto a vented section spoils the good side and
**trips its ion pumps on high pressure**, which is emergent, not scripted; and a
tripped pump stays latched until its volume is back below `SIM:TRIPP`, so
`MPC-nn:RESET` then `:START` will appear to do nothing while the volume is still
high. That is correct behaviour.

Equilibria are set by `gasLoad / speed` and are closer together than they look.
In the example instance the absorber vessel settles at `1.2e-6 / 1000 = 1.2e-9`
mbar, so stopping half its pumps only moves it a factor of two, and no amount of
waiting will move it further. Work the expected number out from the volume's own
constants before concluding a pump down is broken — or before writing a test
threshold.

## Driving the simulation

There are **no `SIM:` records on the vacuum model** — it publishes nothing, by
design, because none of it exists on real hardware. The knobs are plain Python
attributes, reached from the IOC's interactive shell, where the volumes are in
scope:

```python
>>> absorber.gasLoad = 1.0e-4        # a leak the pumps have to hold against
>>> absorber.forcedPressure = 1.0e-3 # pin it, and its whole group with it
>>> absorber.forcedPressure = None   # back to the model
```

Over Channel Access you cannot spring a leak, but you can still do everything
interesting: stopping pumps makes the pressure rise, starting them brings it
back, and opening a valve equalises two volumes. `SIM:` records still exist on
the *devices* (`IONP-nn:SIM:TRIPP`, `IMG-nn:SIM:STARTUP_DELAY`) — those name a
knob on a thing that is real.

## Groups

The five group classes mirror `digitelMpcIonpGroup`, `mks937aImgGroup`,
`mks937aPirgGroup`, `mks937aGaugeGroup` and `dlsPLC_vacValveGroup`.

**A group's members may themselves be groups.** `SR21C-VA-GIONP-01` in
`SR21C-VA-IOC-01.xml` has `SR21A-VA-GIONP-01` and `SR21S-VA-GIONP-01` as members,
and one write starts sixteen supplies. That works because a group publishes its
records under **the same attribute names its members use** — a group's status is
`statusPV` just as a pump's is — so nothing that reads a member has to know which
it has got. Keep that property when adding to a group.

Ticking is order-sensitive: a group must be ticked after its members, and a group
of groups after the groups inside it. `orderedGroups()` sorts on depth and the
IOC file relies on it.

The gauge groups come from **mks937a** while the gauges themselves are **937B**,
because that is where a space's builder XML gets its groups from and there is no
937B equivalent. A 937A gauge keeps its interlock setpoints on itself; a 937B puts
them on numbered relays. So `:RLY:` is relay 1 (valve interlock), `:RLA:` is
relay 2 (MPS interlock), `:RLB:` is the Pirani's relay 1 (ion pump interlock).
`:PRO:ENABLE` is the one record with nowhere to go and is not created.

## Building an instance

Generate it (below) unless there is no XML to generate from. Either way the
assembly order is fixed, because each layer reads the one below it:

1. **Devices** — valves, `mpcRecord` + `ionPumpRecord`, `gaugeSetRecord`. None of
   them take a volume; they are built exactly as a builder XML declares them.
2. **The vacuum layout** — `vacuumVolume`s and `gate`s in beam order, then
   `layout.attach(everyDeviceYouBuilt)` to resolve the names. Prefer
   `builder_xml.attachLayout(layout, devices)`, which does that *and* refuses a
   layout that leaves a pump or gauge off every volume. `attach` alone only
   catches the opposite mistake — a layout naming a device that was not built.
   The two hand-written examples still call bare `attach`; generated instances
   and `parsed_ioc.attach` go through `attachLayout`.
3. **Groups** — over devices, then over groups. `orderedGroups()` sorts them so a
   group ticks after everything inside it.
4. **Spaces** — `spaceRecord(prefix, ionp=…, gauge=…, img=…, pirg=…, valve=…)`.
5. **The tick list**, in that same order: layout, devices, groups, spaces.

### The usual way in: generate it from the XML

Steps 1, 3, 4 and 5 are already written down in the builder XML the real IOC is
built from. `generate_ioc.py` reads that and writes the instance out as Python:

```bash
dls-va-ioc-sim generate .../SR03C-VA-IOC-01.xml 99     # -> sr99c-va-ioc-01.py
dls-va-ioc-sim generate .../SR21C-VA-IOC-01.xml 99 -n  # report only, write nothing
```

**One file comes out**, named after the IOC in lower case, written into the
current directory and executable — it is the instance *and* its launcher. See
*The instance is its own launcher* below.

**Generate, then edit.** The output is a plain instance file that imports only
the installed package — no template to keep in step, and hand edits are never
undone by regenerating something else. It refuses to overwrite an existing
instance without `--force`, because the one you have has probably been edited.

Checked by generating `SR99C-VA-IOC-01.py` and diffing against the hand-written
one: **2080 records, byte identical databases.** All 24 `SR-BUILDER` cell XMLs
generate, parse as Python and run.

- **Step 2, the vacuum layout, is generated as a guess and marked `EDIT ME`.**
  One volume per domain, joined by nothing, litres from the supply count and
  gas loads set to settle each domain near 7e-10 mbar. It runs out of the box
  and it is not the beam pipe. The XML cannot tell you the beam pipe.
- `cell` rewrites the two digit cell in every device name. A simulation serving
  `SR03A-VA-IONP-01:START` is the thing the non-standard port rule exists to
  prevent, so the argument is not optional in spirit even though it defaults.
- **`attachLayout` refuses a layout that leaves any pump or gauge off a
  volume**, and the generated file calls it. That bug bit twice before the
  check existed: the device keeps `volume = None`, its first tick raises, the
  dispatcher swallows it, and the *rest of that controller's* gauges stop
  updating with no visible symptom at all.
- **The report is written into the generated file as a comment**, so what the
  parse made of the XML is readable without re-running anything. Anything it
  has never seen says `NOT RECOGNISED`. Nothing is skipped silently.
- Two standing approximations: **mks937a is built as mks937b** (the machine is
  going that way, and `SR-VA.gaugeSetA` and `gaugeSet` become the same thing),
  and **a QPC is built as a Digitel MPC**. Both are in the `builder_xml` header.
- Gauges arrive two ways — `SR-VA.gaugeSet*`, or a bare `mks937a.mks937a`
  controller with its IMGs and PIRGs declared separately, which have to be
  paired back up by id. The older cells use the second form. `gaugeSetRecord`
  takes `idB=None` for a controller with one pair fitted, which those need.
- What it cannot do: a gauge declared as `mks937a.mks937aGauge` is read off an
  ADC and has no IMG/PIRG pair behind it, so there is nothing to build. In
  `SR02C` that drops one gauge group, reported rather than guessed at.

`parsed_ioc.iocFromXml` builds the same devices at start up instead of
generating a file, for an instance that would rather stay three lines long, and
is what `dls-va-ioc-sim run` is. It goes through the same
`builder_xml.parseXml`, so the two cannot drift — **keep it that way:
`builder_xml` holds no EPICS records and must not import softioc.** The CLI
relies on it: `generate` never imports softioc, so it works on a machine with no
EPICS on it.

Nothing above knows the domain, the number of volumes, or the shape of the
machine. That used to be checked by hand-building one deliberately different
instance; it is now checked by **generating all 24 `SR-BUILDER` cell XMLs**,
which between them cover both gauge declaration styles, QPCs as well as MPCs,
both valve tags, group trees three deep and one cell with no vacuum in it at
all. If a change makes that hard, the change is in the wrong layer.

**The modules are a package now, and that line is gone.** Every instance used
to open with `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))`
because the framework was a directory of scripts; an instance now does
`from dls_va_ioc_sim.vacuum_model import …` like anything else, and can sit
wherever its IOC does rather than having to live beside the framework. The
generator writes into the current directory in consequence, not beside itself —
beside itself is now site-packages.

### The instance is its own launcher

Making the modules a package left one problem behind: **whatever runs a
generated instance has to be able to import `dls_va_ioc_sim`**, which the
`/dls_sw/prod/.../softioc/4.6.0` interpreter the old `.sh` exec'd cannot. The
instance now answers that itself, and there is no `.sh` at all:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["dls-va-ioc-sim==1.0.0b1"]
# ///
```

`./sr99c-va-ioc-01.py` is now the whole of it: uv builds the environment the
header asks for, caches it, and runs the file in it. `python
sr99c-va-ioc-01.py` still works in an environment that already has the package.

- **The pin is the version that generated the file**, so an instance written
  today still starts when the framework has moved on. A checkout's
  setuptools_scm version (`0.1.dev0+d20260811`) is on no index, so
  `requirement()` writes the dependency **unpinned** when the generator is
  unreleased — otherwise generating from a working tree would write a file that
  cannot resolve. Note what that means when testing: `uv run --script` on an
  instance generated from a checkout fetches the *released* package, not your
  working tree. Use `python instance.py` in the project venv to exercise local
  changes, or `dbdump`, which never leaves the process.
- **The Channel Access ports are set in the instance**, at the top, before
  softioc is imported — `os.environ.setdefault("EPICS_CA_SERVER_PORT", "6064")`.
  In the shell script they were one forgotten wrapper away from 5064; in the
  file they cannot be lost. `setdefault` means the environment still wins, so
  `EPICS_CA_SERVER_PORT=6066 ./other-instance.py` runs a second simulation
  beside the first. The numbers live in `CA_SERVER_PORT`/`CA_REPEATER_PORT` in
  `generate_ioc.py`.
- **`pythonSoftIOC` was never doing anything.** Its entry point is twelve lines
  that `subprocess.Popen([sys.executable, script])` — it is only "an interpreter
  with softioc in it", which is exactly what the PEP 723 header asks uv for. It
  also does not exec, so a signal sent to it never reaches the IOC; running the
  file directly is better under procServ, not just shorter.
- **`examples/` deliberately has no such header.** Those instances are run from
  the checkout to exercise the working tree, and a PEP 723 header would quietly
  run the release instead.

### More than one cell at a time: `start`

`dls-va-ioc-sim start a.py b.py …` serves any number of written instances as
**one IOC on one port**, and one file behaves exactly as running that file
does. `start_ioc.py` stubs the four things that *start* an IOC — `LoadDatabase`,
`iocInit`, `interactive_ioc`, `AsyncioDispatcher` — while it `exec`s each file,
which is `dbdump`'s trick with the stubs put back afterwards, then starts the
IOC once over the merged recordset with one tick loop. **The instances need no
change at all**; keep it that way.

- **Each file gets its own namespace.** Every instance has a `vacuum`, an
  `upstream` and a `dispatcher` at module scope, so one namespace would have
  cells silently overwriting each other. The IOC shell gets them as
  `instances`, keyed by filename:
  `instances["sr06c-va-ioc-01.py"]["upstream"].gasLoad = 1.0e-4`.
- **Cells must be generated with their own numbers.** Distinct PV names are
  what lets them share one database; all-99 builds duplicate records and the
  second instance fails.
- **`callbackSetQueueSize` is not optional above a few thousand records.**
  Every `.set()` from the tick loop queues a record process on EPICS's `cbLow`
  ring, which defaults to **2000 entries**. One cell never comes near it; 24
  cells put ~50000 on it in one pass, and the overflow is *silent* — dropped
  record processing, frozen readbacks, missed monitors, and nothing but
  `callbackRequest: ERROR cbLow ring buffer full` on stderr. Measured: 25805
  overflows in the first minute before the ring was sized, none after. It is
  sized from the record count (four entries a record) and has to be set
  **after `LoadDatabase` and before `iocInit`**, which is where EPICS creates
  the ring.
- **`--no-interactive` is what a container wants.** An IOC shell reads stdin,
  and a container nothing is attached to reaches EOF and exits.
- Measured on 24 cells (one XML regenerated as cells 01–24): **49920 records,
  1032 devices, ~11s to build, 322 MiB resident, 15% of one core.** That is
  the sizing argument for one process over one pod per cell — and under
  `hostNetwork` 24 pods would in any case be 24 attempts to bind 6064.

## softioc versions — the trap is retired, keep the habit

`start-ioc` now runs **4.6.0**, not 4.0.1. That matters more than it looks:
**4.6.0 is identical to 4.7.x on all three of the APIs that used to diverge** —
`aIn(name, LOPR, HOPR, EGU, PREC, **fields)`, `aOut(name, DRVL, DRVH, EGU, PREC,
**fields)` and `dispatcher(func, func_args=())` — so production and
`pip install softioc` no longer disagree and the silent field swap cannot happen.
(Checked by reading `builder.py` and `asyncio_dispatcher.py` from a 4.6.0 install;
4.6.0 has no wheel and its source build would not import here, so this is not a
run-time check.)

Keep the habits anyway — they cost nothing, and they are what lets the code run on
either. The full table and reasoning are in the **`pythonsoftioc-device-sims`
skill**; it still describes 4.0.1 because other IOCs are still pinned there.

- **Never pass a builder field positionally.** Keyword-only, always. For `aOut`
  spell out `DRVL`/`DRVH` *and* `LOPR`/`HOPR`: positions 2 and 3 meant LOPR/HOPR
  on 4.0.1 but DRVL/DRVH from 4.6 on, with no error either way.
- **Dispatch coroutines with no arguments** and close over a module-level list.
  `dispatcher(coro, (x,))` became `coro((x,))` on 4.0.1.
- No `PREC` on a `longOut` — that one is a build failure on every version. If a
  template declares the demand as `ao`, use `aOut` even when the readback is a
  `longin`.

**A dispatched coroutine that raises dies silently** and the IOC stays up serving
every PV, so the symptom is frozen readbacks rather than a crash. An instance's
tick loop should catch per device and log once — check the IOC's stdout for
`Simulation failed for …` before suspecting a state machine. Every tickable object
needs a `prefix` attribute for that loop.

### `.set()` on an output record fires its own `on_update`

This one cost a debugging round and this file previously said the opposite.
Measured on 4.7, on a **running** IOC (after `iocInit`):

| | `.set()` fires `on_update`? |
|---|---|
| `boolOut`/`aOut`/`mbbOut`, value changed | **yes** |
| same, `always_update=True`, value unchanged | **yes** |
| same, `always_update=False`, value unchanged | no |

No CA write is needed — a `.set()` from the tick loop is enough. So **a callback
that writes its own record calls itself again**, and with `always_update=True` it
never stops: one external `caput` was measured driving 88523 callbacks and leaving
pumps flapping between Standby and Waiting.

Groups have to write their members' demand records, because the real templates fan
out with CA links and a pump started by its group does show Start on its own
`:START`. Do it through **`device_groups.setDemand`**, which writes only on a
change and so settles after one extra pass. Every setter reached that way must be
safe to run twice — they are.

The trap is invisible before `iocInit`: a script that builds the database and
calls the setters directly sees no re-entry at all, which is why this was
originally written down backwards.

## Verifying a change

Build the database and diff it against the version before your change. Since
the production softioc moved to 4.6.0 this no longer has to be a *cross-version*
diff — 4.6 and 4.7 agree on the APIs that used to swap fields silently, so a
plain before/after under one version catches what matters.

`dbdump` does it, and takes a second:

```bash
export UV_PROJECT_ENVIRONMENT=/root/.venvs/dls-va-ioc-sim   # never the shared mount
uv run dls-va-ioc-sim dbdump sr99c-va-ioc-01.py before.db
# ...make the change...
uv run dls-va-ioc-sim dbdump sr99c-va-ioc-01.py after.db
diff before.db after.db
```

Two of the tests do exactly this and are the regression net: `test_instance.py`
dumps the same instance twice and asserts the databases match, and dumps again
after regenerating from the XML. Neither asserts a record *count*, for the
reason below. The port into this module was verified the same way — byte
identical at 2080 records for SR03C and 1119 for FE99B.

**For a change to how an instance is *assembled* — extracting a module,
generating the file from a builder XML, rewriting a parse — expect the diff to
be empty.** Byte identical, not shape identical: nothing about the database is
supposed to move. That is a far stronger check than running the IOC and a far
cheaper one, and it is what verified both rewrites of `builder_xml.py`, the
whole of `generate_ioc.py` and the port into this module. **Diff before you
run.**
For a change to a device class the diff is meant to be non-empty, and is then
the record-by-record statement of what you changed.

The cross-version diff is still worth one run if you touch a builder call, or if
any IOC you care about is still pinned to 4.0.1. Assert the **shape**: same record
names and types under both, every differing line an `OMSL` or a `PINI` (4.7 emits
those where 4.0.1 leaves them to the dbd default). Never assert a record count —
that only tripwires whichever instance you happened to build, and goes stale the
moment anyone adds a device.

Neither version needs `/dls_sw`. 4.0.1 installs from PyPI on Python 3.8 and
imports once `CC` is patched; the `pythonsoftioc-device-sims` skill has that
recipe. `dbdump.py` already carries the `builder.LoadDatabase` stub, without
which the dump comes out empty because `LoadDatabase` consumes the recordset.
`dumpDatabase` mutates softioc's module attributes to do it and cannot undo
them, so it is **one dump per process** — take the two you are comparing in two
runs, which is what the CLI and the tests both do.

4.6.0 itself has no PyPI wheel and its source build does not import in a plain
container, so it cannot be checked this way — read `builder.py` from a
`uv pip install --target` if you need to confirm a signature.

Then run it and drive it over Channel Access, on a **non-standard loopback port** so
it can never be mistaken for real hardware:

```bash
EPICS_CA_SERVER_PORT=15064 EPICS_CAS_INTF_ADDR_LIST=127.0.0.1 \
  EPICS_CAS_BEACON_ADDR_LIST=127.0.0.1 \
  sh -c 'sleep 420 | uv run python examples/FE99B-CS-IOC-01.py'
```

Plain `python`, not `pythonSoftIOC`: that entry point only Popens
`sys.executable` on the script, and the project venv is the interpreter you
want here — an example has no PEP 723 header precisely so that it runs the
working tree. A *generated* instance is `./sr99c-va-ioc-01.py` on its own, and
sets its own port, so it needs only the two `EPICS_CAS_*` lines above.

- `interactive_ioc` **exits on stdin EOF**, so `< /dev/null` kills the IOC at once.
  Hold stdin open with a piped `sleep`, which also bounds its life.
- **For the client, `--with aioca`, not `--with cothread`.** aioca pulls in
  `epicscorelibs` and so finds `libca` with no EPICS install; bare cothread dies
  at import with `KeyError: 'EPICS_BASE'` (`--with cothread --with epicscorelibs`
  fixes it — see the `cothread-uv-scripts` skill). The client needs the port
  pointing at the IOC as well, which is easy to forget and looks exactly like a
  dead IOC:
  ```bash
  EPICS_CA_AUTO_ADDR_LIST=NO EPICS_CA_ADDR_LIST=127.0.0.1 \
    EPICS_CA_SERVER_PORT=15064 uv run --no-project --with aioca --python 3.11 python probe.py
  ```
- **Run the IOC by absolute path** if you background it. The instance adds its
  own directory to `sys.path`, but the shell may not be in it.
- Demand→readback wiring is worth testing over CA even though `.set()` does fire
  `on_update` — only a real write exercises the record's own conversion and limits.
- In-process, a record's `.get()` takes no `as_string`; index the module's own
  `*_STATES` tuple. Over CA it is `caget(pv, datatype=str)`.
- **Allow for the blocking valve sleeps when timing a test.** A group `:CON` write
  serialises every valve underneath it, and while those `time.sleep` calls run the
  tick loop is stopped, so gauges and pumps freeze too. A test that reads three
  seconds after a group open will see half-finished transitions and stale
  everything else.

## Conventions and standing decisions

- **Skip the StreamDevice/PLC glue records** and list what you skipped in the module
  docstring. Nothing outside the IOC reads `:COMMSMATCH`, the `:FAN10S` fanouts, the
  `:PSEQ`/`:HYSTSEQ` sequences or the `:SPOFFWRITE` chain, and there is no serial
  link here for them to drive. The groups skip the same sort of thing: `:CALSTA`,
  `:MAXSTA`, `:MINSTA`, `:SELSTA`, `:SELERR` and the `:SEQ*` fan-out sequences are
  internal to their templates and are computed in Python instead.
- **Create records in the same order the builder XML does** wherever numbering is
  controller-wide. Relay *n* in `gaugeSet.xml` is IMG-a 1–4, IMG-b 5–8, PIRG-a 9–10,
  PIRG-b 11–12; grouping per gauge pair instead silently mis-points the
  `GCTLR:RLY<n>` aliases. Verify an alias by writing through one name and reading
  back the other.
- **Freeze an invalid reading, never publish a live one.** The real templates disable
  `:P` through `:PDIS` with `DISS INVALID` so it holds its last value; do the same
  with `severity=alarm.INVALID_ALARM, alarm=alarm.DISABLE_ALARM`. A gauge with its HV
  off must not leak a pressure it cannot know. Likewise an ion pump with its supply
  off reads `:P` = 0 — an MPC derives pressure from discharge current and has none.
- **Slide pressures in log space** (`vacuumSim.slideLog`). Vacuum covers decades and
  a linear approach spends all its time in the top decade, then appears to stop.
- **Keep deliberate template infidelities, with a comment saying so.** An ion pump's
  `:V` has `LOPR 0 / HOPR 10` while reading kilovolts because that is what the real
  IOC serves. A space's `:STA` is a `longIn` where the template has an
  `mbbiDirect`, because pythonSoftIOC only builds those without device support and
  they cannot then be written from Python; the number is the same and only `.B0`
  field access is lost.
- **The simulation comes up as a *running* front end** — pumps pumping, cold cathodes
  lit — where the real templates come up idle. Otherwise every volume leaks up to its
  vent pressure whenever the IOC is left alone. Both are constructor arguments
  (`running=`, `enabled=`) if the faithful default is wanted. Valves are the
  exception and still come up Closed with their interlocks Failed, so a space reads
  `:STA` = 4 until you `:CON` Reset and then Open.
- **Group `delay=` is accepted but not slept through.** The templates stagger eight
  supplies by a few seconds to spread the inrush; the dispatcher is single threaded,
  so sleeping there would stall every other device. `:STARTING`, `:OPENING` and
  `:SWITCHING` are still published, they just go up and down within the one call.
- **Valve interlocks are deliberately not driven by the gauge relays or the ion pump
  setpoints.** This was offered and declined: relay setpoints are configuration
  bookkeeping only. Ask before wiring them into `fe_seq_records.py`.
- **Things that move in the beam rather than in the pipe carry no volumes.**
  Absorbers and shutters are `valveRecord`s and separate nothing, so they get no
  `gate` entry in a layout. Nor does a valve with pipe on only one side of it —
  in the example instance the fast valve would isolate, but there is nothing
  upstream of the first volume to isolate from. Give it a volume on both sides
  if it ever needs to do something.

## Gotchas

- **`valveRecord.isOpen()` matches `OPEN_STATES` exactly.** Do not test
  `"Open" in sta` — that catches `"Opening"` as well. The pre-existing `reset()`
  still has that substring bug; it has been left alone deliberately.
- **`open()` and `close()` block** in their `on_update` callbacks via `time.sleep`,
  which stalls the dispatcher for the valve's `OPEN_DELAY`/`CLOSE_DELAY`. Groups
  multiply this: a valve in two groups gets driven twice, so one `GVALV-04:CON`
  write can stall the simulation for seconds. Harmless at half a second, but do not
  lengthen it far.
- **There is no launcher script any more, and there is nothing to put back.**
  `start-ioc` and `va-start-ioc` in the old tree became a generated `.sh`, and
  that became the instance's own header — see *The instance is its own
  launcher*. The ports are still 6064/6065 and still **not** 5064, so the
  simulation cannot be found by a client looking for the real machine.
- **Every instance defaults to the same port**, but the environment overrides
  it, so two simulations run side by side by starting the second with
  `EPICS_CA_SERVER_PORT=6066 EPICS_CA_REPEATER_PORT=6067`. Nothing has to be
  edited; make it a flag only if that starts to hurt.
- **`examples/` is not on the test path and is not generated.** The two
  hand-written instances there are documentation; they were updated by hand for
  the package imports, and `FE99B-CS-IOC-01.py` is the only thing exercising
  `fvalveRecord` and the absorber interlock chain, since no SR cell has a fast
  valve.
- **The generator's `--force` exists to be *not* used.** A generated file is
  meant to be edited — the vacuum layout in it is a guess — so regenerating over
  the top of one throws away the only part that took any thought. Generate to a
  new name and merge.

## Integrating with the template

This module is a python-copier-template package, and the port had to settle
three things where 6000 lines of working, deliberately-styled code met the
template's house rules. All three are recorded in `pyproject.toml` next to the
setting they justify:

- **Naming: kept.** Classes and functions stay lowerCamelCase because they are
  named after the DLS template each mirrors, and that mapping is the
  specification when a device is edited. `N801`/`N802`/`N803`/`N806` are off.
  Module *filenames* were snake_cased, which costs nothing — nothing outside
  the package referenced them.
- **Type checking: standard, not strict.** The copier answer was strict; strict
  gives 2848 errors of which 2588 are `reportUnknown*` cascading off untyped
  softioc calls, and annotating this code cannot fix those. Standard is clean.
  Put it back to strict if softioc ever grows stubs.
- **Formatting: not run.** `ruff format` would rewrap the hand-aligned
  continuation lines throughout. Everything ruff *checks* is clean; only the
  formatter is left off.

The one bug the port introduced was caught by the database diff, not by review:
a `%`-format conversion turned `"setpoint%d" % self.number` into
`f"setpoint{self}".number`, which only fires when a group builds its setpoint
records. **That is what the diff is for.** Run it on anything mechanical.
