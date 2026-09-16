"""Genera una recomendacion de asignacion de bankroll, en texto explicado.

Este modulo NUNCA calcula ordenes para ejecutar automaticamente. Devuelve
una recomendacion (cuanto dinero, en que mercado, y por que) para que la
persona usuaria la ejecute manualmente en kalshi.com si decide hacerlo.

REGLAS DE DISENO (explicitas, pedidas por el usuario):
  - Si ningun mercado tiene valor esperado positivo real (despues de
    aplicar el umbral minimo de edge y de EV), la respuesta correcta es
    "no hay recomendacion hoy". No se fuerza una sugerencia.
  - Nunca se recomienda apostar el 100% del bankroll en una sola jugada,
    sin importar que tan alto sea el edge calculado (el modelo puede estar
    equivocado). Hay un tope maximo por jugada y un tope maximo de bankroll
    total desplegado.
  - No se arman combinadas/parlays. Si el usuario esta considerando
    combinar varias jugadas, se le explica por que generalmente reduce el
    valor esperado ajustado por riesgo (las probabilidades de fallo se
    multiplican) y se recomienda en contra.
  - El tamano de cada posicion se basa en el criterio de Kelly fraccionado
    (1/4 de Kelly), no en Kelly completo, precisamente porque el modelo de
    probabilidad es simple y su error de estimacion no esta cuantificado
    con precision. Apostar Kelly completo contra una probabilidad estimada
    (no verdadera) es una receta para sobre-apostar.

Formula de Kelly usada (para un contrato binario con precio P que paga $1):
  f* = (p - P) / (1 - P)
donde p es la probabilidad del modelo y P el precio (costo) del contrato.
Ver, por ejemplo, cualquier referencia estandar del criterio de Kelly
aplicado a mercados de apuestas binarias de precio fraccionario.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from analysis.analyzer import Opportunity

KELLY_FRACTION = 0.25
MAX_SINGLE_POSITION_FRACTION = 0.10
MAX_TOTAL_DEPLOYED_FRACTION = 0.50
MIN_EDGE_TO_CONSIDER = 0.03
MIN_EV_RETURN_TO_CONSIDER = 0.04

PARLAY_WARNING = (
    "Esta herramienta no arma ni recomienda combinadas/parlays. Combinar varias "
    "jugadas independientes multiplica sus probabilidades de fallo conjunto "
    "(si necesitas acertar N jugadas para cobrar, tu probabilidad de exito cae "
    "geometricamente) y en la gran mayoria de los casos reduce el valor esperado "
    "ajustado por riesgo frente a jugarlas por separado con el tamano correcto. "
    "Si estabas pensando en combinar las oportunidades de abajo, la recomendacion "
    "explicita es NO hacerlo: juegalas por separado, en el tamano indicado, o no "
    "las juegues."
)

GENERAL_RISK_NOTES = [
    "El modelo de probabilidad es simple y transparente, pero no esta validado "
    "con backtesting historico; trata sus numeros como una segunda opinion, no "
    "como una certeza.",
    "Los precios de Kalshi pueden moverse entre el momento en que se genero este "
    "reporte y el momento en que decidas operar; el edge calculado puede haber "
    "desaparecido.",
    "Las lesiones de ultima hora, cambios de alineacion o informacion no publica "
    "no estan reflejadas en este analisis.",
    "El tamano de posicion usa Kelly fraccionado (1/4) con topes de riesgo, pero "
    "ninguna gestion de riesgo elimina la posibilidad de perder el dinero asignado.",
]


@dataclass
class Allocation:
    market_ticker: str
    league: str
    team_abbreviation: str
    opponent_abbreviation: str
    kalshi_price: float
    model_probability: float
    edge: float
    stake_dollars: float
    stake_fraction_of_bankroll: float
    estimated_ev_dollars: float
    rationale: str
    risks: str


@dataclass
class PortfolioRecommendation:
    date: str
    bankroll: float
    has_recommendation: bool
    summary: str
    allocations: list[Allocation] = field(default_factory=list)
    total_deployed_dollars: float = 0.0
    cash_reserved_dollars: float = 0.0
    parlay_warning: str = PARLAY_WARNING
    general_risk_notes: list[str] = field(default_factory=lambda: list(GENERAL_RISK_NOTES))


def _full_kelly_fraction(model_probability: float, price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return (model_probability - price) / (1 - price)


def build_portfolio_recommendation(
    opportunities: list[Opportunity],
    bankroll: float,
    date: str,
) -> PortfolioRecommendation:
    if bankroll <= 0:
        raise ValueError("El bankroll debe ser un monto positivo.")

    candidates = [
        opp
        for opp in opportunities
        if opp.has_sufficient_data
        and opp.edge >= MIN_EDGE_TO_CONSIDER
        and opp.ev_return_on_cost >= MIN_EV_RETURN_TO_CONSIDER
        and opp.ev_per_contract_after_fee > 0
    ]

    if not candidates:
        return PortfolioRecommendation(
            date=date,
            bankroll=bankroll,
            has_recommendation=False,
            summary=(
                "No hay recomendacion para hoy. Ningun mercado analizado tiene un edge "
                f"de al menos {MIN_EDGE_TO_CONSIDER:.0%} y un retorno esperado de al menos "
                f"{MIN_EV_RETURN_TO_CONSIDER:.0%} sobre el costo, con datos suficientes para "
                "confiar en la estimacion del modelo. En mercados razonablemente eficientes "
                "esto es lo esperable la mayoria de los dias: no forzamos una jugada solo "
                "por generar actividad."
            ),
        )

    # Kelly fraccionado por candidato, con tope individual.
    raw_fractions = []
    for opp in candidates:
        full_kelly = _full_kelly_fraction(opp.model_probability, opp.kalshi_yes_ask)
        fractional = max(0.0, full_kelly * KELLY_FRACTION)
        capped = min(fractional, MAX_SINGLE_POSITION_FRACTION)
        raw_fractions.append(capped)

    total_fraction = sum(raw_fractions)
    scale = 1.0
    if total_fraction > MAX_TOTAL_DEPLOYED_FRACTION:
        scale = MAX_TOTAL_DEPLOYED_FRACTION / total_fraction

    allocations: list[Allocation] = []
    total_deployed = 0.0
    for opp, fraction in zip(candidates, raw_fractions):
        final_fraction = fraction * scale
        stake = round(bankroll * final_fraction, 2)
        if stake <= 0:
            continue
        total_deployed += stake
        ev_dollars = stake * opp.ev_return_on_cost

        rationale = (
            f"Kalshi implica {opp.kalshi_implied_probability:.0%} de probabilidad para "
            f"{opp.team_abbreviation}; el modelo (confianza {opp.model_confidence}) estima "
            f"{opp.model_probability:.0%}, un edge de {opp.edge:+.0%}. Retorno esperado neto "
            f"de comisiones: {opp.ev_return_on_cost:+.1%} sobre el monto invertido. Tamano de "
            f"posicion = 1/4 de Kelly, limitado a un maximo del {MAX_SINGLE_POSITION_FRACTION:.0%} "
            "del bankroll por jugada."
        )
        risks = (
            "El modelo puede estar sobreestimando esta probabilidad (no hay backtesting "
            "historico). " + " ".join(opp.model_notes)
        ).strip()

        allocations.append(
            Allocation(
                market_ticker=opp.market_ticker,
                league=opp.league,
                team_abbreviation=opp.team_abbreviation,
                opponent_abbreviation=opp.opponent_abbreviation,
                kalshi_price=opp.kalshi_yes_ask,
                model_probability=opp.model_probability,
                edge=opp.edge,
                stake_dollars=stake,
                stake_fraction_of_bankroll=final_fraction,
                estimated_ev_dollars=ev_dollars,
                rationale=rationale,
                risks=risks,
            )
        )

    cash_reserved = round(bankroll - total_deployed, 2)

    if not allocations:
        return PortfolioRecommendation(
            date=date,
            bankroll=bankroll,
            has_recommendation=False,
            summary="No hay recomendacion para hoy: las oportunidades candidatas no dejaron ningun monto positivo tras aplicar los limites de riesgo.",
        )

    oportunidad_word = "oportunidades" if len(allocations) != 1 else "oportunidad"
    summary = (
        f"Se encontraron {len(allocations)} {oportunidad_word} con valor esperado positivo real "
        f"hoy. Se recomienda desplegar ${total_deployed:,.2f} de ${bankroll:,.2f} "
        f"({total_deployed / bankroll:.0%} del bankroll) y mantener ${cash_reserved:,.2f} sin "
        "asignar. El tamano de cada jugada ya incorpora un margen de seguridad (Kelly "
        "fraccionado con topes); no lo aumentes manualmente pensando que 'total confianza' "
        "significa apostar mas."
    )

    return PortfolioRecommendation(
        date=date,
        bankroll=bankroll,
        has_recommendation=True,
        summary=summary,
        allocations=allocations,
        total_deployed_dollars=round(total_deployed, 2),
        cash_reserved_dollars=cash_reserved,
    )
