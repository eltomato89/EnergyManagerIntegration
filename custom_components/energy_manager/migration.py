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

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

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
    DOMAIN,
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


# Die beiden Stellgrößen, die am Hub hingen. Links der Schlüssel von damals,
# rechts der von heute — am Speichergerät braucht keiner mehr ein Präfix.
STELLGROESSEN: tuple[tuple[str, str], ...] = (
    ("battery_priority", "priority"),
    ("battery_min_soc", "min_soc"),
)


@callback
def async_migrate_battery_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Hängt die Stellgrößen der Batterie an ihr neues Gerät.

    Die Entitäts-ID bleibt dabei erhalten — und darauf kommt es an: Sie steht in
    Dashboards, Automatisierungen und Verlaufsdaten. Home Assistant leitet sie
    nur beim ersten Anlegen aus dem Namen ab; danach ist sie ein eigener Wert
    und überlebt sowohl den Gerätewechsel als auch die neue ``unique_id``.

    Ohne diesen Schritt entstünde beim Einrichten eine zweite Entität — die alte
    ``unique_id`` findet keiner mehr, die neue ist frei, und weil die schöne
    Entitäts-ID noch belegt ist, hinge an der neuen ein ``_2``. Der Nutzer
    behielte einen toten Eintrag und verlöre jede Verknüpfung.
    """
    speicher = next(
        (sub for sub in entry.subentries.values() if sub.subentry_type == SUBENTRY_TYPE_BATTERY),
        None,
    )
    if speicher is None:
        return

    registry = er.async_get(hass)

    for alt, neu in STELLGROESSEN:
        entity_id = registry.async_get_entity_id(NUMBER_DOMAIN, DOMAIN, f"{entry.entry_id}_{alt}")
        if entity_id is None:
            continue

        neue_kennung = f"{speicher.subentry_id}_{neu}"

        # Ist die neue Kennung schon vergeben, würde das Umschreiben hart
        # werfen und die Einrichtung abbrechen. Das passiert genau einem
        # Nutzer: dem, der die Fassung ohne diesen Schritt schon geladen hat und
        # nun eine ``_2``-Entität besitzt. Ihm ist mit einem Abbruch nicht
        # geholfen — der alte Eintrag bleibt dann eben liegen.
        if registry.async_get_entity_id(NUMBER_DOMAIN, DOMAIN, neue_kennung):
            continue

        registry.async_update_entity(
            entity_id,
            # Beides zusammen: Ohne ``config_entry_id`` prüft Home Assistant
            # gar nicht, ob es den Subentry überhaupt gibt, und schriebe eine
            # falsche Zuordnung wortlos fort.
            config_entry_id=entry.entry_id,
            config_subentry_id=speicher.subentry_id,
            new_unique_id=neue_kennung,
        )
