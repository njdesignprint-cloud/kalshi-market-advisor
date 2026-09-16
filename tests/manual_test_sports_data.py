import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connectors.sports_data import EspnSportsDataClient  # noqa: E402


def main() -> None:
    nba = EspnSportsDataClient("basketball", "nba")

    team = nba.resolve_kalshi_code("SAS")
    assert team is not None, "No se pudo resolver SAS (San Antonio Spurs)"
    print("Resuelto SAS ->", team["displayName"], "id:", team["id"])

    record = nba.get_recent_record(team["id"], last_n=10)
    print(record)
    print("win_pct:", record.win_pct, "home_win_pct:", record.home_win_pct)

    injuries = nba.get_injuries_by_team_id()
    print(f"Equipos con reporte de lesiones: {len(injuries)}")
    sample_team_id = team["id"]
    print(f"Lesiones para {team['displayName']}:", injuries.get(sample_team_id, []))

    nyk = nba.resolve_kalshi_code("NYK")
    assert nyk is not None and nyk["abbreviation"] == "NY"
    print("Resuelto NYK -> abreviatura ESPN:", nyk["abbreviation"])

    unknown = nba.resolve_kalshi_code("ZZZ")
    assert unknown is None
    print("OK: codigo desconocido devuelve None en vez de adivinar.")


if __name__ == "__main__":
    main()
