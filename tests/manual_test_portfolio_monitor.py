import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.portfolio_monitor import analyze_portfolio_health  # noqa: E402
from connectors.kalshi_client import MarketPosition, MarketQuote, PortfolioBalance  # noqa: E402


class FakeKalshiClient:
    """Sustituto de KalshiClient para probar la logica sin pegarle a la red ni necesitar credenciales reales."""

    def __init__(self, balance: PortfolioBalance, positions: list[MarketPosition], quotes: list[MarketQuote]):
        self._balance = balance
        self._positions = positions
        self._quotes = {q.ticker: q for q in quotes}

    def get_balance(self) -> PortfolioBalance:
        return self._balance

    def get_positions(self) -> list[MarketPosition]:
        return self._positions

    def get_markets_by_tickers(self, tickers: list[str]) -> list[MarketQuote]:
        return [self._quotes[t] for t in tickers if t in self._quotes]


def make_quote(ticker, yes_bid, yes_ask, no_bid, no_ask, close_time=None, title="Equipo gana"):
    return MarketQuote(
        ticker=ticker, event_ticker=ticker.rsplit("-", 1)[0], series_ticker="KXNBAGAME",
        title=title, subtitle=title, status="active",
        yes_bid=yes_bid, yes_ask=yes_ask, no_bid=no_bid, no_ask=no_ask,
        last_price=yes_bid, volume=100, volume_24h=10, open_interest=50,
        close_time=close_time, occurrence_datetime=close_time,
    )


def main():
    # Caso 1: sin posiciones -> reporte vacio, sin advertencias
    client = FakeKalshiClient(PortfolioBalance(100.0, 100.0, None), [], [])
    report = analyze_portfolio_health(client)
    assert report.positions == []
    assert report.total_value_dollars == 100.0
    assert report.warnings == []
    print("OK caso sin posiciones")

    # Caso 2: una posicion Yes con ganancia, dentro de limites razonables
    quote = make_quote("KXNBAGAME-TEST-AAA", yes_bid=0.60, yes_ask=0.62, no_bid=0.38, no_ask=0.40)
    position = MarketPosition("KXNBAGAME-TEST-AAA", position=10, market_exposure_dollars=4.0, realized_pnl_dollars=0, fees_paid_dollars=0.1, total_traded_dollars=4.0, last_updated_ts=None)
    client = FakeKalshiClient(PortfolioBalance(96.0, 100.0, None), [position], [quote])
    report = analyze_portfolio_health(client)
    p = report.positions[0]
    assert p.side == "Yes"
    assert p.current_price == 0.60  # usa el bid, no el ask
    assert abs(p.current_value_dollars - 6.0) < 1e-9
    assert abs(p.unrealized_pnl_dollars - 2.0) < 1e-9  # 6.0 valor actual - 4.0 costo
    assert not p.closes_soon
    assert report.warnings == []
    print("OK caso posicion Yes con ganancia:", p.unrealized_pnl_dollars)

    # Caso 3: concentracion excesiva -> debe generar advertencia
    quote_big = make_quote("KXNBAGAME-TEST-BIG", yes_bid=0.50, yes_ask=0.52, no_bid=0.48, no_ask=0.50)
    position_big = MarketPosition("KXNBAGAME-TEST-BIG", position=100, market_exposure_dollars=45.0, realized_pnl_dollars=0, fees_paid_dollars=0, total_traded_dollars=45.0, last_updated_ts=None)
    client = FakeKalshiClient(PortfolioBalance(10.0, 55.0, None), [position_big], [quote_big])
    report = analyze_portfolio_health(client)
    assert any("Concentracion alta" in w for w in report.warnings)
    print("OK caso concentracion alta detectada:", report.warnings[0])

    # Caso 4: posicion que cierra pronto -> debe generar advertencia
    soon = (datetime.now(tz=timezone.utc) + timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    quote_soon = make_quote("KXNBAGAME-TEST-SOON", yes_bid=0.50, yes_ask=0.52, no_bid=0.48, no_ask=0.50, close_time=soon)
    position_soon = MarketPosition("KXNBAGAME-TEST-SOON", position=5, market_exposure_dollars=2.0, realized_pnl_dollars=0, fees_paid_dollars=0, total_traded_dollars=2.0, last_updated_ts=None)
    client = FakeKalshiClient(PortfolioBalance(90.0, 100.0, None), [position_soon], [quote_soon])
    report = analyze_portfolio_health(client)
    assert any("Cierra pronto" in w for w in report.warnings)
    print("OK caso cierre inminente detectado:", [w for w in report.warnings if "Cierra pronto" in w])

    # Caso 5: no se puede verificar el precio (mercado ya no aparece) -> advertencia, no crash
    position_gone = MarketPosition("KXNBAGAME-TEST-GONE", position=3, market_exposure_dollars=1.5, realized_pnl_dollars=0, fees_paid_dollars=0, total_traded_dollars=1.5, last_updated_ts=None)
    client = FakeKalshiClient(PortfolioBalance(90.0, 100.0, None), [position_gone], [])
    report = analyze_portfolio_health(client)
    assert report.positions[0].current_price is None
    assert any("No se pudo verificar" in w for w in report.warnings)
    print("OK caso precio no verificable manejado sin crash")

    print("\nTodas las pruebas de portfolio_monitor pasaron.")


if __name__ == "__main__":
    main()
