"""Einrichtungsdialog.

Schritt 0 legt nur das Nötigste an, damit sich die Integration überhaupt
einrichten lässt. Der vollständige Dialog samt Verbraucher-Subentries folgt in
Schritt 2 des Plans.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .const import CONF_GRID_ENTITY, CONF_METER_MODE, DOMAIN, METER_MODE_GRID

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_GRID_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="power"),
        ),
    }
)


class EnergyManagerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Führt durch die Einrichtung."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Erster und vorerst einziger Schritt."""
        if user_input is not None:
            return self.async_create_entry(
                title="Energy Manager",
                data={CONF_METER_MODE: METER_MODE_GRID, **user_input},
            )

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)
