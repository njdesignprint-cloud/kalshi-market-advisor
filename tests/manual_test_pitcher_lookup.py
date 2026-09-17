import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.analyzer import _espn_lookup_cutoff  # noqa: E402
from connectors.sports_data import EspnSportsDataClient  # noqa: E402


def make_schedule(*meetings):
    """meetings: lista de (event_id, iso_date) contra el mismo rival."""
    return {
        "events": [
            {
                "id": event_id,
                "date": iso_date,
                "competitions": [{"competitors": [
                    {"team": {"id": "OPP"}},
                    {"team": {"id": "SELF"}},
                ]}],
            }
            for event_id, iso_date in meetings
        ]
    }


def main():
    client = EspnSportsDataClient("baseball", "mlb")

    # Caso real que motivo el fix: CIN y LAD se enfrentan 7 veces en el mes.
    # Sin cota, find_event_id devuelve siempre el primer encuentro (el del
    # 8-sep), que ya jugo hace dias y trae pitchers probables obsoletos.
    schedule = make_schedule(
        ("early_meeting", "2026-09-08T01:10Z"),
        ("mid_meeting", "2026-09-14T22:40Z"),
        ("tonight", "2026-09-17T16:40Z"),
    )

    # Sin cota (comportamiento viejo): agarra el primero, no el de hoy.
    assert client.find_event_id(schedule, "OPP") == "early_meeting"

    # Con la cota derivada del propio mercado de Kalshi de esta noche: agarra
    # el encuentro correcto, no uno de hace dias ni el de otra noche.
    market_tonight = SimpleNamespace(occurrence_datetime="2026-09-17T19:40:00Z", close_time=None)
    cutoff = _espn_lookup_cutoff(market_tonight)
    assert client.find_event_id(schedule, "OPP", on_or_after=cutoff) == "tonight"

    # Noches consecutivas de una misma serie (Detroit vs Chicago) tampoco deben
    # mezclarse: la cota de la noche de manana no debe agarrar la de hoy.
    series_schedule = make_schedule(
        ("today_game", "2026-09-17T23:40Z"),
        ("tomorrow_game", "2026-09-18T23:40Z"),
    )
    market_tomorrow = SimpleNamespace(occurrence_datetime="2026-09-19T02:40:00Z", close_time=None)
    cutoff_tomorrow = _espn_lookup_cutoff(market_tomorrow)
    assert client.find_event_id(series_schedule, "OPP", on_or_after=cutoff_tomorrow) == "tomorrow_game"

    # Sin occurrence_datetime ni close_time, no hay cota (no se rompe, solo no filtra).
    assert _espn_lookup_cutoff(SimpleNamespace(occurrence_datetime=None, close_time=None)) is None

    print("OK: la busqueda de pitcher abridor usa el encuentro correcto de la serie, no siempre el primero de la temporada.")


if __name__ == "__main__":
    main()
