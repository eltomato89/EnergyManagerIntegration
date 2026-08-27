"""Einrichtung, Optionen und Verbraucher-Subentries."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_manager.const import (
    CONF_BATTERY_MAX_CHARGE_W,
    CONF_BATTERY_MIN_SOC,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CONSUMPTION_ENTITY,
    CONF_GRID_ENTITY,
    CONF_INVERT_GRID,
    CONF_METER_MODE,
    CONF_MIN_OFF_TIME,
    CONF_NAME,
    CONF_PRODUCTION_ENTITY,
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_SWITCH_ENTITY,
    DOMAIN,
    METER_MODE_GRID,
    METER_MODE_SPLIT,
    SUBENTRY_TYPE_BATTERY,
    SUBENTRY_TYPE_CONSUMER,
)


async def test_grid_flow(hass: HomeAssistant) -> None:
    """Einrichtung mit bidirektionalem Netzsensor."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_METER_MODE: METER_MODE_GRID}
    )
    assert result["step_id"] == "grid"

    # Der Zählerschritt ist der letzte: Ein Speicher ist ein eigener Subentry,
    # weil es mehr als einen geben darf.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_GRID_ENTITY: "sensor.netz", CONF_INVERT_GRID: False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_GRID_ENTITY] == "sensor.netz"
    assert result["data"][CONF_METER_MODE] == METER_MODE_GRID


async def test_split_flow(hass: HomeAssistant) -> None:
    """Einrichtung mit getrennten Sensoren für Erzeugung und Verbrauch."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_METER_MODE: METER_MODE_SPLIT}
    )
    assert result["step_id"] == "split"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_PRODUCTION_ENTITY: "sensor.pv",
            CONF_CONSUMPTION_ENTITY: "sensor.haus",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PRODUCTION_ENTITY] == "sensor.pv"
    assert result["data"][CONF_CONSUMPTION_ENTITY] == "sensor.haus"


async def test_nur_eine_instanz(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Ein zweiter Eintrag wird abgewiesen."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


class TestNeuKonfigurieren:
    """Der Ablauf endet mit einem aktualisierten, nicht mit einem neuen Eintrag.

    Home Assistant verbietet ``async_create_entry`` in einem
    ``reconfigure``-Ablauf und wirft. In der Oberfläche kam das als „Unknown
    error occurred" an — genau am letzten Schritt, dem Batterie-Formular.
    """

    async def test_aktualisiert_den_bestehenden_eintrag(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        mock_config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": mock_config_entry.entry_id,
            },
        )
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_METER_MODE: METER_MODE_GRID}
        )
        assert result["step_id"] == "grid"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_GRID_ENTITY: "sensor.netz_neu"}
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert len(hass.config_entries.async_entries(DOMAIN)) == 1
        assert mock_config_entry.data[CONF_GRID_ENTITY] == "sensor.netz_neu"

    async def test_formulare_sind_vorbelegt(self, hass: HomeAssistant) -> None:
        """Sonst müsste jedes Feld erneut ausgefüllt werden, um eines zu ändern."""
        entry = _eintrag()
        entry.add_to_hass(hass)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert _suggested(result, CONF_METER_MODE) == METER_MODE_GRID

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_METER_MODE: METER_MODE_GRID}
        )
        assert _suggested(result, CONF_GRID_ENTITY) == "sensor.netz"


def _eintrag(**extra) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Energy Manager",
        data={
            CONF_METER_MODE: METER_MODE_GRID,
            CONF_GRID_ENTITY: "sensor.netz",
            **extra,
        },
        options={},
        unique_id=DOMAIN,
    )


def _suggested(result, key: str):
    """Der vorbelegte Wert eines Feldes im gezeigten Formular."""
    for marker in result["data_schema"].schema:
        if marker == key:
            return (marker.description or {}).get("suggested_value")
    raise AssertionError(f"Feld {key} nicht im Formular")


async def test_options_flow(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Regelungsparameter lassen sich nachträglich ändern."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SMOOTHING_WINDOW: 90, CONF_SETTLE_TIME: 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SMOOTHING_WINDOW] == 90


async def test_verbraucher_anlegen(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Ein Verbraucher wird als Subentry angelegt."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, SUBENTRY_TYPE_CONSUMER),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Wallbox",
            CONF_SWITCH_ENTITY: "switch.wallbox",
            CONF_MIN_OFF_TIME: 600,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Wallbox"

    await hass.async_block_till_done()
    subentries = list(mock_config_entry.subentries.values())
    assert len(subentries) == 1
    assert subentries[0].data[CONF_SWITCH_ENTITY] == "switch.wallbox"
    assert subentries[0].data[CONF_MIN_OFF_TIME] == 600


async def test_verbraucher_bearbeiten(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Ein vorhandener Verbraucher lässt sich ändern."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, SUBENTRY_TYPE_CONSUMER),
        context={"source": config_entries.SOURCE_USER},
    )
    await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_NAME: "Wallbox", CONF_SWITCH_ENTITY: "switch.wallbox"},
    )
    await hass.async_block_till_done()

    subentry_id = next(iter(mock_config_entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, SUBENTRY_TYPE_CONSUMER),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_NAME: "Wallbox Garage", CONF_SWITCH_ENTITY: "switch.wallbox"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    await hass.async_block_till_done()
    assert mock_config_entry.subentries[subentry_id].title == "Wallbox Garage"


class TestDoppelteSchaltEntitaet:
    """Zwei Verbraucher für dasselbe Gerät sind immer ein Versehen.

    Meist eine verwechselte Entität, weil deren ID nicht zum Anzeigenamen passt.
    Die Automatik verplant das Gerät dann doppelt: rechnet den Bedarf zweimal
    ab, hält es für zweimal schaltbar und führt zwei getrennte Sperrzeiten.
    """

    async def test_anlegen_wird_abgelehnt(self, hass: HomeAssistant) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
            unique_id=DOMAIN,
            subentries_data=[
                ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_CONSUMER,
                    title="Erster",
                    unique_id=None,
                    data={CONF_NAME: "Erster", CONF_SWITCH_ENTITY: "switch.geteilt"},
                )
            ],
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_CONSUMER), context={"source": "user"}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_NAME: "Zweiter", CONF_SWITCH_ENTITY: "switch.geteilt"},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {CONF_SWITCH_ENTITY: "switch_in_use"}

    async def test_ein_freies_geraet_geht_durch(self, hass: HomeAssistant) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
            unique_id=DOMAIN,
            subentries_data=[
                ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_CONSUMER,
                    title="Erster",
                    unique_id=None,
                    data={CONF_NAME: "Erster", CONF_SWITCH_ENTITY: "switch.eins"},
                )
            ],
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_CONSUMER), context={"source": "user"}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_NAME: "Zweiter", CONF_SWITCH_ENTITY: "switch.zwei"},
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY

    async def test_der_eigene_eintrag_blockiert_sich_nicht(self, hass: HomeAssistant) -> None:
        """Beim Bearbeiten darf die eigene Entität bleiben."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_METER_MODE: METER_MODE_GRID, CONF_GRID_ENTITY: "sensor.netz"},
            unique_id=DOMAIN,
            subentries_data=[
                ConfigSubentryData(
                    subentry_type=SUBENTRY_TYPE_CONSUMER,
                    title="Pumpe",
                    unique_id=None,
                    data={CONF_NAME: "Pumpe", CONF_SWITCH_ENTITY: "switch.pumpe"},
                )
            ],
        )
        entry.add_to_hass(hass)
        subentry = next(iter(entry.subentries.values()))

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_CONSUMER),
            context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_NAME: "Pumpe neu", CONF_SWITCH_ENTITY: "switch.pumpe"},
        )

        assert result["type"] is FlowResultType.ABORT


