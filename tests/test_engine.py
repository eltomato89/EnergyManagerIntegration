"""Schaltentscheidung und die vier Zeitfelder.

Die Zeitfelder sind der Grund, warum die Integration überhaupt eigenen
Laufzeitzustand führt. Entsprechend genau werden sie hier geprüft.
"""

from __future__ import annotations

import pytest

from custom_components.energy_manager.engine import (
    Blocker,
    compute_lock,
    decide,
    decide_for,
    order_consumers,
    resolve_required_w,
    update_conditions,
)
from custom_components.energy_manager.models import (
    ConsumerConfig,
    ConsumerRuntime,
    ConsumerView,
    DeviceStatus,
)

NOW = 1_800_000_000.0


def consumer(name: str = "Test", **kwargs) -> ConsumerConfig:
    return ConsumerConfig(
        subentry_id=kwargs.pop("subentry_id", name.lower()),
        name=name,
        switch_entity=f"switch.{name.lower()}",
        **kwargs,
    )


def view(
    cfg: ConsumerConfig,
    *,
    status: DeviceStatus,
    is_on: bool,
    managed: bool = True,
    available: bool = True,
    locked_until: float | None = None,
) -> ConsumerView:
    return ConsumerView(
        config=cfg,
        rank=0,
        is_on=is_on,
        available=available,
        managed=managed,
        status=status,
        locked_until=locked_until,
    )


class TestResolveRequiredW:
    def test_reihenfolge_der_quellen(self) -> None:
        assert resolve_required_w(consumer(min_power=800, max_power=3000), 1200) == 800
        assert resolve_required_w(consumer(max_power=3000), 1200) == 3000
        assert resolve_required_w(consumer(), 1200) == 1200
        assert resolve_required_w(consumer(), None) == 500

    def test_null_werte_zaehlen_nicht(self) -> None:
        assert resolve_required_w(consumer(min_power=0, max_power=2000), None) == 2000
        assert resolve_required_w(consumer(), 0) == 500


class TestOrderConsumers:
    def test_sortiert_nach_prioritaet(self) -> None:
        a, b, c = consumer("A"), consumer("B"), consumer("C")
        order = order_consumers({"a": a, "b": b, "c": c}, {"a": 3.0, "b": 1.0, "c": 2.0})
        assert [x.name for x in order] == ["B", "C", "A"]

    def test_gleichstand_bleibt_stabil(self) -> None:
        a, b = consumer("Zebra"), consumer("Anton")
        order = order_consumers({"zebra": a, "anton": b}, {"zebra": 1.0, "anton": 1.0})
        # Bei gleicher Priorität entscheidet der Name — nicht die Zufallsordnung
        # eines Dictionaries.
        assert [x.name for x in order] == ["Anton", "Zebra"]


class TestComputeLock:
    def test_mindestlaufzeit_nach_dem_einschalten(self) -> None:
        cfg = consumer(min_runtime=300)
        runtime = ConsumerRuntime(last_switch_ts=NOW - 120, last_switch_to=True)

        until, kind = compute_lock(cfg, runtime, True, NOW)
        assert until == NOW + 180
        assert kind == "min_runtime"

    def test_mindest_aus_zeit_nach_dem_ausschalten(self) -> None:
        cfg = consumer(min_off_time=600)
        runtime = ConsumerRuntime(last_switch_ts=NOW - 45, last_switch_to=False)

        until, kind = compute_lock(cfg, runtime, False, NOW)
        assert until == NOW + 555
        assert kind == "min_off_time"

    def test_abgelaufene_sperre(self) -> None:
        cfg = consumer(min_off_time=600)
        runtime = ConsumerRuntime(last_switch_ts=NOW - 900, last_switch_to=False)
        assert compute_lock(cfg, runtime, False, NOW) == (None, None)

    def test_ohne_eigene_schaltung_keine_sperre(self) -> None:
        """Hat die Integration nie geschaltet, sperrt sie auch nichts."""
        cfg = consumer(min_runtime=300)
        assert compute_lock(cfg, ConsumerRuntime(), True, NOW) == (None, None)

    def test_sperre_gilt_nur_fuer_die_geschaltete_richtung(self) -> None:
        """Nach manuellem Gegensteuern greift die eigene Sperre nicht mehr."""
        cfg = consumer(min_runtime=300, min_off_time=300)
        # Integration schaltete EIN, jemand hat von Hand ausgeschaltet.
        runtime = ConsumerRuntime(last_switch_ts=NOW - 10, last_switch_to=True)
        assert compute_lock(cfg, runtime, False, NOW) == (None, None)


