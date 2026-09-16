"""Registro central de categorías de mercado soportadas.

Agregar una categoría nueva (por ejemplo MLB o NHL) consiste en añadir una
entrada aquí -- ningun otro modulo necesita cambios estructurales. Cada
entrada conecta:
  - el ticker de serie de Kalshi que agrupa los mercados de "ganador del
    partido" para esa liga (mercado binario, uno por equipo),
  - el slug de deporte/liga que usa la API publica de ESPN para traer
    estadisticas, calendarios y lesiones.

Limitacion conocida: se asume que cada liga tiene una serie de Kalshi de
"ganador de partido" (patron KX<LIGA>GAME con un mercado binario por
equipo). Si una liga futura no sigue ese patron, hay que adaptar
connectors/kalshi_client.py o agregar un campo extra aqui, pero el resto
del pipeline (analysis/, models/) no deberia necesitar cambios.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LeagueConfig:
    key: str
    display_name: str
    kalshi_series_ticker: str
    espn_sport_slug: str
    espn_league_slug: str
    uses_starting_pitcher: bool = False  # solo tiene sentido en beisbol


LEAGUES: dict[str, LeagueConfig] = {
    "NBA": LeagueConfig(
        key="NBA",
        display_name="NBA",
        kalshi_series_ticker="KXNBAGAME",
        espn_sport_slug="basketball",
        espn_league_slug="nba",
    ),
    "NFL": LeagueConfig(
        key="NFL",
        display_name="NFL",
        kalshi_series_ticker="KXNFLGAME",
        espn_sport_slug="football",
        espn_league_slug="nfl",
    ),
    "MLB": LeagueConfig(
        key="MLB",
        display_name="MLB",
        kalshi_series_ticker="KXMLBGAME",
        espn_sport_slug="baseball",
        espn_league_slug="mlb",
        uses_starting_pitcher=True,
    ),
    "NHL": LeagueConfig(
        key="NHL",
        display_name="NHL",
        kalshi_series_ticker="KXNHLGAME",
        espn_sport_slug="hockey",
        espn_league_slug="nhl",
    ),
}


def get_league(key: str) -> LeagueConfig:
    try:
        return LEAGUES[key.upper()]
    except KeyError as exc:
        raise ValueError(
            f"Liga '{key}' no esta registrada. Ligas disponibles: {list(LEAGUES)}"
        ) from exc