class TestNullwerte:
    async def test_null_bei_der_nennleistung_wird_nicht_gespeichert(
        self, hass: HomeAssistant
    ) -> None:
        """Sie sähe im Attribut wie eine Angabe aus, wirkt aber nicht."""
        from custom_components.energy_manager.const import CONF_HYSTERESIS, CONF_MAX_POWER
        from custom_components.energy_manager.schemas import clean

        gereinigt = clean(
            {
                CONF_NAME: "X",
                CONF_SWITCH_ENTITY: "switch.x",
                CONF_MAX_POWER: 0,
                # Hier bedeutet 0 "aus" und gehört gespeichert.
                CONF_HYSTERESIS: 0,
            }
        )
        assert CONF_MAX_POWER not in gereinigt
        assert gereinigt[CONF_HYSTERESIS] == 0


class TestSpeicherAnlegen:
    """Der Speicher als eigener Subentry-Typ.

    Er ist kein Verbraucher: keine Schalt-Entität, dafür ein Ladestand, und
    seine Leistung steht in der Überschussformel selbst. Die beiden Formulare
    teilen kein einziges Pflichtfeld — deshalb zwei Typen und nicht einer mit
    Umschalter.
    """

    async def _setup(self, hass: HomeAssistant, entry: MockConfigEntry) -> None:
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    async def _formular(self, hass: HomeAssistant, entry: MockConfigEntry):
        """Startet den Ablauf; mehrfach aufrufbar für mehrere Speicher."""
        return await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_BATTERY),
            context={"source": config_entries.SOURCE_USER},
        )

    async def test_wird_angelegt(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        await self._setup(hass, mock_config_entry)
        result = await self._formular(hass, mock_config_entry)
        assert result["type"] is FlowResultType.FORM

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Keller",
                CONF_BATTERY_SOC_ENTITY: "sensor.akku_soc",
                CONF_BATTERY_POWER_ENTITY: "sensor.akku_leistung",
                CONF_BATTERY_MAX_CHARGE_W: 5000,
            },
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "Keller"

        speicher = [
            s
            for s in mock_config_entry.subentries.values()
            if s.subentry_type == SUBENTRY_TYPE_BATTERY
        ]
        assert len(speicher) == 1
        assert speicher[0].data[CONF_BATTERY_SOC_ENTITY] == "sensor.akku_soc"

    async def test_zwei_speicher_nebeneinander(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Der ganze Zweck des Umbaus: Es darf mehr als einen geben.

        Anders als bei Verbrauchern gibt es keine Prüfung auf doppelte
        Entitäten — zwei Speicher am selben Sensor wären ein Versehen, aber
        eines, das sich nicht von einer Anlage mit zwei Wechselrichtern am
        selben Summenzähler unterscheiden lässt.
        """
        await self._setup(hass, mock_config_entry)

        for name, sensor in (("Keller", "sensor.keller_soc"), ("Garage", "sensor.garage_soc")):
            result = await self._formular(hass, mock_config_entry)
            result = await hass.config_entries.subentries.async_configure(
                result["flow_id"], {CONF_NAME: name, CONF_BATTERY_SOC_ENTITY: sensor}
            )
            await hass.async_block_till_done()
            assert result["type"] is FlowResultType.CREATE_ENTRY

        speicher = [
            s
            for s in mock_config_entry.subentries.values()
            if s.subentry_type == SUBENTRY_TYPE_BATTERY
        ]
        assert [s.title for s in speicher] == ["Keller", "Garage"]

    async def test_ohne_messwert_wird_abgelehnt(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Ein Eintrag aus lauter Zahlen wäre eine Zeile, hinter der nichts steht.

        Ohne Messwert lässt sich weder erkennen, ob der Speicher lädt, noch ob
        er voll ist — und beides braucht die Reihenfolge.
        """
        await self._setup(hass, mock_config_entry)
        result = await self._formular(hass, mock_config_entry)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_NAME: "Luftschloss", CONF_BATTERY_MAX_CHARGE_W: 5000}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {CONF_BATTERY_POWER_ENTITY: "battery_entity_required"}

    async def test_leere_felder_werden_nicht_gespeichert(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Ein leeres Formularfeld ist etwas anderes als ein gespeicherter Leerwert.

        Gespeichert höbe er die Vorgabewerte auf und stünde für immer in den
        Daten, ohne dass ihn je jemand einträgt.
        """
        await self._setup(hass, mock_config_entry)
        result = await self._formular(hass, mock_config_entry)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_NAME: "Keller", CONF_BATTERY_SOC_ENTITY: "sensor.akku_soc"},
        )
        await hass.async_block_till_done()

        gespeichert = next(
            s.data
            for s in mock_config_entry.subentries.values()
            if s.subentry_type == SUBENTRY_TYPE_BATTERY
        )
        assert CONF_BATTERY_POWER_ENTITY not in gespeichert

    async def test_geleertes_feld_bleibt_leer(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Vorbelegte Felder müssen sich auch wieder abwählen lassen."""
        await self._setup(hass, mock_config_entry)
        result = await self._formular(hass, mock_config_entry)
        await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Keller",
                CONF_BATTERY_SOC_ENTITY: "sensor.akku_soc",
                CONF_BATTERY_POWER_ENTITY: "sensor.akku_leistung",
            },
        )
        await hass.async_block_till_done()

        speicher = next(
            s
            for s in mock_config_entry.subentries.values()
            if s.subentry_type == SUBENTRY_TYPE_BATTERY
        )
        result = await hass.config_entries.subentries.async_init(
            (mock_config_entry.entry_id, SUBENTRY_TYPE_BATTERY),
            context={"source": "reconfigure", "subentry_id": speicher.subentry_id},
        )
        assert _suggested(result, CONF_BATTERY_POWER_ENTITY) == "sensor.akku_leistung"

        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_NAME: "Keller", CONF_BATTERY_SOC_ENTITY: "sensor.akku_soc"},
        )
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert CONF_BATTERY_POWER_ENTITY not in speicher.data

    async def test_der_mindestladestand_steht_nicht_im_formular(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Er ist eine Vorgabe, die sich mit dem Tag ändert, keine Beschreibung
        der Anlage — und liegt deshalb hinter einer eigenen Entität.

        Stünde er hier, gäbe es zwei Orte für denselben Wert, und wer ihn im
        Formular änderte, sähe nichts geschehen.
        """
        await self._setup(hass, mock_config_entry)
        result = await self._formular(hass, mock_config_entry)

        assert CONF_BATTERY_MIN_SOC not in result["data_schema"].schema

    async def test_die_eingabe_bleibt_nach_dem_fehler_stehen(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Sonst verschwindet genau der Wert, der korrigiert werden soll."""
        await self._setup(hass, mock_config_entry)
        result = await self._formular(hass, mock_config_entry)
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_NAME: "Luftschloss", CONF_BATTERY_MAX_CHARGE_W: 5000}
        )

        assert _suggested(result, CONF_BATTERY_MAX_CHARGE_W) == 5000
