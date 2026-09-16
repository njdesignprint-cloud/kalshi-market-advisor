"""Modelo de probabilidad para partidos de un-contra-uno (NBA/NFL).

QUE HACE: combina tres senales objetivas y publicas -- forma reciente
(record de los ultimos N partidos), ventaja de local/visitante, y
diferencial de puntos promedio -- en una probabilidad estimada de que un
equipo gane su proximo partido. Aplica un ajuste pequeno y conservador por
lesiones de jugadores marcados como "Out".

SUPUESTOS (explicitos):
  1. Los pesos que combinan cada senal (RECENT_FORM_WEIGHT,
     POINT_DIFF_WEIGHT, HOME_ADVANTAGE_LOGIT, INJURY_PENALTY_PER_OUT_STARTER)
     son heuristicos, elegidos por razonabilidad estadistica general, NO
     ajustados (fitted) contra datos historicos de resultados reales. Este
     modelo no ha sido validado con backtesting.
  2. "Forma reciente" usa como maximo los ultimos RECENT_GAMES_WINDOW
     partidos finalizados. No pondera la calidad del rival vencido/perdido
     (una racha contra rivales debiles pesa igual que contra rivales
     fuertes).
  3. El ajuste por lesiones es una señal cruda: resta una penalizacion fija
     por cada jugador en estado "Out" segun ESPN, sin intentar cuantificar
     el impacto real de ese jugador especifico en el resultado. No
     distingue una estrella de un suplente.
  4. No incorpora informacion de mercado (lineas de casas de apuestas,
     poder de las propias probabilidades implicitas de Kalshi de partidos
     pasados, etc.), ni lesiones de ultima hora que ocurran despues de que
     se genero el reporte.
  5. Cuando no hay suficientes partidos recientes para uno de los equipos
     (por ejemplo, en pretemporada), el modelo reduce su confianza y
     empuja la probabilidad hacia 50/50 en vez de inventar una senal.

LIMITACION CENTRAL: este modelo es deliberadamente simple y transparente
para que sus supuestos sean auditables. No es un modelo predictivo de
nivel profesional (no usa Elo ajustado por jugador, no usa datos
avanzados por posesion, no usa lesiones ponderadas por WAR/VORP). Debe
tratarse como una segunda opinion basada en datos objetivos, nunca como
una prediccion confiable por si sola.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from connectors.sports_data import InjuryReport, TeamRecord

RECENT_GAMES_WINDOW = 10
MIN_GAMES_FOR_FULL_CONFIDENCE = 5

RECENT_FORM_WEIGHT = 1.6         # peso del diferencial de win% reciente
POINT_DIFF_WEIGHT = 0.045        # peso del diferencial de puntos promedio (por punto)
HOME_ADVANTAGE_LOGIT = 0.25      # ventaja fija de local, en escala logit
INJURY_PENALTY_PER_OUT_STARTER = 0.12  # penalizacion en escala logit por jugador "Out"

_MAX_OUT_PLAYERS_COUNTED = 3  # evita que un reporte ruidoso hunda la probabilidad a 0


@dataclass
class ProbabilityEstimate:
    team_abbreviation: str
    opponent_abbreviation: str
    is_home: bool
    probability: float           # probabilidad estimada de que `team` gane
    confidence: str              # "alta" | "media" | "baja" | "ninguna"
    components: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z))


def _count_out_players(injuries: list[InjuryReport]) -> int:
    return min(sum(1 for i in injuries if i.status.lower() == "out"), _MAX_OUT_PLAYERS_COUNTED)


def estimate_win_probability(
    team: TeamRecord,
    opponent: TeamRecord,
    team_is_home: bool,
    team_injuries: list[InjuryReport] | None = None,
    opponent_injuries: list[InjuryReport] | None = None,
) -> ProbabilityEstimate:
    """Estima P(team gana) usando forma reciente, local/visitante y lesiones.

    Ver docstring del modulo para los supuestos y limitaciones completos.
    """
    team_injuries = team_injuries or []
    opponent_injuries = opponent_injuries or []
    notes: list[str] = []

    team_games = team.games_considered
    opp_games = opponent.games_considered

    if team_games == 0 and opp_games == 0:
        notes.append(
            "Ninguno de los dos equipos tiene partidos recientes finalizados "
            "disponibles (probable pretemporada o inicio de temporada). "
            "Probabilidad fijada en 50/50 por falta de datos."
        )
        return ProbabilityEstimate(
            team_abbreviation=team.abbreviation,
            opponent_abbreviation=opponent.abbreviation,
            is_home=team_is_home,
            probability=0.5,
            confidence="ninguna",
            components={},
            notes=notes,
        )

    team_win_pct = team.win_pct if team.win_pct is not None else 0.5
    opp_win_pct = opponent.win_pct if opponent.win_pct is not None else 0.5

    form_component = RECENT_FORM_WEIGHT * (team_win_pct - opp_win_pct)
    point_diff_component = POINT_DIFF_WEIGHT * (team.avg_point_differential - opponent.avg_point_differential)
    home_component = HOME_ADVANTAGE_LOGIT if team_is_home else -HOME_ADVANTAGE_LOGIT

    team_out = _count_out_players(team_injuries)
    opp_out = _count_out_players(opponent_injuries)
    injury_component = INJURY_PENALTY_PER_OUT_STARTER * (opp_out - team_out)
    if team_out or opp_out:
        notes.append(
            f"Ajuste por lesiones: {team.abbreviation} tiene {team_out} jugador(es) 'Out', "
            f"{opponent.abbreviation} tiene {opp_out}. Ajuste crudo, no pondera importancia del jugador."
        )

    z = form_component + point_diff_component + home_component + injury_component
    raw_probability = _sigmoid(z)

    min_games = min(team_games, opp_games)
    if min_games >= MIN_GAMES_FOR_FULL_CONFIDENCE:
        confidence = "alta"
        shrink = 0.0
    elif min_games >= 2:
        confidence = "media"
        shrink = 0.35
        notes.append(
            f"Muestra chica ({min_games} partidos finalizados para el equipo con menos datos). "
            "Probabilidad ajustada hacia 50/50 para reflejar la incertidumbre."
        )
    else:
        confidence = "baja"
        shrink = 0.65
        notes.append(
            f"Muy pocos partidos recientes disponibles ({min_games}). "
            "La estimacion se acerca fuertemente a 50/50 por falta de evidencia."
        )

    probability = raw_probability * (1 - shrink) + 0.5 * shrink

    return ProbabilityEstimate(
        team_abbreviation=team.abbreviation,
        opponent_abbreviation=opponent.abbreviation,
        is_home=team_is_home,
        probability=probability,
        confidence=confidence,
        components={
            "forma_reciente": form_component,
            "diferencial_puntos": point_diff_component,
            "ventaja_local": home_component,
            "ajuste_lesiones": injury_component,
            "probabilidad_sin_ajustar_por_muestra": raw_probability,
        },
        notes=notes,
    )
