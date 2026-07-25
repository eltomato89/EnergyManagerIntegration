"""Gemeinsame Fixtures."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_manager.const import (
    CONF_GRID_ENTITY,
    CONF_METER_MODE,
    DOMAIN,
    METER_MODE_GRID,
)

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Macht custom_components/ überhaupt ladbar.

    Ohne diese Fixture findet HA die Integration im Test nicht — das ist der
    häufigste Grund für ein rätselhaftes "Integration not found".
    """
    return


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Ein eingerichteter Eintrag mit bidirektionalem Netzsensor."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Energy Manager",
        data={
            CONF_METER_MODE: METER_MODE_GRID,
            CONF_GRID_ENTITY: "sensor.netz",
        },
        options={},
        unique_id=DOMAIN,
    )
