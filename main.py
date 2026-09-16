"""Pipeline completo: recolectar -> analizar -> recomendar -> generar dashboard.

Uso:
    python main.py --date 2026-10-20 --bankroll 500
    python main.py --date 2026-10-20 --bankroll 500 --leagues NBA

Este script SOLO lee datos (Kalshi, ESPN) y escribe un archivo de
resultados local. No coloca ordenes ni mueve dinero. El resultado es
informacion para que decidas y operes manualmente en kalshi.com.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from analysis.analyzer import Opportunity, analyze_league
from analysis.portfolio import build_portfolio_recommendation
from config import LEAGUES, get_league
from connectors.kalshi_client import KalshiClient
from connectors.sports_data import EspnSportsDataClient

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DASHBOARD_TEMPLATE = Path(__file__).resolve().parent / "dashboard.html"
DASHBOARD_DATA_PLACEHOLDER = "/*__KALSHI_MARKET_ADVISOR_DATA__*/ null"
DASHBOARD_NAV_PLACEHOLDER = "<!--__KALSHI_MARKET_ADVISOR_NAV__-->"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kalshi Market Advisor - pipeline de analisis")
    parser.add_argument("--date", required=True, help="Fecha de los partidos a considerar, formato YYYY-MM-DD")
    parser.add_argument("--bankroll", required=True, type=float, help="Monto total disponible para asignar, en dolares")
    parser.add_argument(
        "--leagues",
        default=",".join(LEAGUES.keys()),
        help=f"Ligas a analizar, separadas por coma. Disponibles: {', '.join(LEAGUES.keys())}",
    )
    return parser.parse_args()


def _matches_date(opportunity: Opportunity, target_date: str) -> bool:
    if not opportunity.game_datetime:
        return False
    try:
        game_date = datetime.fromisoformat(opportunity.game_datetime.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return False
    return game_date == target_date


def run_pipeline(date: str, bankroll: float, league_keys: list[str]) -> dict:
    kalshi_client = KalshiClient()

    all_opportunities: list[Opportunity] = []
    all_skipped = []

    for key in league_keys:
        league = get_league(key)
        sports_client = EspnSportsDataClient(league.espn_sport_slug, league.espn_league_slug)
        result = analyze_league(
            kalshi_client, sports_client, league.key, league.kalshi_series_ticker,
            uses_starting_pitcher=league.uses_starting_pitcher,
        )
        all_opportunities.extend(result.opportunities)
        all_skipped.extend(result.skipped)

    opportunities_for_date = [o for o in all_opportunities if _matches_date(o, date)]

    recommendation = build_portfolio_recommendation(opportunities_for_date, bankroll=bankroll, date=date)

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "date": date,
        "bankroll": bankroll,
        "leagues_analyzed": league_keys,
        "total_markets_analyzed": len(all_opportunities),
        "markets_for_date": len(opportunities_for_date),
        "markets_skipped": len(all_skipped),
        "opportunities": [asdict(o) for o in sorted(all_opportunities, key=lambda o: o.ev_per_contract_after_fee, reverse=True)],
        "opportunities_for_date": [asdict(o) for o in opportunities_for_date],
        "recommendation": asdict(recommendation),
    }


def render_dashboard_html(data: dict, nav_html: str = "") -> str:
    template = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    if DASHBOARD_DATA_PLACEHOLDER not in template:
        raise RuntimeError(
            f"No se encontro el marcador '{DASHBOARD_DATA_PLACEHOLDER}' en dashboard.html; "
            "no se puede inyectar la data generada."
        )
    rendered = template.replace(DASHBOARD_DATA_PLACEHOLDER, json.dumps(data, ensure_ascii=False, indent=2))
    rendered = rendered.replace(DASHBOARD_NAV_PLACEHOLDER, nav_html)
    return rendered


def write_dashboard(data: dict) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    rendered = render_dashboard_html(data)
    output_path = OUTPUT_DIR / "dashboard.html"
    output_path.write_text(rendered, encoding="utf-8")

    (OUTPUT_DIR / "recommendation_latest.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output_path


def main() -> None:
    load_dotenv()
    args = parse_args()
    league_keys = [k.strip().upper() for k in args.leagues.split(",") if k.strip()]

    print(f"Analizando {', '.join(league_keys)} para la fecha {args.date} con bankroll ${args.bankroll:,.2f}...")
    data = run_pipeline(args.date, args.bankroll, league_keys)

    rec = data["recommendation"]
    print(f"\nMercados analizados en total: {data['total_markets_analyzed']} (omitidos: {data['markets_skipped']})")
    print(f"Mercados que corresponden a partidos del {args.date}: {data['markets_for_date']}")
    print(f"\n{rec['summary']}\n")
    if rec["has_recommendation"]:
        for alloc in rec["allocations"]:
            print(f"  - {alloc['team_abbreviation']} vs {alloc['opponent_abbreviation']}: ${alloc['stake_dollars']:.2f} ({alloc['market_ticker']})")

    output_path = write_dashboard(data)
    print(f"\nDashboard generado en: {output_path}")


if __name__ == "__main__":
    main()
