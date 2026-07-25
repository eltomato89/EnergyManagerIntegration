"""Einrichtung und Konfiguration.

Verbraucher sind **Subentries**, nicht Einträge in einer Liste in
``entry.options``. Der Unterschied ist mehr als kosmetisch: Home Assistant
verknüpft jeden Subentry mit einem eigenen Gerät und räumt beides beim Löschen
selbst auf. Bei einer Liste in den Optionen müsste die Integration verwaiste
Geräte von Hand aus der Registry entfernen — eine bekannte Fehlerquelle.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback

from .const import (
    CONF_METER_MODE,
    DOMAIN,
    METER_MODE_GRID,
    SUBENTRY_TYPE_CONSUMER,
)
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

        return self.async_show_form(step_id="grid", data_schema=GRID_SCHEMA)

    async def async_step_split(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Getrennte Sensoren für Erzeugung und Verbrauch."""
        if user_input is not None:
            self._data.update(clean(user_input))
            return await self.async_step_battery()

        return self.async_show_form(step_id="split", data_schema=SPLIT_SCHEMA)

    async def async_step_battery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Hausbatterie — freiwillig, das Formular darf leer bleiben."""
        if user_input is not None:
            self._data.update(clean(user_input))
            return self.async_create_entry(title="Energy Manager", data=self._data)

        return self.async_show_form(step_id="battery", data_schema=BATTERY_SCHEMA)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Erlaubt das nachträgliche Ändern der Sensoren."""
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            self._data = {**entry.data, **clean(user_input)}
            if user_input[CONF_METER_MODE] == METER_MODE_GRID:
                return await self.async_step_grid()
            return await self.async_step_split()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(METER_MODE_SCHEMA, entry.data),
        )

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

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            cleaned = clean(user_input)
            return self.async_create_entry(title=cleaned["name"], data=cleaned)

        return self.async_show_form(step_id="user", data_schema=CONSUMER_SCHEMA)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()

        if user_input is not None:
            cleaned = clean(user_input)
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                data=cleaned,
                title=cleaned["name"],
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(CONSUMER_SCHEMA, subentry.data),
        )
