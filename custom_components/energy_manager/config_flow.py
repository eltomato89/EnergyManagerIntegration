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
    CONF_CONSUMER_TYPE,
    CONF_CONTROL_ENTITY,
    CONF_LEVEL_MAP,
    CONF_METER_MODE,
    CONF_MIN_LEVEL_W,
    CONF_NAME,
    CONF_PHASES,
    CONF_SWITCH_ENTITY,
    CONSUMER_TYPE_MODULATING,
    DOMAIN,
    METER_MODE_GRID,
    SUBENTRY_TYPE_CONSUMER,
)
from .ladder import build_ladder, control_kind, describe
from .models import ConsumerConfig, has_battery
from .schemas import (
    BATTERY_SCHEMA,
    CONSUMER_SCHEMA,
    CONTROL_SCHEMA,
    GRID_SCHEMA,
    METER_MODE_SCHEMA,
    OPTIONS_SCHEMA,
    SPLIT_SCHEMA,
    clean,
    levels_schema,
)
from .units import is_unavailable


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
    """Legt einen Verbraucher an oder ändert ihn.

    Zweistufig, sobald der Verbraucher regelbar ist: Erst die Angaben, die für
    jeden gelten, dann die Steuerentität. Bei einer Auswahlliste kommt ein
    dritter Schritt für die Watt-Zuordnung dazu — er lässt sich nicht vorab
    bauen, weil die Optionen erst mit der gewählten Entität bekannt sind.

    Ein **Feld** statt eines eigenen Subentry-Typs: Der ist unveränderlich, und
    ein Wechsel von schaltbar auf regelbar würde Rang, Verlauf und
    Entitäts-IDs kosten.
    """

    def __init__(self) -> None:
        # Über die Schritte hinweg gesammelt. Bewusst nicht auf den bisherigen
        # Daten aufgesetzt: Was am Ende steht, ist genau das, was in den
        # Formularen stand — so lässt sich ein regelbarer Verbraucher auch wieder
        # zu einem schaltbaren machen, ohne Reste zurückzulassen.
        self._data: dict[str, Any] = {}
        self._subentry: Any = None
        # Nur beim Bearbeiten gefüllt: der bisherige Stand. Getrennt von
        # ``_data``, damit ein geleertes Feld auch leer bleibt — er dient
        # ausschließlich der Vorbelegung und der Beschreibung, nie dem Speichern.
        self._vorher: dict[str, Any] = {}

    # -- Schritte ------------------------------------------------------------

    def _finish(self) -> SubentryFlowResult:
        titel = self._data[CONF_NAME]
        if self._subentry is not None:
            return self.async_update_and_abort(
                self._get_entry(), self._subentry, data=self._data, title=titel
            )
        return self.async_create_entry(title=titel, data=self._data)

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
        return await self._grundangaben("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._subentry = self._get_reconfigure_subentry()
        self._vorher = dict(self._subentry.data)
        return await self._grundangaben("reconfigure", user_input)

    async def _grundangaben(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        """Die Angaben, die für jeden Verbraucher gelten."""
        errors: dict[str, str] = {}
        eigener = self._subentry.subentry_id if self._subentry is not None else None

        if user_input is not None:
            cleaned = clean(user_input)
            if self._switch_belegt(cleaned[CONF_SWITCH_ENTITY], eigener):
                errors[CONF_SWITCH_ENTITY] = "switch_in_use"
            else:
                self._data = cleaned
                if cleaned.get(CONF_CONSUMER_TYPE) == CONSUMER_TYPE_MODULATING:
                    return await self.async_step_control()
                return self._finish()

        vorbelegung = user_input or (self._subentry.data if self._subentry is not None else {})
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(CONSUMER_SCHEMA, vorbelegung),
            errors=errors,
        )

    async def async_step_control(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Die Steuerentität, über die die Stufe gestellt wird.

        Geprüft wird streng: Was sich nicht in ein Raster übersetzen lässt, wird
        abgewiesen statt angenommen. Bei einer Stellgröße in Ampere ohne Einheit
        entstünde sonst eine Leiter von 6 bis 16 **Watt** — sie passt in jeden
        Überschuss, und die Automatik schriebe 16, während das Gerät 16 A zieht.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            eingabe = clean(user_input)
            # Die Auswahlliste liefert Zeichenketten; gespeichert wird eine Zahl.
            if CONF_PHASES in eingabe:
                eingabe[CONF_PHASES] = int(eingabe[CONF_PHASES])
            geplant = {**self._data, **eingabe}

            entity_id = geplant[CONF_CONTROL_ENTITY]
            if entity_id.split(".", 1)[0] == "select":
                # Eine Auswahlliste hat keine Einheit — ohne Zuordnung ist nichts
                # zu prüfen. Die kommt im nächsten Schritt.
                self._data = geplant
                return await self.async_step_levels()

            if (fehler := self._pruefe_number(geplant)) is not None:
                # Der Fehler gehört an das Feld, das ihn verursacht hat.
                feld = CONF_MIN_LEVEL_W if fehler == "min_level_too_high" else CONF_CONTROL_ENTITY
                errors[feld] = fehler
            else:
                self._data = geplant
                return self._finish()

            self._data = geplant

        return self.async_show_form(
            step_id="control",
            data_schema=self.add_suggested_values_to_schema(
                CONTROL_SCHEMA, self._control_vorbelegung(user_input)
            ),
            errors=errors,
            description_placeholders={"detected": self._erkannt()},
        )

    async def async_step_levels(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Watt je Option einer Auswahlliste.

        Der einzige Schritt, in dem tatsächlich etwas eingetragen werden muss:
        Eine Auswahlliste sagt nichts über Leistung, und geraten wird hier nicht.
        """
        entity_id = self._data[CONF_CONTROL_ENTITY]
        state = self.hass.states.get(entity_id)
        options = list(state.attributes.get("options") or []) if state is not None else []
        if not options:
            # Ohne geladene Entität gibt es keine Optionen, für die man ein Feld
            # anbieten könnte.
            return self.async_show_form(
                step_id="control",
                data_schema=self.add_suggested_values_to_schema(CONTROL_SCHEMA, self._data),
                errors={CONF_CONTROL_ENTITY: "select_not_ready"},
                description_placeholders={"detected": self._erkannt()},
            )

        errors: dict[str, str] = {}
        if user_input is not None:
            zuordnung = {
                option: float(watt) for option, watt in user_input.items() if float(watt) > 0
            }
            geplant = {**self._data, CONF_LEVEL_MAP: zuordnung}
            if (fehler := self._pruefe_leiter(geplant)) is not None:
                errors["base"] = fehler
            else:
                self._data = geplant
                return self._finish()

        return self.async_show_form(
            step_id="levels",
            data_schema=levels_schema(options, self._mit_vorher().get(CONF_LEVEL_MAP)),
            errors=errors,
            description_placeholders={"entity": entity_id},
        )

    # -- Prüfungen -----------------------------------------------------------

    def _probe(self, data: dict[str, Any]) -> ConsumerConfig:
        """Ein Verbraucher aus den bisher gesammelten Angaben, nur zum Prüfen."""
        return ConsumerConfig.from_subentry("probe", {**data, CONF_NAME: data.get(CONF_NAME, "")})

    def _pruefe_number(self, data: dict[str, Any]) -> str | None:
        """Warum sich aus dieser ``number`` kein Raster ergibt."""
        state = self.hass.states.get(data[CONF_CONTROL_ENTITY])
        if state is None or is_unavailable(state):
            return "control_entity_unavailable"
        if control_kind(state.attributes.get("unit_of_measurement")) is None:
            return "control_unit_required"
        return self._pruefe_leiter(data)

    def _pruefe_leiter(self, data: dict[str, Any]) -> str | None:
        """Ergibt sich am Ende eine Leiter — und liegt die Untergrenze zu hoch?"""
        if build_ladder(self.hass, self._probe(data)) is not None:
            return None
        # Ohne die Untergrenze noch einmal: Ist sie der Grund, gehört der Fehler
        # an ihr Feld und nicht an die Entität.
        ohne_grenze = {key: value for key, value in data.items() if key != CONF_MIN_LEVEL_W}
        if data.get(CONF_MIN_LEVEL_W) and build_ladder(self.hass, self._probe(ohne_grenze)):
            return "min_level_too_high"
        return "no_levels"

    def _mit_vorher(self) -> dict[str, Any]:
        """Der gesammelte Stand über dem bisherigen. Nur zum Anzeigen."""
        return {**self._vorher, **self._data}

    def _erkannt(self) -> str:
        """Das erkannte Raster für die Beschreibung des Formulars.

        Der Nachweis, dass die Integration die Entität versteht, abzulesen bevor
        scharfgeschaltet wird. Beim ersten Anlegen ist noch nichts gewählt; dann
        bleibt ein Platzhalter stehen.
        """
        stand = self._mit_vorher()
        if not stand.get(CONF_CONTROL_ENTITY):
            return "-"
        ladder = build_ladder(self.hass, self._probe(stand))
        return describe(ladder) if ladder is not None else "-"

    def _control_vorbelegung(self, user_input: dict[str, Any] | None) -> dict[str, Any]:
        # Ohne den bisherigen Stand käme das Formular beim Bearbeiten leer daher:
        # Wer nur die Haltezeit ändern will, müsste die Entität erneut wählen.
        quelle = user_input or self._mit_vorher()
        vorbelegung = dict(quelle)
        # Die Auswahlliste erwartet eine Zeichenkette, gespeichert ist eine Zahl.
        if (phasen := vorbelegung.get(CONF_PHASES)) is not None:
            vorbelegung[CONF_PHASES] = str(phasen)
        return vorbelegung
