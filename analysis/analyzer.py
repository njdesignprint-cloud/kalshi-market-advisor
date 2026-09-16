"""Compara la probabilidad implicita de Kalshi contra el modelo propio.

Para cada mercado binario "el equipo X gana" de una serie de Kalshi
(ej. KXNBAGAME), este modulo:
  1. Identifica los dos equipos del evento (a partir de los dos mercados
     que Kalshi crea por partido, uno por equipo).
  2. Trae su forma reciente y lesiones desde ESPN.
  3. Calcula la probabilidad del modelo (models/probability_model.py).
  4. Compara esa probabilidad contra el precio de venta (yes_ask) de
     Kalshi, que es el costo real de entrar a la posicion "Yes".
  5. Calcula el edge y el valor esperado (EV), incluyendo una estimacion
     aproximada de la comision de Kalshi.

IMPORTANTE: este modulo solo lee datos. No coloca ordenes ni sugiere una
forma de "ejecutar" nada -- eso queda para portfolio.py (que solo genera
texto explicativo) y para el usuario, manualmente, en kalshi.com.

LIMITACION DEL EV: la formula general de comision publicada por Kalshi es
aproximada (0.07 * P * (1-P) por contrato, escalada por el fee_multiplier
de cada serie); algunos mercados pueden tener condiciones especiales que
esta estimacion no captura. Verifica el fee schedule real antes de operar.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from connectors.kalshi_client import KalshiClient, MarketQuote
from connectors.sports_data import EspnSportsDataClient
from models.probability_model import ProbabilityEstimate, estimate_win_probability, get_weights_for_league

BASE_FEE_RATE = 0.07  # formula general publicada por Kalshi (ver docstring)


@dataclass
class Opportunity:
    market_ticker: str
    event_ticker: str
    league: str
    team_abbreviation: str
    team_display_name: str
    opponent_abbreviation: str
    opponent_display_name: str
    is_home: bool | None
    game_datetime: str | None
    close_time: str | None
    kalshi_yes_ask: float
    kalshi_implied_probability: float
    model_probability: float
    model_confidence: str
    model_notes: list[str]
    edge: float                       # model_probability - kalshi_implied_probability
    ev_per_contract: float            # antes de comision
    estimated_fee_per_contract: float
    ev_per_contract_after_fee: float
    ev_return_on_cost: float          # ev_after_fee / costo, como fraccion

    @property
    def has_sufficient_data(self) -> bool:
        return self.model_confidence in ("alta", "media")


@dataclass
class SkippedMarket:
    market_ticker: str
    reason: str


@dataclass
class AnalysisResult:
    league: str
    opportunities: list[Opportunity] = field(default_factory=list)
    skipped: list[SkippedMarket] = field(default_factory=list)


def _estimate_fee_per_contract(price: float, fee_multiplier: float) -> float:
    raw = BASE_FEE_RATE * fee_multiplier * price * (1 - price)
    # Kalshi redondea hacia arriba al centavo.
    import math

    return math.ceil(raw * 100) / 100


def _group_markets_by_event(quotes: list[MarketQuote]) -> dict[str, list[MarketQuote]]:
    groups: dict[str, list[MarketQuote]] = {}
    for quote in quotes:
        groups.setdefault(quote.event_ticker, []).append(quote)
    return groups


def analyze_league(
    kalshi_client: KalshiClient,
    sports_client: EspnSportsDataClient,
    league_key: str,
    series_ticker: str,
    fee_multiplier: float = 1.0,
    recent_games_window: int = 10,
) -> AnalysisResult:
    result = AnalysisResult(league=league_key)
    weights = get_weights_for_league(league_key)

    quotes = kalshi_client.get_active_markets_for_series(series_ticker)
    if not quotes:
        return result

    injuries_by_team_id = sports_client.get_injuries_by_team_id()
    schedule_cache: dict[str, dict] = {}

    def get_team_and_schedule(kalshi_code: str):
        team = sports_client.resolve_kalshi_code(kalshi_code)
        if team is None:
            return None, None
        team_id = team["id"]
        if team_id not in schedule_cache:
            schedule_cache[team_id] = sports_client.get_schedule_raw(team_id)
        return team, schedule_cache[team_id]

    for event_ticker, markets in _group_markets_by_event(quotes).items():
        if len(markets) != 2:
            for m in markets:
                result.skipped.append(
                    SkippedMarket(m.ticker, f"Evento con {len(markets)} mercados (se esperaban 2); se omite.")
                )
            continue

        market_a, market_b = markets
        code_a = market_a.ticker.split("-")[-1]
        code_b = market_b.ticker.split("-")[-1]

        team_a, schedule_a = get_team_and_schedule(code_a)
        team_b, schedule_b = get_team_and_schedule(code_b)

        if team_a is None:
            result.skipped.append(SkippedMarket(market_a.ticker, f"No se pudo resolver el equipo '{code_a}' contra ESPN."))
        if team_b is None:
            result.skipped.append(SkippedMarket(market_b.ticker, f"No se pudo resolver el equipo '{code_b}' contra ESPN."))
        if team_a is None or team_b is None:
            continue

        record_a = sports_client.summarize_recent_record(schedule_a, team_a["id"], last_n=recent_games_window)
        record_b = sports_client.summarize_recent_record(schedule_b, team_b["id"], last_n=recent_games_window)

        is_home_a = sports_client.find_home_away(schedule_a, team_b["id"])
        is_home_b = (not is_home_a) if is_home_a is not None else None

        injuries_a = injuries_by_team_id.get(team_a["id"], [])
        injuries_b = injuries_by_team_id.get(team_b["id"], [])

        for market, team, opponent, record, opp_record, is_home, injuries, opp_injuries in (
            (market_a, team_a, team_b, record_a, record_b, is_home_a, injuries_a, injuries_b),
            (market_b, team_b, team_a, record_b, record_a, is_home_b, injuries_b, injuries_a),
        ):
            if market.yes_ask is None:
                result.skipped.append(SkippedMarket(market.ticker, "Sin precio yes_ask disponible (mercado sin liquidez)."))
                continue

            estimate = estimate_win_probability(
                team=record,
                opponent=opp_record,
                team_is_home=bool(is_home) if is_home is not None else False,
                team_injuries=injuries,
                opponent_injuries=opp_injuries,
                weights=weights,
            )
            if is_home is None:
                estimate.notes.append("Localia no confirmada contra el calendario de ESPN; se asumio visitante por defecto (conservador).")

            price = market.yes_ask
            fee = _estimate_fee_per_contract(price, fee_multiplier)
            ev = estimate.probability - price
            ev_after_fee = ev - fee
            roi = ev_after_fee / price if price > 0 else 0.0

            result.opportunities.append(
                Opportunity(
                    market_ticker=market.ticker,
                    event_ticker=event_ticker,
                    league=league_key,
                    team_abbreviation=team["abbreviation"],
                    team_display_name=team["displayName"],
                    opponent_abbreviation=opponent["abbreviation"],
                    opponent_display_name=opponent["displayName"],
                    is_home=is_home,
                    game_datetime=market.occurrence_datetime or market.close_time,
                    close_time=market.close_time,
                    kalshi_yes_ask=price,
                    kalshi_implied_probability=price,
                    model_probability=estimate.probability,
                    model_confidence=estimate.confidence,
                    model_notes=estimate.notes,
                    edge=estimate.probability - price,
                    ev_per_contract=ev,
                    estimated_fee_per_contract=fee,
                    ev_per_contract_after_fee=ev_after_fee,
                    ev_return_on_cost=roi,
                )
            )

    result.opportunities.sort(key=lambda o: o.ev_per_contract_after_fee, reverse=True)
    return result
