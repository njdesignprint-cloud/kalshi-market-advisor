"""Datos deportivos publicos y gratuitos (NBA/NFL) desde la API publica de ESPN.

ESPN no publica documentacion oficial de estos endpoints (son los mismos
que usa espn.com internamente), pero son publicos, no requieren API key y
son ampliamente usados con este fin. Si ESPN cambia el formato sin aviso,
las funciones de este modulo son el unico lugar que deberia romperse.

Cada liga se identifica con el mismo "espn_sport_slug"/"espn_league_slug"
definidos en config.py, asi que agregar una liga nueva (MLB, NHL, ...) solo
requiere que exista en ese registro -- no hace falta tocar este archivo,
siempre que ESPN siga el mismo patron de URLs para esa liga.

LIMITACION IMPORTANTE: los codigos de equipo que usa Kalshi en sus tickers
no siempre coinciden con la abreviatura que usa ESPN (por ejemplo Kalshi
usa "SAS" para San Antonio Spurs, ESPN usa "SA"). Se mantiene una tabla de
alias explicita (_KALSHI_TO_ESPN_ABBR_ALIASES) para los casos conocidos.
Si Kalshi agrega un equipo o cambia un codigo y no esta en la tabla, la
funcion de resolucion devuelve None en vez de adivinar, y quien la llama
debe tratar ese mercado como "sin datos suficientes" en vez de inventar
una probabilidad.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

ESPN_SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_CORE_BASE = "https://site.api.espn.com/apis/v2/sports"

# Codigos de equipo de Kalshi que NO coinciden con la abreviatura de ESPN.
# key: codigo tal como aparece en el ticker de Kalshi. value: abreviatura de ESPN.
_KALSHI_TO_ESPN_ABBR_ALIASES: dict[str, str] = {
    # NBA
    "GSW": "GS",
    "NOP": "NO",
    "UTA": "UTAH",
    "SAS": "SA",
    "NYK": "NY",
    # NFL
    "WAS": "WSH",
    "JAC": "JAX",
    # MLB
    "AZ": "ARI",
    "CWS": "CHW",
}


@dataclass
class TeamRecord:
    """Resumen objetivo de forma reciente de un equipo, segun ESPN."""

    team_id: str
    abbreviation: str
    display_name: str
    games_considered: int
    wins: int
    losses: int
    home_wins: int
    home_losses: int
    away_wins: int
    away_losses: int
    avg_point_differential: float
    last_updated: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())

    @property
    def win_pct(self) -> float | None:
        if self.games_considered == 0:
            return None
        return self.wins / self.games_considered

    @property
    def home_win_pct(self) -> float | None:
        total = self.home_wins + self.home_losses
        return None if total == 0 else self.home_wins / total

    @property
    def away_win_pct(self) -> float | None:
        total = self.away_wins + self.away_losses
        return None if total == 0 else self.away_wins / total


@dataclass
class InjuryReport:
    team_abbreviation: str
    player_name: str
    position: str
    status: str  # "Out", "Doubtful", "Questionable", "Day-To-Day", etc.
    comment: str


@dataclass
class ProbablePitcher:
    """Pitcher abridor probable/confirmado de un equipo para un partido, con su ERA/WHIP.

    LIMITACION IMPORTANTE: el endpoint de ESPN de donde sale esto siempre
    refleja las estadisticas ACUMULADAS A HOY del pitcher, sin importar la
    fecha del partido consultado. Para partidos futuros es exactamente lo
    que se quiere (su forma antes de que ocurra el partido). Para
    reconstruir un partido YA JUGADO hace tiempo, estas cifras incluirian
    starts posteriores a ese partido (informacion del futuro) -- por eso
    el backtesting de esta senal solo usa una ventana corta de partidos
    recientes, donde ese sesgo es pequeno. Ver models/probability_model.py
    y tests/backtest_probability_model.py.
    """

    name: str
    era: float | None
    whip: float | None


class SportsDataError(RuntimeError):
    pass


class EspnSportsDataClient:
    """Cliente de solo lectura para los feeds publicos de ESPN."""

    def __init__(self, sport_slug: str, league_slug: str, session: requests.Session | None = None, timeout: float = 15.0):
        self.sport_slug = sport_slug
        self.league_slug = league_slug
        self._session = session or requests.Session()
        self.timeout = timeout
        self._team_cache: dict[str, dict] | None = None  # abreviatura ESPN -> team dict

    def _get(self, url: str, params: dict | None = None) -> dict:
        response = self._session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    # ---------------------------------------------------------------
    # Equipos
    # ---------------------------------------------------------------
    def list_teams(self) -> dict[str, dict]:
        """Devuelve {abreviatura_ESPN: team_dict}, con cache en memoria."""
        if self._team_cache is not None:
            return self._team_cache
        url = f"{ESPN_SITE_BASE}/{self.sport_slug}/{self.league_slug}/teams"
        data = self._get(url, {"limit": 50})
        teams: dict[str, dict] = {}
        for entry in data["sports"][0]["leagues"][0]["teams"]:
            team = entry["team"]
            teams[team["abbreviation"]] = team
        self._team_cache = teams
        return teams

    def resolve_kalshi_code(self, kalshi_code: str) -> dict | None:
        """Traduce un codigo de equipo de Kalshi (ej. 'BOS') a un team dict de ESPN.

        Devuelve None si no se puede resolver con confianza, en vez de
        adivinar -- quien llama debe tratar esto como dato insuficiente.
        """
        teams = self.list_teams()
        espn_abbr = _KALSHI_TO_ESPN_ABBR_ALIASES.get(kalshi_code, kalshi_code)
        return teams.get(espn_abbr)

    # ---------------------------------------------------------------
    # Forma reciente (calendario/resultados)
    # ---------------------------------------------------------------
    def get_schedule_raw(self, team_id: str) -> dict:
        """Trae el calendario de temporada regular (pasado y futuro) de un equipo.

        Se fuerza seasontype=2 (temporada regular) dejando que ESPN infiera
        el anio de temporada actual automaticamente -- esto evita tener que
        adivinar la convencion de etiquetado de temporada (que difiere
        entre NBA y NFL) y sigue funcionando sin cambios en anios futuros.
        LIMITACION: no incluye partidos de pretemporada ni de playoffs, y no
        cruza al final de la temporada anterior si la actual recien empieza
        (en ese caso el modelo de probabilidad simplemente tendra poca o
        ninguna muestra reciente, lo cual ya maneja con baja confianza).
        """
        url = f"{ESPN_SITE_BASE}/{self.sport_slug}/{self.league_slug}/teams/{team_id}/schedule"
        return self._get(url, {"seasontype": 2})

    def find_home_away(self, schedule_data: dict, opponent_id: str, on_or_after: str | None = None) -> bool | None:
        """Busca en el calendario un partido contra `opponent_id` y devuelve si es local.

        Devuelve None si no se encuentra el partido (por ejemplo si Kalshi ya
        abrio un mercado para un partido que ESPN todavia no publico en el
        calendario). Quien llama debe tratar None como "no se pudo confirmar
        local/visitante", no como "es visitante".
        """
        for event in schedule_data.get("events", []):
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            comp = competitions[0]
            competitors = comp.get("competitors", [])
            opp = next((c for c in competitors if c.get("team", {}).get("id") == str(opponent_id)), None)
            if opp is None:
                continue
            if on_or_after and event.get("date", "") < on_or_after:
                continue
            this_team = next((c for c in competitors if c.get("team", {}).get("id") != str(opponent_id)), None)
            if this_team is None:
                continue
            return this_team.get("homeAway") == "home"
        return None

    def find_event_id(self, schedule_data: dict, opponent_id: str, on_or_after: str | None = None) -> str | None:
        """Busca en el calendario el id de ESPN del partido contra `opponent_id`.

        Se usa para poder consultar /summary (pitcher abridor probable,
        etc.) de ese partido especifico. Devuelve None si no se encuentra.
        """
        for event in schedule_data.get("events", []):
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            competitors = competitions[0].get("competitors", [])
            opp = next((c for c in competitors if c.get("team", {}).get("id") == str(opponent_id)), None)
            if opp is None:
                continue
            if on_or_after and event.get("date", "") < on_or_after:
                continue
            return event.get("id")
        return None

    def get_probable_pitchers(self, event_id: str) -> dict[str, ProbablePitcher]:
        """Devuelve {team_id_ESPN: ProbablePitcher} para un partido de MLB.

        Ver el docstring de ProbablePitcher para la limitacion sobre el
        sesgo de informacion futura al usar esto en partidos ya jugados.
        Si uno de los dos equipos todavia no tiene pitcher confirmado,
        simplemente no aparece en el diccionario devuelto -- quien llama
        debe tratar eso como dato faltante, no como "sin pitcher".
        """
        url = f"{ESPN_SITE_BASE}/{self.sport_slug}/{self.league_slug}/summary"
        data = self._get(url, {"event": event_id})
        competitions = data.get("header", {}).get("competitions", [])
        if not competitions:
            return {}
        result: dict[str, ProbablePitcher] = {}
        for competitor in competitions[0].get("competitors", []):
            team_id = competitor.get("team", {}).get("id")
            probables = competitor.get("probables") or []
            if not team_id or not probables:
                continue
            categories = probables[0].get("statistics", {}).get("splits", {}).get("categories", [])
            stats = {c.get("name"): c.get("value") for c in categories}
            result[str(team_id)] = ProbablePitcher(
                name=probables[0].get("athlete", {}).get("displayName", ""),
                era=stats.get("ERA"),
                whip=stats.get("WHIP"),
            )
        return result

    def get_recent_record(self, team_id: str, last_n: int = 10) -> TeamRecord:
        """Calcula record reciente, splits local/visitante y diferencial de puntos.

        Se basa unicamente en los ultimos `last_n` partidos ya finalizados
        segun el calendario publico de ESPN para ese equipo.
        """
        data = self.get_schedule_raw(team_id)
        return self.summarize_recent_record(data, team_id, last_n=last_n)

    def summarize_recent_record(
        self,
        data: dict,
        team_id: str,
        last_n: int = 10,
        before_date: str | None = None,
    ) -> TeamRecord:
        """Resume forma reciente a partir de un calendario ya descargado.

        `before_date` (ISO 8601) permite reconstruir el record "tal como se
        conocia" antes de una fecha dada, usando solo partidos completados
        estrictamente anteriores a esa fecha. Se usa para backtesting (ver
        tests/backtest_probability_model.py); en produccion se deja en None
        y simplemente se toman los ultimos partidos completados hasta hoy.
        """
        team_info = data.get("team", {})
        completed = []
        for event in data.get("events", []):
            if before_date and event.get("date", "") >= before_date:
                continue
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            comp = competitions[0]
            status = comp.get("status", {}).get("type", {})
            if not status.get("completed"):
                continue
            completed.append((event, comp))

        # Los eventos vienen en orden cronologico ascendente; nos quedamos
        # con los ultimos N ya jugados.
        completed = completed[-last_n:]

        wins = losses = home_wins = home_losses = away_wins = away_losses = 0
        point_diffs: list[float] = []

        for _event, comp in completed:
            competitors = comp.get("competitors", [])
            this_team = next((c for c in competitors if c.get("team", {}).get("id") == str(team_id)), None)
            opponent = next((c for c in competitors if c.get("team", {}).get("id") != str(team_id)), None)
            if this_team is None or opponent is None:
                continue
            try:
                own_score = float(this_team.get("score", {}).get("value", this_team.get("score")))
                opp_score = float(opponent.get("score", {}).get("value", opponent.get("score")))
            except (TypeError, ValueError):
                continue
            won = this_team.get("winner") is True or own_score > opp_score
            is_home = this_team.get("homeAway") == "home"
            point_diffs.append(own_score - opp_score)
            if won:
                wins += 1
                home_wins += int(is_home)
                away_wins += int(not is_home)
            else:
                losses += 1
                home_losses += int(is_home)
                away_losses += int(not is_home)

        avg_diff = sum(point_diffs) / len(point_diffs) if point_diffs else 0.0

        return TeamRecord(
            team_id=str(team_id),
            abbreviation=team_info.get("abbreviation", ""),
            display_name=team_info.get("displayName", ""),
            games_considered=wins + losses,
            wins=wins,
            losses=losses,
            home_wins=home_wins,
            home_losses=home_losses,
            away_wins=away_wins,
            away_losses=away_losses,
            avg_point_differential=avg_diff,
        )

    # ---------------------------------------------------------------
    # Lesiones
    # ---------------------------------------------------------------
    def get_injuries_by_team_id(self) -> dict[str, list[InjuryReport]]:
        """Devuelve {team_id_ESPN: [InjuryReport, ...]} para toda la liga."""
        url = f"{ESPN_SITE_BASE}/{self.sport_slug}/{self.league_slug}/injuries"
        data = self._get(url)
        result: dict[str, list[InjuryReport]] = {}
        for team_block in data.get("injuries", []):
            team_name = team_block.get("displayName", "")
            reports = []
            for injury in team_block.get("injuries", []):
                athlete = injury.get("athlete", {})
                reports.append(
                    InjuryReport(
                        team_abbreviation=team_name,
                        player_name=athlete.get("displayName", "desconocido"),
                        position=(athlete.get("position") or {}).get("abbreviation", ""),
                        status=injury.get("status", "desconocido"),
                        comment=injury.get("shortComment", ""),
                    )
                )
            result[str(team_block.get("id"))] = reports
        return result
