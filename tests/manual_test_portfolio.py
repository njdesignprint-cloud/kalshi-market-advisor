import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.analyzer import Opportunity  # noqa: E402
from analysis.portfolio import build_portfolio_recommendation  # noqa: E402


def make_opp(ticker, price, model_p, confidence="alta", ev_after_fee=None):
    edge = model_p - price
    ev = model_p - price
    fee = 0.01
    ev_after_fee = ev - fee if ev_after_fee is None else ev_after_fee
    roi = ev_after_fee / price
    return Opportunity(
        market_ticker=ticker, event_ticker=ticker.rsplit("-", 1)[0], league="NBA",
        team_abbreviation=ticker[-3:], team_display_name=ticker[-3:],
        opponent_abbreviation="OPP", opponent_display_name="Opponent",
        is_home=True, game_datetime=None, close_time=None,
        kalshi_yes_ask=price, kalshi_implied_probability=price,
        model_probability=model_p, model_confidence=confidence, model_notes=[],
        edge=edge, ev_per_contract=ev, estimated_fee_per_contract=fee,
        ev_per_contract_after_fee=ev_after_fee, ev_return_on_cost=roi,
    )


def main():
    # Caso 1: ninguna oportunidad supera el umbral -> "no hay recomendacion"
    weak_opps = [make_opp("EVT1-AAA", 0.50, 0.51, confidence="alta")]
    rec = build_portfolio_recommendation(weak_opps, bankroll=1000, date="2026-09-20")
    assert rec.has_recommendation is False
    print("OK caso sin oportunidades reales:", rec.summary[:80], "...")

    # Caso 1b: confianza "ninguna" (sin datos) nunca debe generar recomendacion
    no_data_opps = [make_opp("EVT2-BBB", 0.40, 0.70, confidence="ninguna")]
    rec = build_portfolio_recommendation(no_data_opps, bankroll=1000, date="2026-09-20")
    assert rec.has_recommendation is False
    print("OK caso confianza insuficiente se descarta aunque el edge parezca grande")

    # Caso 2: una oportunidad clara con buen edge -> debe recomendar una posicion, nunca 100%
    good_opps = [make_opp("EVT3-CCC", 0.40, 0.55, confidence="alta")]
    rec = build_portfolio_recommendation(good_opps, bankroll=1000, date="2026-09-20")
    assert rec.has_recommendation is True
    assert len(rec.allocations) == 1
    alloc = rec.allocations[0]
    assert alloc.stake_fraction_of_bankroll <= 0.10 + 1e-9
    assert alloc.stake_dollars < 1000
    print(f"OK una oportunidad -> stake ${alloc.stake_dollars} ({alloc.stake_fraction_of_bankroll:.1%} del bankroll, nunca 100%)")

    # Caso 3: varias oportunidades muy buenas -> el total desplegado nunca debe superar el tope global
    many_opps = [make_opp(f"EVT{i}-CCC", 0.30, 0.60, confidence="alta") for i in range(6)]
    rec = build_portfolio_recommendation(many_opps, bankroll=1000, date="2026-09-20")
    assert rec.has_recommendation is True
    total_fraction = sum(a.stake_fraction_of_bankroll for a in rec.allocations)
    assert total_fraction <= 0.50 + 1e-6, total_fraction
    print(f"OK varias oportunidades -> {total_fraction:.1%} del bankroll desplegado en total (tope 50%)")

    # El aviso anti-parlay siempre debe estar presente
    assert "combinadas" in rec.parlay_warning.lower()
    print("OK aviso explicito contra combinadas/parlays presente en toda recomendacion")

    print("Todas las pruebas de portfolio pasaron.")


if __name__ == "__main__":
    main()
