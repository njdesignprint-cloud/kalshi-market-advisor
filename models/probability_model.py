"""Modelo de probabilidad para partidos de un-contra-uno (NBA/NFL/MLB/...).

QUE HACE: combina tres senales objetivas y publicas -- forma reciente
(record de los ultimos N partidos), ventaja de local/visitante, y
diferencial de puntos promedio -- en una probabilidad estimada de que un
equipo gane su proximo partido. Aplica un ajuste pequeno y conservador por
lesiones de jugadores marcados como "Out".

SUPUESTOS (explicitos):
  1. Los pesos que combinan cada senal viven en `ModelWeights`. Los pesos
     por defecto (`DEFAULT_WEIGHTS`, usados por NBA/NFL) son heuristicos,
     elegidos por razonabilidad estadistica general, NO ajustados contra
     datos historicos -- no fueron validados con backtesting. `MLB_WEIGHTS`
     es la excepcion: SI fue ajustado y verificado con
     tests/backtest_probability_model.py contra resultados reales (ver
     README, seccion de backtesting). Cualquier liga nueva deberia pasar
     por el mismo proceso antes de confiar en los pesos por defecto --
     no asumas que sirven fuera de NBA/NFL.
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

_MAX_OUT_PLAYERS_COUNTED = 3  # evita que un reporte ruidoso hunda la probabilidad a 0


@dataclass(frozen=True)
class ModelWeights:
    """Pesos que combinan cada senal, en escala logit.

    Los pesos por defecto (NBA/NFL) son heuristicos, sin ajustar contra
    datos historicos. `MLB_WEIGHTS`, en cambio, SI fue ajustado con
    backtesting real (ver tests/backtest_probability_model.py y el
    README) porque los pesos originales, pensados para deportes de baja
    varianza por partido, resultaron mal calibrados para MLB (peor que
    adivinar 50/50). Si agregas una liga nueva con caracteristicas de
    varianza distintas a NBA/NFL, corre el backtest antes de confiar en
    los pesos por defecto.
    """

    recent_form_weight: float = 1.6          # peso del diferencial de win% reciente
    point_diff_weight: float = 0.045         # peso del diferencial de puntos/carreras promedio
    home_advantage_logit: float = 0.25       # ventaja fija de local, en escala logit
    injury_penalty_per_out_starter: float = 0.12  # penalizacion por jugador "Out"


DEFAULT_WEIGHTS = ModelWeights()

# Recalibrado con tests/backtest_probability_model.py --search contra la
# temporada MLB 2026 real (2225 partidos, split cronologico 70/30
# entrenamiento/prueba, grid search en TRAIN, verificado en TEST fuera de
# muestra). Los pesos por defecto daban Brier score 0.2556 en TEST (apenas
# peor que 50/50 = 0.25); estos pesos dan 0.2486 en TEST. La mejora es
# real pero MODESTA: en MLB la senal de forma reciente/diferencial de
# carreras es mucho mas debil que en NBA/NFL (mayor varianza por partido),
# y el resultado recalibrado queda muy cerca de la linea base ingenua
# "siempre predecir la tasa real de victoria de local". Tratar cualquier
# edge que el analyzer encuentre en MLB con escepticismo extra por esto
# mismo. home_advantage_logit=0.078 es literalmente el logit de la tasa de
# victoria de local observada en el set de entrenamiento (52%); el grid
# search no encontro nada mejor que usar directamente el dato empirico.
# injury_penalty_per_out_starter no se recalibro (ESPN no expone lesiones
# historicas por fecha pasada para poder evaluarlo con backtesting).
MLB_WEIGHTS = ModelWeights(
    recent_form_weight=0.2,
    point_diff_weight=0.03,
    home_advantage_logit=0.078,
    injury_penalty_per_out_starter=0.12,
)

LEAGUE_WEIGHTS: dict[str, ModelWeights] = {
    "MLB": MLB_WEIGHTS,
}


def get_weights_for_league(league_key: str) -> ModelWeights:
    return LEAGUE_WEIGHTS.get(league_key.upper(), DEFAULT_WEIGHTS)


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
    weights: ModelWeights = DEFAULT_WEIGHTS,
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

    form_component = weights.recent_form_weight * (team_win_pct - opp_win_pct)
    point_diff_component = weights.point_diff_weight * (team.avg_point_differential - opponent.avg_point_differential)
    home_component = weights.home_advantage_logit if team_is_home else -weights.home_advantage_logit

    team_out = _count_out_players(team_injuries)
    opp_out = _count_out_players(opponent_injuries)
    injury_component = weights.injury_penalty_per_out_starter * (opp_out - team_out)
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
