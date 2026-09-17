"""Prueba manual (no automatizada) contra la API publica real de Kalshi."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connectors.kalshi_client import KalshiClient, Settlement  # noqa: E402


def test_settlement_net_result_matches_real_account() -> None:
    """Regresion: net_result_dollars debe restar tambien la comision.

    Valores tomados de 6 liquidaciones reales de una cuenta de prueba,
    comparados contra el "Total return" que muestra kalshi.com. Sin restar
    fee_cost_dollars, el neto calculado quedaba sistematicamente mas alto
    que el real (la comision faltante).
    """
    cases = [
        # (cost, fee, revenue, neto_real_esperado)
        (14.95, 0.03, 15.36, 0.38),
        (9.65, 0.35, 19.74, 9.74),
        (9.84, 0.16, 12.71, 2.72),
        (1.94, 0.06, 3.46, 1.46),
        (1.99, 0.01, 2.09, 0.10),
        (1.98, 0.01, 2.20, 0.21),
    ]
    for cost, fee, revenue, expected_net in cases:
        settlement = Settlement(
            ticker="TEST", event_ticker="TEST", market_result="yes",
            yes_count=1.0, yes_total_cost_dollars=cost,
            no_count=0.0, no_total_cost_dollars=0.0,
            revenue_dollars=revenue, fee_cost_dollars=fee, settled_time=None,
        )
        assert abs(settlement.net_result_dollars - expected_net) < 0.02, (
            f"esperado {expected_net}, obtuve {settlement.net_result_dollars}"
        )
    print("OK: net_result_dollars coincide con las 6 liquidaciones reales verificadas.")


def main() -> None:
    test_settlement_net_result_matches_real_account()
    client = KalshiClient()  # sin credenciales: debe funcionar igual (endpoints publicos)
    quotes = client.get_active_markets_for_series("KXNBAGAME")
    print(f"Mercados NBA activos encontrados: {len(quotes)}")
    for q in quotes[:5]:
        print(
            f"  {q.ticker:35s} {q.subtitle:20s} yes_ask={q.yes_ask} "
            f"implied_p={q.implied_yes_probability} status={q.status}"
        )

    # Verificacion de la barrera de seguridad: no debe existir ningun metodo
    # de escritura/orden en el cliente.
    forbidden = [m for m in dir(client) if "order" in m.lower() or "trade" in m.lower()]
    assert not forbidden, f"Se encontraron metodos sospechosos: {forbidden}"
    print("OK: el cliente no expone ningun metodo de ordenes/trading.")


if __name__ == "__main__":
    main()
