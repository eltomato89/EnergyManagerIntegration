"""Konstanten der Energy-Manager-Integration.

Die Konfigurationsschlüssel sind bewusst identisch zu denen der Energy Manager
Card benannt. Wer beides einsetzt, soll dieselben Begriffe wiederfinden — und
eine Übernahme aus einer bestehenden Kartenkonfiguration bleibt ohne
Übersetzungsschicht möglich.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "energy_manager"

# Subentry-Typ für einen Verbraucher.
SUBENTRY_TYPE_CONSUMER: Final = "consumer"

# --- Zählerquellen ----------------------------------------------------------

CONF_METER_MODE: Final = "meter_mode"
METER_MODE_GRID: Final = "grid"
METER_MODE_SPLIT: Final = "split"

CONF_GRID_ENTITY: Final = "grid_entity"
CONF_INVERT_GRID: Final = "invert_grid"
CONF_PRODUCTION_ENTITY: Final = "production_entity"
CONF_CONSUMPTION_ENTITY: Final = "consumption_entity"
CONF_CONSUMPTION_INCLUDES_BATTERY: Final = "consumption_includes_battery"

# --- Batterie ---------------------------------------------------------------

CONF_BATTERY_SOC_ENTITY: Final = "battery_soc_entity"
CONF_BATTERY_POWER_ENTITY: Final = "battery_power_entity"
CONF_BATTERY_INVERT: Final = "battery_invert"
CONF_BATTERY_CHARGE_ENTITY: Final = "battery_charge_entity"
CONF_BATTERY_DISCHARGE_ENTITY: Final = "battery_discharge_entity"
CONF_BATTERY_MODE: Final = "battery_mode"
CONF_BATTERY_MIN_SOC: Final = "battery_min_soc"
CONF_BATTERY_RESERVE_W: Final = "battery_reserve_w"

BATTERY_MODE_CHARGE_ONLY: Final = "charge_only"
BATTERY_MODE_FULL: Final = "full"

# --- Regelung ---------------------------------------------------------------

CONF_SMOOTHING_WINDOW: Final = "smoothing_window"
CONF_SETTLE_TIME: Final = "settle_time"

# --- Verbraucher ------------------------------------------------------------

CONF_NAME: Final = "name"
CONF_SWITCH_ENTITY: Final = "switch_entity"
CONF_POWER_ENTITY: Final = "power_entity"
CONF_MIN_POWER: Final = "min_power"
CONF_MAX_POWER: Final = "max_power"
CONF_HYSTERESIS: Final = "hysteresis"
CONF_MIN_RUNTIME: Final = "min_runtime"
CONF_MIN_OFF_TIME: Final = "min_off_time"
CONF_TURN_ON_DELAY: Final = "turn_on_delay"
CONF_TURN_OFF_DELAY: Final = "turn_off_delay"
CONF_PRIORITY: Final = "priority"

# --- Vorgabewerte -----------------------------------------------------------

# s, Fenster des zeitgewichteten Mittels. Identisch zur Karte.
DEFAULT_SMOOTHING_WINDOW: Final = 60
# s, Beruhigungsfenster nach einer Schaltung. Verhindert, dass die Automatik
# auf die Wirkung ihrer eigenen Schaltung reagiert.
DEFAULT_SETTLE_TIME: Final = 60
# W, angenommener Bedarf, wenn weder min_power noch max_power noch eine
# gemessene Leistung vorliegt. Identisch zur Karte.
DEFAULT_REQUIRED_W: Final = 500
# Anteil des Bedarfs, ab dem ein Verbraucher als "fast bereit" gilt.
CLOSE_THRESHOLD_RATIO: Final = 0.8

# s, Takt für zeitabhängige Bedingungen, die ohne Sensoränderung ablaufen.
TICK_INTERVAL: Final = 10
# s, Sammelfenster für Zustandsänderungen. Bündelt die Ereigniskaskade, die
# eine eigene Schaltung auslöst.
DEBOUNCE_COOLDOWN: Final = 3.0

# --- Speicher ---------------------------------------------------------------

STORAGE_VERSION: Final = 1
STORAGE_MINOR_VERSION: Final = 1
# s, verzögertes Schreiben des Laufzeitzustands.
STORAGE_SAVE_DELAY: Final = 30

# --- Zustände ---------------------------------------------------------------

# Zustände, die HA als "nicht verwertbar" führt.
UNAVAILABLE_STATES: Final = frozenset({"unavailable", "unknown", "", "none", "None", "null"})

# Domains, deren Entitäten sich sinnvoll als Verbraucher schalten lassen.
SWITCHABLE_DOMAINS: Final = [
    "switch",
    "input_boolean",
    "light",
    "fan",
    "humidifier",
    "siren",
    "climate",
    "water_heater",
]
