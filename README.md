# Energy Manager — Integration

A Home Assistant integration that calculates the PV surplus and switches loads **automatically by
priority**.

It is the counterpart to the
[Energy Manager Card](https://github.com/eltomato89/EnergyManagerCard): the card displays and
allows manual operation, the integration decides and switches. Both use the same formula —
otherwise the display would claim something other than what actually happens.

> **Status: under development.** The automation switches, but has not yet been observed over a
> longer period on a real installation. The main switch is deliberately **off** after setup —
> observe first, then arm it.

## Installation

Requires Home Assistant **2025.2** or newer (config subentries).

### HACS

1. HACS → ⋮ menu → **Custom repositories**
2. Add `https://github.com/eltomato89/EnergyManagerIntegration`, category **Integration**
3. Install "Energy Manager", restart Home Assistant
4. Settings → Devices & Services → **Add integration** → "Energy Manager"

### Manual

Copy the folder `custom_components/energy_manager` to `/config/custom_components/` and restart Home
Assistant.

## Setup

1. **Choose the meter source**: a bidirectional grid sensor (>0 import, <0 export) or separate
   sensors for production and house consumption. A home battery is optional.
2. **Add loads** — one at a time via "Add subentry" on the integration entry. Only the switch
   entity is mandatory; a power sensor, the rated power and the timing fields make the decision more
   precise.

   If the **rated power** is missing, the integration estimates it from the recorded statistics of
   the power sensor: the maximum of the last seven days, refreshed once a day. The mean is unsuitable
   for this — it would include every hour in which the device was off. If that is not possible
   either (no power sensor, no recording, sensor without `state_class`), a default of 500 W applies,
   and that is almost always wrong.

   The `required_source` attribute on the status sensor tells you where the value comes from:
   `min_power`, `max_power`, `measured`, `estimated` or `default`.
3. **Observe.** The surplus sensor should match the installation and the load states should be
   plausible. If something is off, the configuration is wrong — not the calculation.
4. Only then turn on the **main switch**.

The [Energy Manager Card](https://github.com/eltomato89/EnergyManagerCard) from v0.4.0 finds the
integration on its own and displays everything. Loads are maintained **here only**.

### Versioning

Card and integration are versioned **independently**; the numbers are deliberately not kept in
lockstep. The card also runs without the integration, and what the two sides share is the attribute
contract, not the release cadence — the card probes for attributes rather than versions, so an older
integration means less display, never an error. Where a card feature does require a minimum
integration version, the
[interface contract](https://github.com/eltomato89/EnergyManagerCard/blob/main/docs/integration-contract.md#versionierung-getrennte-nummern-dokumentierte-mindestversion)
lists it. That document is the single place where cross-repository decisions are recorded.

## Safety nets

The integration intervenes in a real installation. Six mechanisms prevent it from doing so at the
wrong moment — each one closes a case that would otherwise show up as oscillation or as a wrong
switching decision:

| Protection | Against what |
| --- | --- |
| **At most one switching action per run** | Switching on three devices at once would mean spending the same budget three times |
| **Anticipation** | Seconds pass between switching on and the meter showing it. In that gap the same surplus would be handed out a second time |
| **Settling window** (default 60 s) | After a switching action that device is left alone, whatever the surplus does. **Choose it at least as long as the power sensor needs** — sluggish smart plugs report only after minutes |
| **Smoothing** (default 60 s) | A passing cloud is no reason to switch off. While the averaging window is only half filled, nothing is switched at all |
| **Nothing during startup** | While HA boots, not every entity reports a state — the installation would look as if everything had failed |
| **Nothing with an unusable sensor** | A kWh meter or a failed sensor is a configuration error, not a surplus of 0 W |

In addition, each load has the four timing fields (`turn_on_delay`, `turn_off_delay`,
`min_runtime`, `min_off_time`) and its own automation switch.

## What priority means

It determines two things:

1. **Who is served first** when surplus becomes available.
2. **Who has to give way to whom** if they would otherwise never get a turn.

The second point is the important one. An example: 800 W of surplus and three loads requiring
1000 W, 500 W and 200 W, in that order of rank. The first one does not fit, so the second and third
turn on. If the surplus rises to 1100 W, only 400 W are free — the most important load would **never**
run, even though the installation has long been delivering enough.

Therefore: if *available surplus + the load of the running, lower-priority consumers* is enough for
a more important one, those give way. As few as possible give way, the least important first, and
only if it is actually sufficient in the end — otherwise you would have switched things off and
gained nothing.

**Not displaced** is anything working through a minimum runtime (a wash cycle in progress is not
aborted), anything not participating in the automation, anything under a forced run, and anything
just switched. The `displaces` attribute on the status sensor shows how many would give way for a
given load.

### The battery as a shiftable load

Enter a **maximum charge power** for the battery (in its settings) and it joins the priority order
as a load of its own. Loads ranked above the battery are served first and stay on; the battery then
reserves up to its charge power at its rank; only what is left goes to loads ranked below it. When
the battery is full it reserves nothing. This is pure scheduling — the battery is **not** commanded,
so no control entity is needed; a `number.…_battery_priority` holds its rank, draggable in the card
like any load. Leave the charge power empty (the default) and the battery stays what it was: a
correction term in the surplus, with every load ranked ahead of it.

## Why is nothing happening?

The most common question — and without an answer, an absent switching action cannot be told apart
from a fault. Two places provide it:

The **automation status sensor** (`sensor.…_status`) for the whole:

| Value | Meaning |
| --- | --- |
| `starting` | HA is still booting, or the averaging window is too sparsely filled |
| `sensor_error` | A meter sensor is missing, has failed, or does not measure power |
| `paused` | Main switch off |
| `running` | Everything ready |

The **status sensor of each load** in its `blocked_by` attribute for the individual case:

| Value | Meaning |
| --- | --- |
| `not_managed` | The automation switch of that load is off |
| `unavailable` | The switch entity reports no state |
| `settling` | The settling window after the last switching action is running |
| `forced` | A forced run is active — the automation keeps away |
| `turn_on_delay` / `turn_off_delay` | The condition has not held long enough yet |
| `min_runtime` / `min_off_time` | A lockout is running, see "locked until" |

If nothing is listed there, the surplus is simply not sufficient — which the load state then says
(`off_close`, `off_insufficient`).

## What the integration provides

One hub device plus one device per load:

**Hub**

| Entity | Purpose |
| --- | --- |
| `switch.…_automation` | Main switch. Off means: nothing is switched |
| `sensor.…_surplus` | Available surplus after reserve and state-of-charge rule |
| `sensor.…_surplus_unsmoothed` | Unsmoothed raw value, for diagnosis |
| `sensor.…_status` | State of the automation |
| `number.…_battery_priority` | Rank of the battery as a shiftable load — only when a maximum charge power is set |

**Per load**

| Entity | Purpose |
| --- | --- |
| `switch.<name>_automation` | Does this load participate in the automation? |
| `number.<name>_priority` | Rank, 1 = highest |
| `sensor.<name>_status` | Load state, same values as in the card |
| `sensor.<name>_locked_until` | When the lockout ends — exact, not estimated |

No helper variables are required. The card reads and operates these entities directly.

The entity IDs above follow the language of your Home Assistant instance, because HA derives them
from the translated entity name. On a German instance the same sensors are called
`sensor.…_ueberschuss` and `sensor.<name>_gesperrt_bis`. The card does not rely on the IDs; it
identifies the entities by their role.

## Services

Four services for what cannot be expressed through entities alone: a **duration**. The main switch
only knows on and off.

| Service | Purpose |
| --- | --- |
| `energy_manager.force_on` | Switches a load on immediately and keeps it running for the given time, regardless of surplus. Works even with the main switch off, since this is an operation rather than an automation decision |
| `energy_manager.clear_force` | Ends a forced run early. Nothing is switched off; from then on the surplus decides again |
| `energy_manager.pause` | Stops the automation, optionally for a set time ("two hours of quiet", during maintenance for instance) |
| `energy_manager.resume` | Arms it again |

`force_on` and `clear_force` target a **load device**; an entity of the same device works just as
well, since an automation usually has an `entity_id` at hand.

```yaml
action: energy_manager.force_on
target:
  device_id: <wallbox>
data:
  duration: "01:30:00"
```

## Development

```bash
uv venv --python 3.13
uv pip install -r requirements_test.txt
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
```

The calculation core (`surplus.py`, `smoothing.py`, `engine.py`) is a port of the card logic and is
verified against the same test cases. A differing result is a bug — not an acceptable deviation.

## Licence

MIT
