"""Backtesting y recalibracion del modelo de probabilidad (MLB 2026).

Este script NO es parte del pipeline de produccion (no lo llama main.py).
Responde dos preguntas:
  1. Calibracion: si hubieramos usado el modelo para estimar la
     probabilidad de cada partido ya jugado esta temporada, que tan bien
     calibradas hubieran estado esas probabilidades contra lo que
     realmente paso?
  2. Recalibracion (--search): si los pesos por defecto no calibran bien
     para una liga, cuales pesos SI calibran, elegidos con un split
     cronologico entrenamiento/prueba (para no sobreajustar el mismo
     conjunto que se usa para reportar el resultado final)?

METODOLOGIA: para cada partido ya completado, se reconstruye el record de
forma reciente de ambos equipos usando UNICAMENTE partidos anteriores a la
fecha de ese partido (para no hacer "trampa" con informacion del futuro).

LIMITACION IMPORTANTE: esto valida la CALIBRACION del modelo (si dice 65%,
gana como el 65% de las veces?), NO valida si hubiera sido rentable contra
los precios reales de Kalshi en ese momento (la API publica de Kalshi no
expone precios historicos por fecha pasada de forma sencilla). Un modelo
bien calibrado es una condicion necesaria pero no suficiente para que la
herramienta genere valor esperado positivo -- tambien hace falta que el
mercado se haya equivocado en la direccion correcta.

Tambien excluye el ajuste por lesiones (la API de ESPN solo expone el
estado de lesiones ACTUAL, no historico para una fecha pasada).

Uso:
    python tests/backtest_probability_model.py                 # calibracion con pesos actuales de la liga
    python tests/backtest_probability_model.py --search        # grid search train/test para recalibrar
    python tests/backtest_probability_model.py --league NBA    # otra liga (si ya tiene suficientes partidos)
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timedelta, timezone

from config import get_league  # noqa: E402
from connectors.sports_data import EspnSportsDataClient, ProbablePitcher, TeamRecord  # noqa: E402
from models.probability_model import MLB_WEIGHTS, ModelWeights, get_weights_for_league  # noqa: E402
from models.probability_model import estimate_win_probability  # noqa: E402

RECENT_GAMES_WINDOW = 10
MIN_PRIOR_GAMES_TO_EVALUATE = 3  # partidos previos minimos para que valga la pena evaluar
TRAIN_FRACTION = 0.7


@dataclass
class GameSample:
    date: str
    home_team: str
    away_team: str
    home_record: TeamRecord
    away_record: TeamRecord
    actual_home_won: int
    home_pitcher_era: float | None = None
    away_pitcher_era: float | None = None


def collect_game_samples(espn: EspnSportsDataClient) -> list[GameSample]:
    """Reconstruye, para cada partido ya jugado, los records 'tal como se conocian' antes de ese partido."""
    teams = espn.list_teams()
    schedules: dict[str, dict] = {team["id"]: espn.get_schedule_raw(team["id"]) for team in teams.values()}

    samples: list[GameSample] = []
    seen_game_keys: set[str] = set()

    for team in teams.values():
        team_id = team["id"]
        schedule = schedules[team_id]
        for event in schedule.get("events", []):
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            comp = competitions[0]
            if not comp.get("status", {}).get("type", {}).get("completed"):
                continue
            competitors = comp.get("competitors", [])
            this_c = next((c for c in competitors if c.get("team", {}).get("id") == str(team_id)), None)
            opp_c = next((c for c in competitors if c.get("team", {}).get("id") != str(team_id)), None)
            if this_c is None or opp_c is None:
                continue
            opponent_id = opp_c.get("team", {}).get("id")
            if opponent_id not in schedules:
                continue

            game_date = event.get("date", "")
            game_key = "|".join(sorted([team_id, opponent_id])) + "|" + game_date
            is_home = this_c.get("homeAway") == "home"
            if game_key in seen_game_keys or not is_home:
                continue
            seen_game_keys.add(game_key)

            try:
                own_score = float(this_c.get("score", {}).get("value", this_c.get("score")))
                opp_score = float(opp_c.get("score", {}).get("value", opp_c.get("score")))
            except (TypeError, ValueError):
                continue

            home_record = espn.summarize_recent_record(schedule, team_id, last_n=RECENT_GAMES_WINDOW, before_date=game_date)
            away_record = espn.summarize_recent_record(schedules[opponent_id], opponent_id, last_n=RECENT_GAMES_WINDOW, before_date=game_date)

            if min(home_record.games_considered, away_record.games_considered) < MIN_PRIOR_GAMES_TO_EVALUATE:
                continue

            away_team_abbr = next((t["abbreviation"] for t in teams.values() if t["id"] == opponent_id), "?")
            samples.append(
                GameSample(
                    date=game_date,
                    home_team=team["abbreviation"],
                    away_team=away_team_abbr,
                    home_record=home_record,
                    away_record=away_record,
                    actual_home_won=1 if own_score > opp_score else 0,
                )
            )

    samples.sort(key=lambda s: s.date)
    return samples


def predict_all(samples: list[GameSample], weights: ModelWeights) -> list[dict]:
    out = []
    for s in samples:
        estimate = estimate_win_probability(
            s.home_record, s.away_record, team_is_home=True, weights=weights,
            team_pitcher_era=s.home_pitcher_era, opponent_pitcher_era=s.away_pitcher_era,
        )
        out.append(
            {
                "predicted_prob_home_wins": estimate.probability,
                "confidence": estimate.confidence,
                "actual_home_won": s.actual_home_won,
            }
        )
    return out


def collect_recent_game_samples_with_pitchers(espn: EspnSportsDataClient, days_back: int = 14) -> list[GameSample]:
    """Como collect_game_samples, pero solo mira los ultimos `days_back` dias
    y ademas trae el pitcher abridor probable/confirmado de cada equipo.

    LIMITACION (ver ProbablePitcher en connectors/sports_data.py): el ERA
    que devuelve ESPN para un partido pasado es el acumulado A HOY, no "tal
    como se sabia" en ese momento -- incluye starts posteriores al partido.
    Por eso esta funcion se limita deliberadamente a una ventana corta y
    reciente: en una temporada de ~25-30 starts por abridor, que 1-2 de
    esos starts sean "del futuro" mueve el ERA acumulado muy poco. Es una
    aproximacion aceptada, no una reconstruccion perfecta -- tratar este
    backtest especifico como menos riguroso que el de collect_game_samples.
    """
    teams = espn.list_teams()
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days_back)).isoformat()
    schedules: dict[str, dict] = {team["id"]: espn.get_schedule_raw(team["id"]) for team in teams.values()}

    samples: list[GameSample] = []
    seen_game_keys: set[str] = set()
    pitcher_cache: dict[str, dict[str, ProbablePitcher]] = {}  # event_id -> {team_id: ProbablePitcher}

    for team in teams.values():
        team_id = team["id"]
        schedule = schedules[team_id]
        for event in schedule.get("events", []):
            if event.get("date", "") < cutoff:
                continue
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            comp = competitions[0]
            if not comp.get("status", {}).get("type", {}).get("completed"):
                continue
            competitors = comp.get("competitors", [])
            this_c = next((c for c in competitors if c.get("team", {}).get("id") == str(team_id)), None)
            opp_c = next((c for c in competitors if c.get("team", {}).get("id") != str(team_id)), None)
            if this_c is None or opp_c is None:
                continue
            opponent_id = opp_c.get("team", {}).get("id")
            if opponent_id not in schedules:
                continue

            game_date = event.get("date", "")
            game_key = "|".join(sorted([team_id, opponent_id])) + "|" + game_date
            is_home = this_c.get("homeAway") == "home"
            if game_key in seen_game_keys or not is_home:
                continue
            seen_game_keys.add(game_key)

            try:
                own_score = float(this_c.get("score", {}).get("value", this_c.get("score")))
                opp_score = float(opp_c.get("score", {}).get("value", opp_c.get("score")))
            except (TypeError, ValueError):
                continue

            home_record = espn.summarize_recent_record(schedule, team_id, last_n=RECENT_GAMES_WINDOW, before_date=game_date)
            away_record = espn.summarize_recent_record(schedules[opponent_id], opponent_id, last_n=RECENT_GAMES_WINDOW, before_date=game_date)
            if min(home_record.games_considered, away_record.games_considered) < MIN_PRIOR_GAMES_TO_EVALUATE:
                continue

            event_id = event.get("id")
            if event_id not in pitcher_cache:
                try:
                    pitcher_cache[event_id] = espn.get_probable_pitchers(event_id)
                except Exception:
                    pitcher_cache[event_id] = {}
            pitchers = pitcher_cache[event_id]
            home_pitcher = pitchers.get(team_id)
            away_pitcher = pitchers.get(opponent_id)

            away_team_abbr = next((t["abbreviation"] for t in teams.values() if t["id"] == opponent_id), "?")
            samples.append(
                GameSample(
                    date=game_date,
                    home_team=team["abbreviation"],
                    away_team=away_team_abbr,
                    home_record=home_record,
                    away_record=away_record,
                    actual_home_won=1 if own_score > opp_score else 0,
                    home_pitcher_era=home_pitcher.era if home_pitcher else None,
                    away_pitcher_era=away_pitcher.era if away_pitcher else None,
                )
            )

    samples.sort(key=lambda s: s.date)
    return samples


def brier_score(preds: list[dict]) -> float:
    return sum((p["predicted_prob_home_wins"] - p["actual_home_won"]) ** 2 for p in preds) / len(preds)


def log_loss(preds: list[dict]) -> float:
    total = 0.0
    for p in preds:
        prob = min(max(p["predicted_prob_home_wins"], 1e-6), 1 - 1e-6)
        y = p["actual_home_won"]
        total += -(y * math.log(prob) + (1 - y) * math.log(1 - prob))
    return total / len(preds)


def calibration_table(preds: list[dict], n_bins: int = 10) -> list[tuple[str, int, float, float]]:
    bins: dict[int, list[dict]] = defaultdict(list)
    for p in preds:
        idx = min(int(p["predicted_prob_home_wins"] * n_bins), n_bins - 1)
        bins[idx].append(p)
    rows = []
    for idx in sorted(bins):
        bucket = bins[idx]
        avg_pred = sum(p["predicted_prob_home_wins"] for p in bucket) / len(bucket)
        actual_rate = sum(p["actual_home_won"] for p in bucket) / len(bucket)
        rows.append((f"{idx/n_bins:.1f}-{(idx+1)/n_bins:.1f}", len(bucket), avg_pred, actual_rate))
    return rows


def report(preds: list[dict], label: str) -> None:
    if not preds:
        print(f"[{label}] sin partidos para evaluar.")
        return
    home_win_rate = sum(p["actual_home_won"] for p in preds) / len(preds)
    naive_home_brier = sum((home_win_rate - p["actual_home_won"]) ** 2 for p in preds) / len(preds)
    print(f"--- {label} (n={len(preds)}) ---")
    print(f"  Brier score del modelo:               {brier_score(preds):.4f}")
    print(f"  Brier score 'siempre 50/50':           0.2500")
    print(f"  Brier score 'siempre {home_win_rate:.0%} (tasa real de local)': {naive_home_brier:.4f}")
    print(f"  Log loss del modelo:                   {log_loss(preds):.4f}")
    by_conf: dict[str, list[dict]] = defaultdict(list)
    for p in preds:
        by_conf[p["confidence"]].append(p)
    for conf in ("alta", "media", "baja", "ninguna"):
        bucket = by_conf.get(conf, [])
        if not bucket:
            continue
        acc = sum(1 for p in bucket if (p["predicted_prob_home_wins"] > 0.5) == bool(p["actual_home_won"])) / len(bucket)
        print(f"    confianza {conf:8s}: n={len(bucket):4d}  brier={brier_score(bucket):.4f}  accuracy={acc:.1%}")
    print(f"  {'bucket':10s} {'n':>5s} {'pred.':>8s} {'real':>8s}")
    for label_bin, n, avg_pred, actual_rate in calibration_table(preds):
        print(f"  {label_bin:10s} {n:5d} {avg_pred:7.1%} {actual_rate:7.1%}")
    print()


def grid_search(train_samples: list[GameSample]) -> ModelWeights:
    form_candidates = [0.0, 0.2, 0.4, 0.6, 0.8, 1.2, 1.6]
    diff_candidates = [0.0, 0.005, 0.01, 0.02, 0.03, 0.045]
    home_win_rate = sum(s.actual_home_won for s in train_samples) / len(train_samples)
    empirical_home_logit = math.log(home_win_rate / (1 - home_win_rate))
    home_candidates = sorted({0.0, round(empirical_home_logit, 3), 0.15, 0.25})

    best_weights = None
    best_brier = float("inf")
    for form_w in form_candidates:
        for diff_w in diff_candidates:
            for home_w in home_candidates:
                weights = ModelWeights(
                    recent_form_weight=form_w,
                    point_diff_weight=diff_w,
                    home_advantage_logit=home_w,
                    injury_penalty_per_out_starter=0.12,
                )
                preds = predict_all(train_samples, weights)
                b = brier_score(preds)
                if b < best_brier:
                    best_brier = b
                    best_weights = weights

    print(f"Tasa real de victoria de local en TRAIN: {home_win_rate:.1%} (logit empirico: {empirical_home_logit:.3f})")
    print(f"Mejor combinacion encontrada en TRAIN (brier={best_brier:.4f}): {best_weights}\n")
    return best_weights


def pitcher_grid_search(days_back: int = 14) -> None:
    """Backtest enfocado del factor de pitcher abridor (solo MLB, ventana corta).

    Ver el docstring de collect_recent_game_samples_with_pitchers para la
    limitacion de sesgo de informacion futura que justifica usar una
    ventana corta en vez del dataset completo de la temporada.
    """
    league = get_league("MLB")
    espn = EspnSportsDataClient(league.espn_sport_slug, league.espn_league_slug)

    print(f"Recolectando los ultimos {days_back} dias de partidos de MLB con pitcher abridor confirmado...\n")
    samples = collect_recent_game_samples_with_pitchers(espn, days_back=days_back)
    with_pitchers = [s for s in samples if s.home_pitcher_era is not None and s.away_pitcher_era is not None]
    print(f"Partidos en la ventana: {len(samples)}. Con pitcher confirmado en ambos lados: {len(with_pitchers)}.\n")
    if len(with_pitchers) < 20:
        print("Muy pocos partidos con pitcher confirmado en esta ventana para un backtest confiable. Prueba con --pitcher-search-days mas grande.")
        return

    split_idx = int(len(with_pitchers) * TRAIN_FRACTION)
    train_samples, test_samples = with_pitchers[:split_idx], with_pitchers[split_idx:]
    print(f"Split cronologico: {len(train_samples)} entrenamiento, {len(test_samples)} prueba (fuera de muestra)\n")

    baseline_weights = MLB_WEIGHTS  # pitcher_era_weight=0.0 en el default actual
    print("=== Sin factor de pitcher (pesos MLB actuales), evaluado en TEST ===")
    report(predict_all(test_samples, baseline_weights), "MLB - sin pitcher - TEST")

    candidates = [0.0, 0.03, 0.06, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0]
    best_weight, best_brier = 0.0, float("inf")
    for candidate in candidates:
        weights = ModelWeights(
            recent_form_weight=MLB_WEIGHTS.recent_form_weight,
            point_diff_weight=MLB_WEIGHTS.point_diff_weight,
            home_advantage_logit=MLB_WEIGHTS.home_advantage_logit,
            injury_penalty_per_out_starter=MLB_WEIGHTS.injury_penalty_per_out_starter,
            pitcher_era_weight=candidate,
        )
        b = brier_score(predict_all(train_samples, weights))
        print(f"  pitcher_era_weight={candidate:.2f} -> brier en TRAIN = {b:.4f}")
        if b < best_brier:
            best_brier, best_weight = b, candidate

    print(f"\nMejor pitcher_era_weight en TRAIN: {best_weight} (brier={best_brier:.4f})\n")
    best_weights_obj = ModelWeights(
        recent_form_weight=MLB_WEIGHTS.recent_form_weight,
        point_diff_weight=MLB_WEIGHTS.point_diff_weight,
        home_advantage_logit=MLB_WEIGHTS.home_advantage_logit,
        injury_penalty_per_out_starter=MLB_WEIGHTS.injury_penalty_per_out_starter,
        pitcher_era_weight=best_weight,
    )
    print("=== Con ese pitcher_era_weight, evaluado en TEST (fuera de muestra, lo que importa) ===")
    report(predict_all(test_samples, best_weights_obj), "MLB - con pitcher - TEST")
    print(
        "Nota: ventana corta y reciente para acotar el sesgo de informacion futura del ERA "
        "(ver docstring de collect_recent_game_samples_with_pitchers). Con una muestra chica, "
        "trata este resultado con mas cautela que el backtest completo de la temporada."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="MLB")
    parser.add_argument("--search", action="store_true", help="Corre grid search train/test para recalibrar pesos")
    parser.add_argument("--pitcher-search", action="store_true", help="Backtest enfocado del factor de pitcher abridor (solo MLB)")
    parser.add_argument("--pitcher-search-days", type=int, default=14, help="Ventana de dias hacia atras para --pitcher-search")
    args = parser.parse_args()

    if args.pitcher_search:
        pitcher_grid_search(days_back=args.pitcher_search_days)
        return

    league = get_league(args.league)
    espn = EspnSportsDataClient(league.espn_sport_slug, league.espn_league_slug)

    print(f"Recolectando partidos historicos reales de {args.league} (esto puede tardar unos segundos)...\n")
    samples = collect_game_samples(espn)
    print(f"Partidos utilizables: {len(samples)}\n")
    if not samples:
        print("No hay suficientes partidos completados para evaluar todavia.")
        return

    if not args.search:
        current_weights = get_weights_for_league(args.league)
        print(f"Pesos actuales para {args.league}: {current_weights}\n")
        report(predict_all(samples, current_weights), f"{args.league} - pesos actuales, todo el dataset")
        return

    split_idx = int(len(samples) * TRAIN_FRACTION)
    train_samples, test_samples = samples[:split_idx], samples[split_idx:]
    print(f"Split cronologico: {len(train_samples)} partidos de entrenamiento, {len(test_samples)} de prueba (fuera de muestra)\n")

    print("=== Pesos por defecto (heuristicos), evaluados en TEST ===")
    report(predict_all(test_samples, ModelWeights()), f"{args.league} - pesos por defecto - TEST")

    print("=== Grid search en TRAIN ===")
    best_weights = grid_search(train_samples)

    print("=== Pesos recalibrados, evaluados en TRAIN (referencia) ===")
    report(predict_all(train_samples, best_weights), f"{args.league} - pesos recalibrados - TRAIN")

    print("=== Pesos recalibrados, evaluados en TEST (fuera de muestra, lo que importa) ===")
    report(predict_all(test_samples, best_weights), f"{args.league} - pesos recalibrados - TEST")

    print(f"Pesos recalibrados finales para {args.league}: {best_weights}")
    print(
        "\nNota: esto mide calibracion contra resultados reales, no rentabilidad contra "
        "Kalshi (no se evaluaron precios historicos de mercado). Un modelo bien calibrado "
        "es necesario pero no suficiente para generar valor esperado positivo real."
    )


if __name__ == "__main__":
    main()
