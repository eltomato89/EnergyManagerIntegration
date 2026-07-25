"""Formularschemata für Einrichtung und Optionen.

Durchgängig ``selector``-basiert statt roher voluptuous-Typen: nur so entstehen
echte Bedienelemente in der Oberfläche — Entitätsauswahl mit Filter, Schieber
mit Einheit, Auswahllisten mit Übersetzung.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.helpers import selector

from .const import (
    BATTERY_MODE_CHARGE_ONLY,
    BATTERY_MODE_FULL,
    CONF_BATTERY_CHARGE_ENTITY,
    CONF_BATTERY_DISCHARGE_ENTITY,
    CONF_BATTERY_INVERT,
    CONF_BATTERY_MIN_SOC,
    CONF_BATTERY_MODE,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_RESERVE_W,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CONSUMPTION_ENTITY,
    CONF_CONSUMPTION_INCLUDES_BATTERY,
    CONF_GRID_ENTITY,
    CONF_HYSTERESIS,
    CONF_INVERT_GRID,
    CONF_MAX_POWER,
    CONF_METER_MODE,
    CONF_MIN_OFF_TIME,
    CONF_MIN_POWER,
    CONF_MIN_RUNTIME,
    CONF_NAME,
    CONF_POWER_ENTITY,
    CONF_PRODUCTION_ENTITY,
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    CONF_TURN_OFF_DELAY,
    CONF_TURN_ON_DELAY,
    DEFAULT_SETTLE_TIME,
    DEFAULT_SMOOTHING_WINDOW,
    METER_MODE_GRID,
    METER_MODE_SPLIT,
    SWITCHABLE_DOMAINS,
)

# --- Bausteine --------------------------------------------------------------

POWER_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="power")
)

BATTERY_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="battery")
)

SWITCHABLE = selector.EntitySelector(selector.EntitySelectorConfig(domain=list(SWITCHABLE_DOMAINS)))


def watts(maximum: int = 30000, step: int = 50) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=maximum,
            step=step,
            unit_of_measurement="W",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def seconds(maximum: int = 86400, step: int = 10) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=maximum,
            step=step,
            unit_of_measurement="s",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


# --- Einrichtung ------------------------------------------------------------

METER_MODE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_METER_MODE, default=METER_MODE_GRID): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[METER_MODE_GRID, METER_MODE_SPLIT],
                translation_key="meter_mode",
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
    }
)

GRID_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_GRID_ENTITY): POWER_SENSOR,
        vol.Optional(CONF_INVERT_GRID, default=False): selector.BooleanSelector(),
    }
)

SPLIT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PRODUCTION_ENTITY): POWER_SENSOR,
        vol.Required(CONF_CONSUMPTION_ENTITY): POWER_SENSOR,
        vol.Optional(CONF_CONSUMPTION_INCLUDES_BATTERY, default=False): selector.BooleanSelector(),
    }
)

BATTERY_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_BATTERY_SOC_ENTITY): BATTERY_SENSOR,
        vol.Optional(CONF_BATTERY_POWER_ENTITY): POWER_SENSOR,
        vol.Optional(CONF_BATTERY_INVERT, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_BATTERY_CHARGE_ENTITY): POWER_SENSOR,
        vol.Optional(CONF_BATTERY_DISCHARGE_ENTITY): POWER_SENSOR,
        vol.Optional(CONF_BATTERY_MODE, default=BATTERY_MODE_CHARGE_ONLY): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[BATTERY_MODE_CHARGE_ONLY, BATTERY_MODE_FULL],
                translation_key="battery_mode",
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
        vol.Optional(CONF_BATTERY_MIN_SOC): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                unit_of_measurement="%",
                mode=selector.NumberSelectorMode.SLIDER,
            )
        ),
        vol.Optional(CONF_BATTERY_RESERVE_W, default=0): watts(20000),
    }
)


# --- Optionen ---------------------------------------------------------------

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_SMOOTHING_WINDOW, default=DEFAULT_SMOOTHING_WINDOW
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=900,
                step=5,
                unit_of_measurement="s",
                mode=selector.NumberSelectorMode.SLIDER,
            )
        ),
        vol.Optional(CONF_SETTLE_TIME, default=DEFAULT_SETTLE_TIME): seconds(600, 10),
    }
)


# --- Verbraucher ------------------------------------------------------------

CONSUMER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): selector.TextSelector(),
        vol.Required(CONF_SWITCH_ENTITY): SWITCHABLE,
        vol.Optional(CONF_POWER_ENTITY): POWER_SENSOR,
        vol.Optional(CONF_MIN_POWER): watts(),
        vol.Optional(CONF_MAX_POWER): watts(),
        vol.Optional(CONF_HYSTERESIS, default=0): watts(5000, 25),
        # Die vier Zeitfelder greifen an unterschiedlichen Stellen und ersetzen
        # einander nicht; die Erklärung steht in den Übersetzungen.
        vol.Optional(CONF_TURN_ON_DELAY, default=0): seconds(3600, 10),
        vol.Optional(CONF_TURN_OFF_DELAY, default=0): seconds(3600, 10),
        vol.Optional(CONF_MIN_RUNTIME, default=0): seconds(86400, 60),
        vol.Optional(CONF_MIN_OFF_TIME, default=0): seconds(86400, 60),
    }
)


def clean(data: dict[str, Any]) -> dict[str, Any]:
    """Entfernt leere Werte, statt sie zu speichern.

    Die Formulare liefern geleerte Felder als ``None`` oder leeren String
    zurück. Gespeichert wären sie etwas anderes als "nicht gesetzt" und würden
    die Vorgabewerte aushebeln.
    """
    return {k: v for k, v in data.items() if v is not None and v != ""}
