"""Monitorea la salud del portafolio REAL del usuario en Kalshi (solo lectura).

Lee el balance y las posiciones abiertas de TU cuenta (requiere que hayas
configurado tu propia API key en .env) y calcula senales de riesgo
simples y explicables: concentracion excesiva en una sola posicion, y
posiciones que cierran pronto. No sugiere comprar ni vender nada -- solo
describe lo que ya tienes y por que podria merecer tu atencion. Ninguna
funcion de este modulo coloca ni modifica ordenes.

LIMITACION IMPORTANTE sobre la ganancia/perdida no realizada: Kalshi no
expone un "costo promedio por contrato" directo en el endpoint de
posiciones. Se aproxima asi:
  valor_actual = contratos * precio_de_venta_actual (bid, no ask -- es lo
                 que realmente recibirias si cerraras la posicion ahora)
  pnl_no_realizado_estimado = valor_actual - market_exposure_dollars
donde market_exposure_dollars es el campo que Kalshi reporta como el
costo de la posicion abierta. Es una aproximacion razonable segun la
documentacion publica de Kalshi, pero no fue verificada contra una cuenta
real con historial complejo (ordenes parciales, promediado de precio,
etc.). Verifica estos numeros contra tu cuenta real antes de confiar en
ellos por completo -- si notas una discrepancia, es una senal para
ajustar esta formula, no para ignorarla.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from connectors.kalshi_client import KalshiClient, MarketPosition, PortfolioBalance

DEFAULT_CONCENTRATION_THRESHOLD = 0.25  # una sola posicion no deberia superar el 25% del portafolio
DEFAULT_CLOSING_SOON_HOURS = 48.0


@dataclass
class PositionHealth:
    ticker: str
    title: str
    subtitle: str
    side: str  # "Yes" | "No"
    contracts: float
    market_exposure_dollars: float
    current_price: float | None
    current_value_dollars: float | None
    unrealized_pnl_dollars: float | None
    close_time: str | None
    closes_soon: bool
    concentration_pct: float | None
    price_verified: bool  # False si el mercado ya no aparece en la API (cerrado/liquidado)


@dataclass
class PortfolioHealthReport:
    generated_at: str
    balance_dollars: float
    total_value_dollars: float
    positions: list[PositionHealth] = field(default_factory=list)
    total_unrealized_pnl_dollars: float | None = None
    warnings: list[str] = field(default_factory=list)


def _closes_soon(close_time: str | None, hours: float) -> bool:
    if not close_time:
        return False
    try:
        close_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
    except ValueError:
        return False
    return close_dt <= datetime.now(tz=timezone.utc) + timedelta(hours=hours)


def analyze_portfolio_health(
    client: KalshiClient,
    concentration_threshold: float = DEFAULT_CONCENTRATION_THRESHOLD,
    closing_soon_hours: float = DEFAULT_CLOSING_SOON_HOURS,
) -> PortfolioHealthReport:
    """Trae balance + posiciones reales y arma un reporte de salud, sin operar nada."""
    balance: PortfolioBalance = client.get_balance()
    positions: list[MarketPosition] = client.get_positions()

    if not positions:
        return PortfolioHealthReport(
            generated_at=datetime.now(tz=timezone.utc).isoformat(),
            balance_dollars=balance.balance_dollars,
            total_value_dollars=balance.balance_dollars,
            positions=[],
            total_unrealized_pnl_dollars=0.0,
            warnings=[],
        )

    quotes_by_ticker = {q.ticker: q for q in client.get_markets_by_tickers([p.ticker for p in positions])}

    position_healths: list[PositionHealth] = []
    for position in positions:
        side = "Yes" if position.position > 0 else "No"
        contracts = abs(position.position)
        quote = quotes_by_ticker.get(position.ticker)
        current_price: float | None = None
        price_verified = quote is not None
        if quote is not None:
            current_price = quote.yes_bid if side == "Yes" else quote.no_bid
        current_value = (contracts * current_price) if current_price is not None else None
        unrealized_pnl = (current_value - position.market_exposure_dollars) if current_value is not None else None
        position_healths.append(
            PositionHealth(
                ticker=position.ticker,
                title=quote.title if quote else "",
                subtitle=quote.subtitle if quote else "",
                side=side,
                contracts=contracts,
                market_exposure_dollars=position.market_exposure_dollars,
                current_price=current_price,
                current_value_dollars=current_value,
                unrealized_pnl_dollars=unrealized_pnl,
                close_time=quote.close_time if quote else None,
                closes_soon=_closes_soon(quote.close_time if quote else None, closing_soon_hours),
                concentration_pct=None,  # se completa abajo, una vez que sabemos el total
                price_verified=price_verified,
            )
        )

    total_positions_value = sum(
        (p.current_value_dollars if p.current_value_dollars is not None else p.market_exposure_dollars)
        for p in position_healths
    )
    total_value = balance.balance_dollars + total_positions_value
    for p in position_healths:
        value_for_concentration = p.current_value_dollars if p.current_value_dollars is not None else p.market_exposure_dollars
        p.concentration_pct = (value_for_concentration / total_value) if total_value > 0 else None

    warnings: list[str] = []
    for p in position_healths:
        label = f"{p.ticker} ({p.subtitle or p.title or 'sin titulo'})"
        if p.concentration_pct is not None and p.concentration_pct > concentration_threshold:
            warnings.append(
                f"Concentracion alta: {label} representa el {p.concentration_pct:.0%} de tu portafolio "
                f"(mas del {concentration_threshold:.0%} recomendado como maximo en una sola jugada)."
            )
        if p.closes_soon:
            warnings.append(f"Cierra pronto (dentro de {closing_soon_hours:.0f}h): {label}, cierra {p.close_time}.")
        if not p.price_verified:
            warnings.append(
                f"No se pudo verificar el precio actual de {label} (puede que el mercado ya haya cerrado o liquidado); "
                "revisa esta posicion manualmente en kalshi.com."
            )

    total_unrealized = None
    if all(p.unrealized_pnl_dollars is not None for p in position_healths):
        total_unrealized = sum(p.unrealized_pnl_dollars for p in position_healths)  # type: ignore[misc]

    return PortfolioHealthReport(
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        balance_dollars=balance.balance_dollars,
        total_value_dollars=total_value,
        positions=position_healths,
        total_unrealized_pnl_dollars=total_unrealized,
        warnings=warnings,
    )
