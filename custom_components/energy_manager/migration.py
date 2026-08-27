"""Wanderungen bestehender Konfigurationen.

Sie laufen beim Einrichten, bevor Koordinator und Plattformen entstehen — was
danach käme, sähe schon den halb umgezogenen Zustand.

Jede Wanderung hier ist so gebaut, dass ein zweiter Durchlauf nichts mehr tut:
Sie erkennt am Ergebnis, ob sie nötig ist, und nicht an einer Merkerzahl. Eine
Zahl, die versehentlich zurückgesetzt wird, ließe die Wanderung erneut laufen —
und beim Anlegen von Subentries hieße das jedes Mal ein weiteres Gerät.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_BATTERY_CHARGE_ENTITY,
    CONF_BATTERY_DISCHARGE_ENTITY,
    CONF_BATTERY_INVERT,
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_MODE,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_RESERVE_W,
    CONF_BATTERY_SOC_ENTITY,
    CONF_NAME,
    SUBENTRY_TYPE_BATTERY,
)
from .models import BatteryConfig

# Die Felder, die einen Speicher beschreiben. Sie behalten ihre Namen im
# Subentry, damit die Wanderung ein reines Kopieren ist.
#
# ``battery_min_soc`` steht bewusst **nicht** dabei: Er ist keine Beschreibung
# der Anlage, sondern eine Stellgröße, und wird weiterhin aus den Eintragsdaten
# übernommen, solange er dort liegt.
BATTERIE_FELDER: tuple[str, ...] = (
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_INVERT,
    CONF_BATTERY_CHARGE_ENTITY,
    CONF_BATTERY_DISCHARGE_ENTITY,
    CONF_BATTERY_MODE,
    CONF_BATTERY_RESERVE_W,
    CONF_BATTERY_MAX_CHARGE_W,
)

# Der Name, unter dem eine gewanderte Batterie erscheint. Er ist frei änderbar;
# er muss nur überhaupt einer sein, denn der Subentry trägt seinen Titel als
# Gerätenamen.
GEWANDERTER_NAME = "Hausbatterie"


@callback
def async_migrate_battery(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Hebt eine im Haupteintrag eingerichtete Batterie in einen Subentry.

    Bis 0.5.0b3 stand sie als Feldergruppe in den Eintragsdaten und es gab genau
    eine. Als Subentry kann es mehrere geben, und jeder trägt seine Stellgrößen
    an seinem eigenen Gerät.

    Zwei Fälle, und der zweite ist der unauffällige:

    * Es sind Batteriefelder eingetragen — dann entsteht daraus ein Subentry.
    * Es sind nur Reste eingetragen, etwa eine Ladeleistung ohne jeden Sensor.
      Daraus wird kein Speicher: Ohne Messwert ließe sich weder erkennen, ob er
      lädt, noch ob er voll ist. Die Reste werden trotzdem entfernt, sonst
      stünden sie für immer in den Daten und niemand käme je an sie heran.

    In beiden Fällen verlassen die Felder die Eintragsdaten. Bleiben sie liegen,
    legt der nächste Start einen weiteren Subentry an.
    """
    daten: dict[str, Any] = {
        schluessel: entry.data[schluessel]
        for schluessel in BATTERIE_FELDER
        if entry.data.get(schluessel) is not None
    }
    if not daten:
        return

    # Wer schon einen Speicher hat, ist gewandert. Die Felder trotzdem
    # wegräumen: Sie sind dann tot, und tote Felder in den Daten führen beim
    # nächsten Blick in die Konfiguration nur in die Irre.
    schon_gewandert = any(
        sub.subentry_type == SUBENTRY_TYPE_BATTERY for sub in entry.subentries.values()
    )

    if not schon_gewandert and BatteryConfig.from_subentry("probe", daten).has_reading:
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data={CONF_NAME: GEWANDERTER_NAME, **daten},
                subentry_type=SUBENTRY_TYPE_BATTERY,
                title=GEWANDERTER_NAME,
                unique_id=None,
            ),
        )

    hass.config_entries.async_update_entry(
        entry,
        data={
            schluessel: wert
            for schluessel, wert in entry.data.items()
            if schluessel not in BATTERIE_FELDER
        },
    )
