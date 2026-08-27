# 0.5.0b2 — pre-release

Two fixes and one addition, all found by running `0.5.0b1` on a real installation. Nothing
here changes behaviour for switchable loads.

## Fixed

- **A load that could not reach its smallest level was silently holding power back.** The
  ladder is capped at what a device actually draws, but that cap cannot go below the first
  step — so the priority cascade kept reserving the minimum level while the device drew a
  fraction of it. Seen on a wallbox configured as three-phase while charging single-phase:
  smallest level 4140 W, actual draw 1779 W, and 2361 W withheld from the loads below it
  with nothing anywhere reporting a fault. Below the smallest level the claim is now what
  the device actually draws.

- Such a load was also shown as a **one-step ladder** — a single segment across the full
  width labelled "Level 1/1", which reads like a device with one step rather than like a
  misconfiguration. Needs card `0.7.0-beta.2` for the improved display.

## Added

- **`phases_entity`.** The number of phases is not a constant: a wallbox switched between
  single- and three-phase changes the power of every current step by a factor of three, and
  a fixed setting is wrong from that moment until someone notices. Point this at an entity
  reporting the phase count and the ladder follows the device; the fixed field stays as a
  fallback.

  A plain number is expected (1, 2 or 3). Devices with their own dialect — go-e reports the
  phase *mode* as 0, 1, 2, where 2 means three phases — need a template sensor in front, by
  the same rule that already applies to the switch entity.

## Before you arm anything

This is a pre-release. The main switch is off after setup and every load has its own
automation switch: add a load, watch it, and arm it once the numbers match what your
installation is actually doing.

**Known limitation:** the forecast is still not divided among several daily targets. With a
single daily target this does not occur.
