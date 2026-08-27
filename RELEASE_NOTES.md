# 0.5.0b4 — pre-release

Batteries have become a category of their own. This is the largest change in the 0.5 series,
and the only one so far that alters behaviour on an existing installation — the section
"What actually changes for you" says exactly when.

**This release belongs together with card `0.7.0-beta.4`.** Everything about a single battery
moved from the hub to the battery's own device, so an older card finds none of it and shows no
battery row at all.

## Changed

- **A battery is a subentry of its own now**, added through "Add subentry" like a load and
  shown as its own device — and there may be several. It is still not a load: it is never
  switched, it has a state of charge, and its power is part of the surplus formula itself.
  That is why it gets its own type rather than a flag on the existing form; the two share no
  required field.

  **The migration runs by itself.** On the first start after the update an existing battery
  moves into a subentry named "Hausbatterie", its two controls move to the new device, and
  their **entity IDs are kept** — dashboards and automations keep working. The way back is not
  open this time: on an older version the battery would be unconfigured.

- **Several batteries are added up in the balance**, each with its own rule for how discharging
  counts. One may sit at the house connection and another behind a meter of its own — that is a
  property of the connection, not of the installation.

- **The charging-priority threshold is a claim in the order instead of a lock in the formula.**
  It used to cap the reported surplus at zero, so nobody got anything while the card said "no
  surplus" with the sun shining. A battery below its threshold now moves ahead of every load
  and claims there — and its row shows that it does.

- **The surplus sensor keeps only figures for the installation as a whole.** Everything about a
  single battery is on that battery's own sensor. The state of charge stays while there is
  exactly one: a combined figure would need the capacities, and 5 kWh at 20 % plus 10 kWh at
  80 % is not 50 %.

- The device models are now `Surplus control`, `Load` and `Battery`.

## What actually changes for you

Only installations that entered a **maximum charge power smaller than their surplus**, and only
while the battery is below its threshold: the loads now get the remainder the battery cannot
take anyway. Previously nobody got anything.

Without a charge power the battery still claims everything below its threshold — which is
exactly the old lock, except the surplus is now reported instead of being calculated away.

## Before you arm anything

This is a pre-release. The main switch is off after setup and every load has its own
automation switch: add a load, watch it, and arm it once the numbers match what your
installation is actually doing.

**Known limitation:** the forecast is still not divided among several daily targets. With a
single daily target this does not occur.