class TestUpdateConditions:
    def test_merkt_sich_den_beginn(self) -> None:
        cfg = consumer()
        runtime = ConsumerRuntime()
        v = view(cfg, status=DeviceStatus.OFF_READY, is_on=False)

        update_conditions(v, runtime, NOW)
        assert runtime.on_condition_since == NOW

        # Bei erneutem Aufruf bleibt der Beginn stehen.
        update_conditions(v, runtime, NOW + 30)
        assert runtime.on_condition_since == NOW

    def test_eine_luecke_setzt_zurueck(self) -> None:
        """Der Kern der Verzögerungslogik."""
        cfg = consumer()
        runtime = ConsumerRuntime()

        update_conditions(view(cfg, status=DeviceStatus.OFF_READY, is_on=False), runtime, NOW)
        assert runtime.on_condition_since == NOW

        # Eine Wolke: Bedingung kurz nicht erfüllt.
        update_conditions(
            view(cfg, status=DeviceStatus.OFF_INSUFFICIENT, is_on=False), runtime, NOW + 10
        )
        assert runtime.on_condition_since is None

        # Danach beginnt die Zeit von vorn, nicht bei 10 Sekunden.
        update_conditions(view(cfg, status=DeviceStatus.OFF_READY, is_on=False), runtime, NOW + 20)
        assert runtime.on_condition_since == NOW + 20

    def test_ein_und_ausschaltbedingung_schliessen_sich_aus(self) -> None:
        cfg = consumer()
        runtime = ConsumerRuntime()

        update_conditions(view(cfg, status=DeviceStatus.ON_DEFICIT, is_on=True), runtime, NOW)
        assert runtime.off_condition_since == NOW
        assert runtime.on_condition_since is None


class TestDecideFor:
    def test_einschalten_nach_ablauf_der_verzoegerung(self) -> None:
        cfg = consumer(turn_on_delay=120)
        runtime = ConsumerRuntime(on_condition_since=NOW - 120)
        v = view(cfg, status=DeviceStatus.OFF_READY, is_on=False)

        decision, blocker = decide_for(v, runtime, NOW)
        assert blocker is None
        assert decision is not None
        assert decision.turn_on is True

    def test_verzoegerung_haelt_zurueck(self) -> None:
        cfg = consumer(turn_on_delay=120)
        runtime = ConsumerRuntime(on_condition_since=NOW - 60)
        v = view(cfg, status=DeviceStatus.OFF_READY, is_on=False)

        decision, blocker = decide_for(v, runtime, NOW)
        assert decision is None
        assert blocker is Blocker.TURN_ON_DELAY

    def test_ausschalten_nach_anhaltendem_defizit(self) -> None:
        cfg = consumer(turn_off_delay=180)
        runtime = ConsumerRuntime(off_condition_since=NOW - 200)
        v = view(cfg, status=DeviceStatus.ON_DEFICIT, is_on=True)

        decision, blocker = decide_for(v, runtime, NOW)
        assert blocker is None
        assert decision is not None
        assert decision.turn_on is False

    def test_mindestlaufzeit_verhindert_das_ausschalten(self) -> None:
        cfg = consumer(min_runtime=900)
        runtime = ConsumerRuntime(off_condition_since=NOW - 10)
        v = view(cfg, status=DeviceStatus.ON_DEFICIT, is_on=True, locked_until=NOW + 500)

        decision, blocker = decide_for(v, runtime, NOW)
        assert decision is None
        assert blocker is Blocker.MIN_RUNTIME

    def test_mindest_aus_zeit_verhindert_das_einschalten(self) -> None:
        cfg = consumer(min_off_time=600)
        runtime = ConsumerRuntime(on_condition_since=NOW - 10)
        v = view(cfg, status=DeviceStatus.OFF_READY, is_on=False, locked_until=NOW + 300)

        decision, blocker = decide_for(v, runtime, NOW)
        assert decision is None
        assert blocker is Blocker.MIN_OFF_TIME

    @pytest.mark.parametrize(
        ("kwargs", "erwartet"),
        [
            ({"managed": False}, Blocker.NOT_MANAGED),
            ({"available": False}, Blocker.UNAVAILABLE),
        ],
    )
    def test_grundsaetzliche_sperren(self, kwargs, erwartet) -> None:
        cfg = consumer()
        runtime = ConsumerRuntime(on_condition_since=NOW - 999)
        v = view(cfg, status=DeviceStatus.OFF_READY, is_on=False, **kwargs)

        decision, blocker = decide_for(v, runtime, NOW)
        assert decision is None
        assert blocker is erwartet

    def test_beruhigungsfenster(self) -> None:
        """Nach einer Schaltung wird derselbe Verbraucher in Ruhe gelassen."""
        cfg = consumer()
        runtime = ConsumerRuntime(on_condition_since=NOW - 999, settle_until=NOW + 30)
        v = view(cfg, status=DeviceStatus.OFF_READY, is_on=False)

        decision, blocker = decide_for(v, runtime, NOW)
        assert decision is None
        assert blocker is Blocker.SETTLING


