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
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_MIN_SOC,
    CONF_BATTERY_MODE,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_RESERVE_W,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CONSUMER_TYPE,
    CONF_CONSUMPTION_ENTITY,
    CONF_CONSUMPTION_INCLUDES_BATTERY,
    CONF_CONTROL_ENTITY,
    CONF_GRID_ENTITY,
    CONF_HYSTERESIS,
    CONF_INVERT_GRID,
    CONF_LEVEL_HOLD,
    CONF_MANUAL_OVERRIDE_TIME,
    CONF_MAX_POWER,
    CONF_METER_MODE,
    CONF_MIN_LEVEL_W,
    CONF_MIN_OFF_TIME,
    CONF_MIN_POWER,
    CONF_MIN_RUNTIME,
    CONF_NAME,
    CONF_PHASES,
    CONF_POWER_ENTITY,
    CONF_PRODUCTION_ENTITY,
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    CONF_TURN_OFF_DELAY,
    CONF_TURN_ON_DELAY,
    CONSUMER_TYPE_MODULATING,
    CONSUMER_TYPE_SWITCH,
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
        vol.Optional(CONF_BATTERY_MAX_CHARGE_W, default=0): watts(20000),
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
        # Der Verhaltenstyp steht im ersten Schritt, weil er entscheidet, ob ein
        # zweiter folgt. Vorgabe ist der bisherige Fall — wer nichts umstellt,
        # bekommt dasselbe Formular wie zuvor.
        vol.Required(CONF_CONSUMER_TYPE, default=CONSUMER_TYPE_SWITCH): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[CONSUMER_TYPE_SWITCH, CONSUMER_TYPE_MODULATING],
                translation_key="consumer_type",
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
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
        # 0 bedeutet hier "aus" und gehört gespeichert — wie bei den vier
        # Zeitfeldern und anders als bei der Nennleistung.
        vol.Optional(CONF_MANUAL_OVERRIDE_TIME, default=0): seconds(86400, 60),
    }
)


# --- Regelbare Verbraucher --------------------------------------------------

CONTROL_SCHEMA = vol.Schema(
    {
        # Nur number und select: Beide beschreiben ihr Raster selbst — die eine
        # über min/max/step und ihre Einheit, die andere über ihre Optionen.
        vol.Required(CONF_CONTROL_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["number", "select"])
        ),
        vol.Required(CONF_PHASES, default="1"): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["1", "3"],
                translation_key="phases",
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
        vol.Optional(CONF_MIN_LEVEL_W): watts(),
        vol.Optional(CONF_LEVEL_HOLD, default=0): seconds(3600, 10),
    }
)


def levels_schema(options: list[str], current: dict[str, float] | None = None) -> vol.Schema:
    """Ein Watt-Feld je Option einer Auswahlliste.

    Zur Laufzeit gebaut, weil die Optionen erst mit der gewählten Entität
    bekannt sind. Die Feldnamen sind die **rohen** Optionsschlüssel: Home
    Assistant übersetzt die angezeigten Bezeichnungen, und die wechseln mit der
    Sprache der Instanz — gespeichert werden muss der Schlüssel.

    Eine Option mit 0 W ist die Aus-Stellung und wird keine Stufe; abgeschaltet
    wird über die Schalt-Entität.
    """
    vorher = current or {}
    return vol.Schema(
        {
            vol.Optional(option, default=float(vorher.get(option, 0))): watts(30000, 50)
            for option in options
        }
    )


# Bei diesen Feldern ist 0 keine Angabe, sondern das Fehlen einer. Anders als
# bei `hysteresis` oder den Zeitfeldern, wo 0 "aus" bedeutet und gespeichert
# gehört.
_NULL_IST_LEER = (
    CONF_MIN_POWER,
    CONF_MAX_POWER,
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_MIN_LEVEL_W,
)


def clean(data: dict[str, Any]) -> dict[str, Any]:
    """Entfernt leere Werte, statt sie zu speichern.

    Die Formulare liefern geleerte Felder als ``None`` oder leeren String
    zurück. Gespeichert wären sie etwas anderes als "nicht gesetzt" und würden
    die Vorgabewerte aushebeln.

    Eine 0 bei der Nennleistung ist derselbe Fall: Sie sähe im Attribut wie eine
    Angabe aus, wirkt aber nicht — die Bedarfsermittlung überspringt sie.
    """
    return {
        k: v
        for k, v in data.items()
        if v is not None and v != "" and not (k in _NULL_IST_LEER and v == 0)
    }
