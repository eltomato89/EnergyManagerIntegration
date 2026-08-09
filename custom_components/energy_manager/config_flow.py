"""Einrichtung und Konfiguration.

Verbraucher sind **Subentries**, nicht Einträge in einer Liste in
``entry.options``. Der Unterschied ist mehr als kosmetisch: Home Assistant
verknüpft jeden Subentry mit einem eigenen Gerät und räumt beides beim Löschen
selbst auf. Bei einer Liste in den Optionen müsste die Integration verwaiste
Geräte von Hand aus der Registry entfernen — eine bekannte Fehlerquelle.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback

from .const import (
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_MIN_SOC,
    CONF_BATTERY_SOC_ENTITY,
    CONF_METER_MODE,
    CONF_SWITCH_ENTITY,
    DOMAIN,
    METER_MODE_GRID,
    SUBENTRY_TYPE_CONSUMER,
)
from .models import has_battery
from .schemas import (
    BATTERY_SCHEMA,
    CONSUMER_SCHEMA,
    GRID_SCHEMA,
    METER_MODE_SCHEMA,
    OPTIONS_SCHEMA,
    SPLIT_SCHEMA,
    clean,
)


class EnergyManagerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Führt durch die Einrichtung."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        # Nur beim Neukonfigurieren gefüllt: der bisherige Stand, getrennt von
        # dem, was der Nutzer gerade einträgt. Getrennt, damit ein geleertes
        # Feld auch wirklich leer bleibt — läge beides im selben Dict, würde der
        # alte Wert das Löschen überschreiben.
        self._suggestions: dict[str, Any] = {}

    def _form(
        self,
        step_id: str,
        schema: vol.Schema,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Zeigt ein Formular; beim Neukonfigurieren mit den bisherigen Werten.

        Ohne die Vorbelegung käme jeder Folgeschritt leer daher: Wer nur die
        Ladeleistung nachtragen will, müsste Sensoren und Schalter erneut
        auswählen — und die Felder mit Vorgabewert (Entladeverhalten, Reserve)
        fielen dabei stillschweigend auf ihre Vorgabe zurück.
        """
        if self._suggestions:
            schema = self.add_suggested_values_to_schema(schema, self._suggestions)
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    def _finish(self) -> ConfigFlowResult:
        """Schließt den Ablauf ab — je nach Quelle anlegen oder aktualisieren.

        Home Assistant verbietet es, aus einem ``reconfigure``-Ablauf heraus
        einen neuen Eintrag anzulegen; der Aufruf wirft. In der Oberfläche kam
        das als „Unknown error occurred" an.

        Ohne ``reload``: Das Neuladen übernimmt der Update-Listener aus
        ``__init__``, der ohnehin an jeder Änderung hängt. Beides zusammen liefe
        auf zwei Reloads hinaus.
        """
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(self._get_reconfigure_entry(), data=self._data)
        return self.async_create_entry(title="Energy Manager", data=self._data)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Zählermodus wählen."""
        if user_input is not None:
            self._data.update(user_input)
            if user_input[CONF_METER_MODE] == METER_MODE_GRID:
                return await self.async_step_grid()
            return await self.async_step_split()

        return self.async_show_form(step_id="user", data_schema=METER_MODE_SCHEMA)

    async def async_step_grid(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Bidirektionaler Netzsensor."""
        if user_input is not None:
            self._data.update(clean(user_input))
            return await self.async_step_battery()

        return self._form("grid", GRID_SCHEMA)

    async def async_step_split(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Getrennte Sensoren für Erzeugung und Verbrauch."""
        if user_input is not None:
            self._data.update(clean(user_input))
            return await self.async_step_battery()

        return self._form("split", SPLIT_SCHEMA)

    async def async_step_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Hausbatterie — freiwillig, das Formular darf leer bleiben."""
        errors: dict[str, str] = {}

        if user_input is not None:
            geplant = {**self._data, **clean(user_input)}

            # Beide Angaben brauchen einen Messwert, um überhaupt zu wirken.
            # Ohne diese Prüfung stünde eine Zahl im Formular, die stillschweigend
            # nichts tut — dieselbe Art Fehler wie eine falsche Entität, nur
            # ohne jede Rückmeldung.
            if geplant.get(CONF_BATTERY_MAX_CHARGE_W) and not has_battery(geplant):
                errors[CONF_BATTERY_MAX_CHARGE_W] = "battery_entity_required"
            if geplant.get(CONF_BATTERY_MIN_SOC) is not None and not geplant.get(
                CONF_BATTERY_SOC_ENTITY
            ):
                errors[CONF_BATTERY_MIN_SOC] = "soc_entity_required"

            if not errors:
                self._data = geplant
                return self._finish()

            # Die Eingabe stehen lassen, statt sie gegen den alten Stand zu
            # ersetzen: Sonst verschwindet beim Fehler genau der Wert, den der
            # Nutzer gerade korrigieren soll.
            self._suggestions = {**self._suggestions, **user_input}

        return self._form("battery", BATTERY_SCHEMA, errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Erlaubt das nachträgliche Ändern der Sensoren."""
        self._suggestions = dict(self._get_reconfigure_entry().data)

        if user_input is not None:
            # Bewusst nicht auf den alten Daten aufgesetzt: Der Ablauf fragt
            # alle Felder erneut ab, vorbelegt mit dem bisherigen Stand. Was am
            # Ende steht, ist damit genau das, was in den Formularen stand —
            # auch der Wechsel des Zählermodus lässt keine Reste zurück.
            self._data = clean(user_input)
            if user_input[CONF_METER_MODE] == METER_MODE_GRID:
                return await self.async_step_grid()
            return await self.async_step_split()

        return self._form("reconfigure", METER_MODE_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Die Optionen bekommen einen eigenen Dialog."""
        # Bewusst ohne Argument: seit HA 2024.11 stellt die Basisklasse
        # config_entry selbst bereit, ein eigenes Zuweisen ist abgekündigt.
        return EnergyManagerOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Meldet an, dass Verbraucher als Subentries angelegt werden."""
        return {SUBENTRY_TYPE_CONSUMER: ConsumerSubentryFlowHandler}


class EnergyManagerOptionsFlow(OptionsFlow):
    """Regelungsparameter, die sich im Betrieb ändern lassen."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=clean(user_input))

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )


class ConsumerSubentryFlowHandler(ConfigSubentryFlow):
    """Legt einen Verbraucher an oder ändert ihn."""

    def _switch_belegt(self, switch_entity: str, eigener: str | None = None) -> bool:
        """Führt schon ein anderer Verbraucher dieselbe Schalt-Entität?

        Zwei Einträge für dasselbe Gerät verplant die Automatik doppelt: Sie
        rechnet den Bedarf zweimal ab, hält es für zweimal schaltbar und
        vergleicht seinen Zustand mit zwei getrennten Sperrzeiten. Das ist immer
        ein Versehen — meist eine verwechselte Entität, weil deren ID nicht zum
        Anzeigenamen passt.
        """
        for subentry_id, subentry in self._get_entry().subentries.items():
            if subentry_id == eigener:
                continue
            if subentry.subentry_type != SUBENTRY_TYPE_CONSUMER:
                continue
            if subentry.data.get(CONF_SWITCH_ENTITY) == switch_entity:
                return True
        return False

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            cleaned = clean(user_input)
            if self._switch_belegt(cleaned[CONF_SWITCH_ENTITY]):
                errors[CONF_SWITCH_ENTITY] = "switch_in_use"
            else:
                return self.async_create_entry(title=cleaned["name"], data=cleaned)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(CONSUMER_SCHEMA, user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}

        if user_input is not None:
            cleaned = clean(user_input)
            if self._switch_belegt(cleaned[CONF_SWITCH_ENTITY], subentry.subentry_id):
                errors[CONF_SWITCH_ENTITY] = "switch_in_use"
            else:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    data=cleaned,
                    title=cleaned["name"],
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                CONSUMER_SCHEMA, user_input or subentry.data
            ),
            errors=errors,
        )
