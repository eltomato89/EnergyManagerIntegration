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
lockstep. What the two sides share is the attribute contract, not the release cadence: releases on
one side alone are routine, and the card probes for attributes rather than versions, so an older
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

### When someone else switches

Switching that did not come from this integration is recognised from the service call context — at
the device, in the interface, or from another automation. With a **manual override** time set, the
automation then keeps away from that load for the configured period.

The default is 0, which means off, and that is deliberate: whether an override makes sense depends on
the device class, not on the user. A dehumidifier never switches itself; a hot water tank cycles, and
for it every detection would be a false one. A value that is right for the one and systematically
wrong for the other does not belong in a default for all. The `last_foreign_change` attribute shows
how often the case actually occurs for a given device.

The automation switch is **never** written by the integration. It stays user configuration — and
because the override is time-limited, a false detection expires by itself instead of leaving a load
out of the automation until someone notices.

## Modulating loads

A load can be **modulating**: instead of only being switched on and off, its power is set in steps
through a **control entity** — a `number` in W, kW or A, or a `select`.

The step grid is **read from that entity** on every evaluation, not configured: `min`, `max`, `step`
and the unit for a number, `options` for a select. Only what Home Assistant cannot know is
configured — the number of phases for a control entity in amperes, and the watt value of each option
for a select. Reading it fresh each time is deliberate: some wallbox integrations narrow their
maximum while charging, and a cached grid would throw that away.

A **missing unit is refused**, and that is stricter than for a power sensor, where watts are assumed.
An ampere entity read as watts would yield a ladder from 6 to 16 W that fits into any surplus — and
the automation would write 16 while the device draws 16 A.

Three more fields per load: a **minimum level** below which the load is switched off rather than
throttled (the minimum of the control entity is the limit of the charging station, not that of the
vehicle), a **hold time** between two level changes, and the existing hysteresis, which also acts as
the deadband below which no level change happens.

If a device does not reach the level it is asked for, its ladder is capped at what it actually
draws, one step above the observed maximum. Without that the priority cascade would permanently
reserve the difference for a load that never claims it.

### When the device has no switch entity

The switch entity stays **mandatory and separate** — for a wallbox, 0 is not on the ladder at all,
since the charging current starts at 6 A and charging cannot be stopped through it.

Not every device offers a plain switch. A `select` for the charge release, or a control entity whose
0 means off, are both common. Wrap them in a template switch rather than expecting the integration to
learn every device's dialect:

```yaml
# Charge release is a select (go-e: 2 = charge, 1 = off)
switch:
  - platform: template
    switches:
      wallbox_release:
        value_template: "{{ states('select.goe_XXXXXX_frc') == '2' }}"
        turn_on:
          action: select.select_option
          target: { entity_id: select.goe_XXXXXX_frc }
          data: { option: "2" }
        turn_off:
          action: select.select_option
          target: { entity_id: select.goe_XXXXXX_frc }
          data: { option: "1" }
```

Where **0 on the control entity means off**, the template switch needs a guard. The integration sets
the level *before* switching on, so that the device does not start at the previous, possibly highest
step — a `turn_on` that writes a fixed value would overwrite exactly that:

```yaml
      heater_release:
        value_template: "{{ states('number.heater_set_output') | float(0) > 0 }}"
        turn_on:
          # Only if nothing is set yet. Otherwise this would discard the level
          # the automation has just written.
          - if: "{{ states('number.heater_set_output') | float(0) <= 0 }}"
            then:
              - action: number.set_value
                target: { entity_id: number.heater_set_output }
                data: { value: 500 }
        turn_off:
          - action: number.set_value
            target: { entity_id: number.heater_set_output }
            data: { value: 0 }
```

### One controller per device

A device that regulates on surplus **by itself** must not also be managed here. Two control loops on
the same load work against each other, and the safety nets above are built for a slow allocator, not
for a fast controller: 60 s of smoothing and a 60 s settling window are exactly what a controller
must not have.

This applies to wallboxes with a built-in PV mode, to zero-export controllers driving a heating
element, and to a second energy manager. Either switch off the other side's regulation, or leave that
load out of this integration.

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

**Turning down comes before turning off.** A modulating load first gives up only the difference down
to its smallest level and keeps running. If that is not enough taken together, a second pass turns
throttling into switching off — again from the bottom, and only as far as needed. The status sensor
counts both separately: `throttles` is how many would turn down for a given load, `displaces` how
many would go off.

**Not displaced** is anything working through a minimum runtime (a wash cycle in progress is not
aborted), anything not participating in the automation, anything under a forced run, and anything
just switched. A minimum runtime does **not** prevent throttling, though: it guards against
switching a device off too early, and a throttled device keeps running. What does apply to
throttling is the hold time between two levels — a displacement is no reason to move the ladder
faster than that.

### Daily targets

A load can be given a **daily target**: what it has to achieve per day regardless of the weather. A
pool pump has to complete its circulation whether the sun cooperates or not.

The rule is a comparison, not a control loop: once the **remaining forecast** no longer covers the
**energy still missing**, the load keeps running without surplus. Not before — as long as it works
out with sun, there is no reason to take grid power.

The unit follows from the control method: hours for a switchable load, kilowatt-hours for a
modulating one. Hours are not a statement for a modulating load — six hours at the lowest level and
six at the highest differ by a multiple and satisfy an hours target equally well. Internally
everything is kilowatt-hours; hours are only an input and display form.

Two prerequisites: an **energy meter** on the load, and a **forecast sensor** with the PV energy
still expected today (set in the integration options). Without the forecast the rule never applies —
a missing value is a configuration error, not a reason to run a device on grid power.

The day starts at **sunrise**, not at midnight. For an installation whose yield depends on the sun,
that is the cut that fits: a counter reset at midnight lies hours before the first yield and
separates nothing.

This is the only place where the automation deliberately accepts grid power, and the `must_run`
attribute says when it does — the answer to "why is that drawing from the grid".

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
| `manual` | Someone else switched this load — the automation keeps away for the configured time |
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
| `energy_manager.clear_manual` | Ends a manual override early. Nothing is switched; from then on the surplus decides again |
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
