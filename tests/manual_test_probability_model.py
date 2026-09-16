import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connectors.sports_data import InjuryReport, TeamRecord  # noqa: E402
from models.probability_model import ModelWeights, estimate_win_probability  # noqa: E402


def record(abbr, wins, losses, hw=None, hl=None, aw=None, al=None, diff=0.0):
    hw = hw if hw is not None else wins // 2
    hl = hl if hl is not None else losses // 2
    aw = aw if aw is not None else wins - hw
    al = al if al is not None else losses - hl
    return TeamRecord(
        team_id=abbr, abbreviation=abbr, display_name=abbr,
        games_considered=wins + losses, wins=wins, losses=losses,
        home_wins=hw, home_losses=hl, away_wins=aw, away_losses=al,
        avg_point_differential=diff,
    )


def main():
    # Caso 1: sin datos -> debe devolver 50/50, confianza "ninguna"
    empty_a = record("AAA", 0, 0)
    empty_b = record("BBB", 0, 0)
    est = estimate_win_probability(empty_a, empty_b, team_is_home=True)
    assert est.probability == 0.5 and est.confidence == "ninguna"
    print("OK caso sin datos:", est.probability, est.confidence)

    # Caso 2: equipo A muy superior, en casa, con buena muestra -> prob alta
    strong = record("STR", 9, 1, diff=8.5)
    weak = record("WEK", 1, 9, diff=-7.0)
    est = estimate_win_probability(strong, weak, team_is_home=True)
    assert est.probability > 0.75, est.probability
    assert est.confidence == "alta"
    print("OK caso equipo dominante en casa:", round(est.probability, 3), est.confidence)

    # Caso 3: mismo caso pero de visitante -> probabilidad debe bajar un poco
    est_away = estimate_win_probability(strong, weak, team_is_home=False)
    assert est_away.probability < est.probability
    print("OK ventaja de local aplicada:", round(est_away.probability, 3), "<", round(est.probability, 3))

    # Caso 4: equipos parejos pero el rival tiene 2 jugadores clave "Out"
    even_a = record("EVA", 5, 5, diff=0.5)
    even_b = record("EVB", 5, 5, diff=-0.5)
    b_injuries = [
        InjuryReport("EVB", "Jugador 1", "G", "Out", ""),
        InjuryReport("EVB", "Jugador 2", "F", "Out", ""),
    ]
    est_healthy = estimate_win_probability(even_a, even_b, team_is_home=False)
    est_injured = estimate_win_probability(even_a, even_b, team_is_home=False, opponent_injuries=b_injuries)
    assert est_injured.probability > est_healthy.probability
    print("OK ajuste por lesiones del rival:", round(est_injured.probability, 3), ">", round(est_healthy.probability, 3))

    # Caso 5: muestra chica -> confianza baja/media y shrink hacia 50%
    small_a = record("SMA", 2, 0, diff=15.0)
    small_b = record("SMB", 0, 2, diff=-15.0)
    est_small = estimate_win_probability(small_a, small_b, team_is_home=True)
    assert est_small.confidence in ("baja", "media")
    print("OK muestra chica reduce confianza:", est_small.confidence, round(est_small.probability, 3))

    # Caso 6: pitcher abridor -- con peso 0 (default) no debe cambiar nada
    even_a2 = record("EVA", 5, 5, diff=0.0)
    even_b2 = record("EVB", 5, 5, diff=0.0)
    neutral_weights = ModelWeights(recent_form_weight=0.0, point_diff_weight=0.0, home_advantage_logit=0.0, pitcher_era_weight=0.0)
    est_no_weight = estimate_win_probability(even_a2, even_b2, team_is_home=False, weights=neutral_weights, team_pitcher_era=2.50, opponent_pitcher_era=5.50)
    assert est_no_weight.probability == 0.5
    print("OK pitcher_era_weight=0 no tiene efecto:", est_no_weight.probability)

    # Caso 7: con peso configurado, un abridor mejor (ERA mas bajo) debe subir la probabilidad
    pitcher_weights = ModelWeights(recent_form_weight=0.0, point_diff_weight=0.0, home_advantage_logit=0.0, pitcher_era_weight=0.15)
    est_better_pitcher = estimate_win_probability(even_a2, even_b2, team_is_home=False, weights=pitcher_weights, team_pitcher_era=2.50, opponent_pitcher_era=5.50)
    assert est_better_pitcher.probability > 0.5
    print("OK mejor pitcher abridor sube la probabilidad:", round(est_better_pitcher.probability, 3))

    # Caso 8: si falta el ERA de alguno de los dos, el factor no se aplica (no se inventa)
    est_missing_era = estimate_win_probability(even_a2, even_b2, team_is_home=False, weights=pitcher_weights, team_pitcher_era=2.50, opponent_pitcher_era=None)
    assert est_missing_era.probability == 0.5
    assert any("No se pudo confirmar el pitcher" in n for n in est_missing_era.notes)
    print("OK ERA faltante de un lado no inventa el factor:", est_missing_era.probability)

    # Caso 9: un ERA extremo con pocas entradas lanzadas debe pesar mucho menos
    # que el mismo ERA extremo con temporada completa (evita sobre-confiar en
    # una muestra ruidosa, como el caso real: 22 entradas y ERA 9.13).
    est_full_season = estimate_win_probability(
        even_a2, even_b2, team_is_home=False, weights=pitcher_weights,
        team_pitcher_era=2.50, opponent_pitcher_era=9.13,
        team_pitcher_innings=150.0, opponent_pitcher_innings=150.0,
    )
    est_small_sample = estimate_win_probability(
        even_a2, even_b2, team_is_home=False, weights=pitcher_weights,
        team_pitcher_era=2.50, opponent_pitcher_era=9.13,
        team_pitcher_innings=150.0, opponent_pitcher_innings=22.0,
    )
    assert 0.5 < est_small_sample.probability < est_full_season.probability
    print(
        "OK ERA con muestra chica pesa menos:",
        f"temporada completa={est_full_season.probability:.3f}",
        f"muestra chica={est_small_sample.probability:.3f}",
    )

    print("Todas las pruebas del modelo de probabilidad pasaron.")


if __name__ == "__main__":
    main()
