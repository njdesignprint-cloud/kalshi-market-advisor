"""Prueba manual (no automatizada) contra la API publica real de Kalshi."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connectors.kalshi_client import KalshiClient  # noqa: E402


def main() -> None:
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
