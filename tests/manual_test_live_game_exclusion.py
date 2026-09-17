import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.analyzer import _market_already_started  # noqa: E402


def make_market(occurrence_datetime):
    return SimpleNamespace(occurrence_datetime=occurrence_datetime)


def main():
    now = datetime(2026, 9, 17, 20, 0, tzinfo=timezone.utc)

    # Partido que todavia no empieza: se debe seguir analizando.
    future_game = make_market("2026-09-17T23:00:00Z")
    assert _market_already_started(future_game, now) is False

    # Partido que ya arranco: Kalshi lo sigue mostrando como "open" mientras
    # esta en vivo, pero este modulo no sabe leer el marcador, asi que se
    # tiene que excluir para no comparar una probabilidad "de antes del
    # partido" contra un precio que el mercado ya ajusto con lo que paso en
    # la cancha.
    started_game = make_market("2026-09-17T19:00:00Z")
    assert _market_already_started(started_game, now) is True

    # Empieza exactamente ahora: se trata como ya empezado (limite inclusivo).
    starting_now = make_market("2026-09-17T20:00:00Z")
    assert _market_already_started(starting_now, now) is True

    # Sin occurrence_datetime no se puede confirmar nada; no se excluye de mas.
    unknown = make_market(None)
    assert _market_already_started(unknown, now) is False

    print("OK: los partidos ya iniciados se excluyen del analisis en vez de generar una recomendacion ciega al marcador en vivo.")


if __name__ == "__main__":
    main()