class TestDecide:
    def test_hoechstens_eine_aktion(self) -> None:
        """Jede Schaltung verändert den Überschuss, auf den die nächste sich stützt."""
        a, b = consumer("A", subentry_id="a"), consumer("B", subentry_id="b")
        views = [
            view(a, status=DeviceStatus.OFF_READY, is_on=False),
            view(b, status=DeviceStatus.OFF_READY, is_on=False),
        ]
        runtimes = {
            "a": ConsumerRuntime(on_condition_since=NOW - 999),
            "b": ConsumerRuntime(on_condition_since=NOW - 999),
        }

        result = decide(views, runtimes, NOW)
        assert result.action is not None
        # Der erste in der Prioritätsreihenfolge gewinnt.
        assert result.action.subentry_id == "a"

    def test_ausschalten_hat_vorrang(self) -> None:
        """Ein anhaltendes Defizit zu beenden ist dringender."""
        a, b = consumer("A", subentry_id="a"), consumer("B", subentry_id="b")
        views = [
            view(a, status=DeviceStatus.OFF_READY, is_on=False),
            view(b, status=DeviceStatus.ON_DEFICIT, is_on=True),
        ]
        runtimes = {
            "a": ConsumerRuntime(on_condition_since=NOW - 999),
            "b": ConsumerRuntime(off_condition_since=NOW - 999),
        }

        result = decide(views, runtimes, NOW)
        assert result.action is not None
        assert result.action.subentry_id == "b"
        assert result.action.turn_on is False

    def test_sammelt_die_gruende_fuer_untaetigkeit(self) -> None:
        a = consumer("A", subentry_id="a")
        views = [view(a, status=DeviceStatus.OFF_READY, is_on=False, managed=False)]
        runtimes = {"a": ConsumerRuntime()}

        result = decide(views, runtimes, NOW)
        assert result.action is None
        assert result.blockers["a"] is Blocker.NOT_MANAGED

    def test_ohne_bedarf_keine_aktion(self) -> None:
        a = consumer("A", subentry_id="a")
        views = [view(a, status=DeviceStatus.OFF_INSUFFICIENT, is_on=False)]
        runtimes = {"a": ConsumerRuntime()}

        assert decide(views, runtimes, NOW).action is None

    def test_verzoegerung_wird_ueber_mehrere_durchlaeufe_aufgebaut(self) -> None:
        """Der Ablauf, wie er in der Praxis stattfindet."""
        a = consumer("A", subentry_id="a", turn_on_delay=120)
        runtimes = {"a": ConsumerRuntime()}
        bereit = [view(a, status=DeviceStatus.OFF_READY, is_on=False)]

        # Erster Durchlauf: Bedingung beginnt, noch keine Aktion.
        assert decide(bereit, runtimes, NOW).action is None
        # Nach 60 s immer noch nicht.
        assert decide(bereit, runtimes, NOW + 60).action is None
        # Nach 120 s schaltet sie.
        result = decide(bereit, runtimes, NOW + 120)
        assert result.action is not None and result.action.turn_on

    def test_wolke_setzt_die_verzoegerung_zurueck(self) -> None:
        a = consumer("A", subentry_id="a", turn_on_delay=120)
        runtimes = {"a": ConsumerRuntime()}
        bereit = [view(a, status=DeviceStatus.OFF_READY, is_on=False)]
        wolke = [view(a, status=DeviceStatus.OFF_INSUFFICIENT, is_on=False)]

        decide(bereit, runtimes, NOW)
        decide(bereit, runtimes, NOW + 100)
        decide(wolke, runtimes, NOW + 110)  # kurze Unterbrechung
        decide(bereit, runtimes, NOW + 120)

        # 130 s nach Beginn, aber erst 10 s nach dem Neustart der Bedingung.
        assert decide(bereit, runtimes, NOW + 130).action is None
        # Erst 120 s nach der Unterbrechung wird geschaltet.
        result = decide(bereit, runtimes, NOW + 240)
        assert result.action is not None
