# 0.5.0b1 — pre-release

**If you change nothing, nothing changes.** Every new setting defaults to off, so an
existing installation behaves exactly as it did before. The 417 tests that existed before
this work are unchanged and still pass.

## New

- **Modulating loads.** A load can now be run in steps through a control entity — a
  `number` in W, kW or A, or a `select` — instead of only being switched on and off. The
  step grid is read from that entity (min, max, step and unit; options for a select), so
  only the translation into watts is configured. Wallboxes, adjustable power supplies,
  heating elements with step relays.

- **Turning down comes before turning off.** When a more important load needs room, a
  modulating one below it first gives up only the difference down to its smallest level and
  keeps running.

- **Daily targets.** A load can be told what it has to achieve per day. Once the remaining
  PV forecast no longer covers what is still missing, it runs without surplus. Needs an
  energy meter on the load and a forecast sensor with the energy still expected today. The
  day starts at sunrise.

- **Manual override.** If someone switches a load by hand, the automation keeps away from it
  for a configurable time. Default 0, meaning off: whether this makes sense depends on the
  device. A hot water tank that cycles on its own would trigger it constantly.

## Please read before you enable anything

This is a pre-release, and **it has never run on real hardware.** Everything is covered by
automated tests, but no test writes a setpoint into an actual wallbox. The main switch is
off after setup and every load has its own automation switch — use both, and watch before
arming.

**Known limitation:** the forecast is not divided among several daily targets. Each load
compares what it is missing against the whole remaining forecast, so two targets of 3 kWh
each with 4 kWh forecast both conclude it works out. With a single daily target this does
not occur.
