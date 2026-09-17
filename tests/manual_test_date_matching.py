import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.analyzer import Opportunity  # noqa: E402
from main import _matches_date  # noqa: E402


def make_opp(game_datetime):
    return Opportunity(
        market_ticker="EVT-AAA", event_ticker="EVT", league="MLB",
        team_abbreviation="AAA", team_display_name="AAA",
        opponent_abbreviation="BBB", opponent_display_name="BBB",
        is_home=True, game_datetime=game_datetime, close_time=None,
        kalshi_yes_ask=0.5, kalshi_implied_probability=0.5,
        model_probability=0.5, model_confidence="alta", model_notes=[],
        edge=0.0, ev_per_contract=0.0, estimated_fee_per_contract=0.0,
        ev_per_contract_after_fee=0.0, ev_return_on_cost=0.0,
    )


def main():
    # Caso real que motivo el fix: juego nocturno (19:40 ET del 17-sep) que
    # Kalshi fecha como "26SEP17" pero que en UTC cae al 18-sep. Debe seguir
    # contando como partido del 17, no del 18.
    evening_game = make_opp("2026-09-18T02:40:00Z")
    assert _matches_date(evening_game, "2026-09-17") is True, "El juego nocturno debe pertenecer al 17 (hora del Este), no al 18 (UTC)."
    assert _matches_date(evening_game, "2026-09-18") is False, "No debe duplicarse tambien como partido del 18."

    # Un juego diurno cuya fecha UTC coincide con la fecha ET no debe romperse.
    day_game = make_opp("2026-09-17T18:10:00Z")
    assert _matches_date(day_game, "2026-09-17") is True, "El juego diurno debe seguir cayendo en su mismo dia."

    # Sin game_datetime, nunca hace match (comportamiento previo preservado).
    no_datetime = make_opp(None)
    assert _matches_date(no_datetime, "2026-09-17") is False

    print("OK: el filtro de fecha usa el dia calendario del Este, no el de UTC.")


if __name__ == "__main__":
    main()
