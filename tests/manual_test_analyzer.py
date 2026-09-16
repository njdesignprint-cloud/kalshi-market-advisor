import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.analyzer import analyze_league  # noqa: E402
from connectors.kalshi_client import KalshiClient  # noqa: E402
from connectors.sports_data import EspnSportsDataClient  # noqa: E402
from config import get_league  # noqa: E402


def main():
    league = get_league("NBA")
    kalshi = KalshiClient()
    espn = EspnSportsDataClient(league.espn_sport_slug, league.espn_league_slug)

    result = analyze_league(kalshi, espn, league.key, league.kalshi_series_ticker)

    print(f"Oportunidades analizadas: {len(result.opportunities)}")
    print(f"Mercados omitidos: {len(result.skipped)}")
    for s in result.skipped[:5]:
        print("  omitido:", s.market_ticker, "->", s.reason)

    for opp in result.opportunities[:10]:
        print(
            f"{opp.market_ticker:30s} {opp.team_abbreviation:>4s} vs {opp.opponent_abbreviation:<4s} "
            f"local={opp.is_home} kalshi={opp.kalshi_implied_probability:.2f} "
            f"modelo={opp.model_probability:.2f} conf={opp.model_confidence:6s} "
            f"edge={opp.edge:+.3f} EV={opp.ev_per_contract_after_fee:+.4f}"
        )
        if opp.model_notes:
            for note in opp.model_notes:
                print("     nota:", note)

    assert all(-1 <= o.edge <= 1 for o in result.opportunities)
    print("OK: estructura de oportunidades es consistente.")


if __name__ == "__main__":
    main()
