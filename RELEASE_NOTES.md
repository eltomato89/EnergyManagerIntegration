# 0.5.0b3 — pre-release

One request from the issue tracker, one correction to the interface language, and a display
that finally speaks the unit you typed.

## Changed

- **The battery's minimum state of charge is now an entity**, `number.…_battery_min_soc`,
  instead of a field in the setup form ([#4]). It is not a property of your installation —
  it changes with the day: with a very good forecast you may want the loads to start in the
  late morning at a low state of charge, on an overcast day you may not. A form field cannot
  be automated; `number.set_value` can.

  A change takes effect immediately, not on the next cycle. Your current value is carried
  over once on first start, so nothing changes silently, and from then on only the entity
  counts. `0` switches the rule off. Without a state-of-charge sensor the entity is not
  created — the threshold would have nothing to compare against, and a control that does
  nothing is worse than no control at all. The former form error about this is gone with the
  field.

  The value is also exposed as a `battery_min_soc` attribute on the surplus sensor.

- **A daily target is now reported in the unit it was entered in.** For a switchable load
  that is hours: the status sensor carries `daily_target_h` and `daily_done_h` alongside the
  kWh the automation actually computes with. Reading "0.8 of 4.0 kWh" forced you to divide by
  the rated power to recognise a target you typed as "4 h". Needs card `0.7.0-beta.3` for the
  display; on an older card nothing changes.

  These are full-load hours, not the clock at the switch — running below the rated power, they
  advance more slowly than time does. The automation compares energy against forecast, and the
  display should show what the automation does.

## Fixed

- **The device models were German** — "Überschusssteuerung" on the hub, "Verbraucher" on every
  load — and stood that way in an English interface too. A device model cannot be translated
  in Home Assistant; there is no `translation_key` for it. They are now `Surplus control` and
  `Load`. Existing devices pick up the new name when the integration reloads; automations are
  unaffected, as the model is only a filter in the device picker.

## Before you arm anything

This is a pre-release. The main switch is off after setup and every load has its own
automation switch: add a load, watch it, and arm it once the numbers match what your
installation is actually doing.

**Known limitation:** the forecast is still not divided among several daily targets. With a
single daily target this does not occur.

[#4]: https://github.com/eltomato89/EnergyManagerIntegration/issues/4
