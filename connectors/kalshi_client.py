"""Cliente de solo lectura para la API REST de Kalshi (trade-api/v2).

ALCANCE DELIBERADO: este cliente expone UNICAMENTE endpoints GET de lectura
-- datos de mercado (series, eventos, mercados) y, si hay credenciales
configuradas, el balance y las posiciones de TU PROPIA cuenta
(portfolio/balance, portfolio/positions). No existe ningun metodo para
crear, modificar o cancelar ordenes, ni para mover fondos. Esto es
intencional: la herramienta es de investigacion y monitoreo, nunca de
ejecucion. Si en el futuro alguien necesita colocar ordenes, debe hacerlo
manualmente en kalshi.com o con otro codigo -- no agregues esa capacidad
aqui.

Autenticacion: los endpoints de datos de mercado son publicos y no
requieren autenticacion; este cliente los firma con RSA-PSS igual cuando
hay credenciales, porque Kalshi da limites de tasa mas altos a las
solicitudes autenticadas. Los endpoints de portfolio/* SI requieren
autenticacion -- sin credenciales configuradas, esos metodos fallan con un
mensaje claro en vez de intentar adivinar.

Variables de entorno:
  KALSHI_API_KEY_ID       Identificador de la API key (opcional).
  KALSHI_PRIVATE_KEY_PATH Ruta al archivo .pem de la clave privada RSA
                          asociada a esa key (opcional).
  KALSHI_API_BASE_URL     Sobrescribe la URL base (opcional, por defecto
                          produccion).
"""

from __future__ import annotations

import base64
import datetime
import os
import time
from dataclasses import dataclass
from typing import Any

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

DEFAULT_HOST = "https://external-api.kalshi.com"
API_PREFIX = "/trade-api/v2"

# Endpoints permitidos explicitamente. El cliente se niega a llamar
# cualquier ruta que no empiece con uno de estos prefijos -- barrera
# deliberada contra agregar por accidente un endpoint de ordenes/trading.
# Nota: /portfolio/balance y /portfolio/positions son endpoints de LECTURA
# (GET) de tu propia cuenta; no existen ni existiran /portfolio/orders de
# escritura en este cliente.
_ALLOWED_PATH_PREFIXES = (
    "/series", "/events", "/markets",
    "/portfolio/balance", "/portfolio/positions", "/portfolio/fills", "/portfolio/settlements",
)


class KalshiClientError(RuntimeError):
    pass


@dataclass
class MarketQuote:
    """Cotizacion de un mercado binario individual de Kalshi."""

    ticker: str
    event_ticker: str
    series_ticker: str
    title: str
    subtitle: str
    status: str
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    last_price: float | None
    volume: float | None
    volume_24h: float | None
    open_interest: float | None
    close_time: str | None
    occurrence_datetime: str | None

    @property
    def implied_yes_probability(self) -> float | None:
        """Probabilidad implicita de "Yes" usando el precio de venta (ask).

        Se usa el ask (no el midpoint) porque es el costo real que pagarias
        para entrar a la posicion; es una estimacion conservadora del precio
        de mercado, no una probabilidad "verdadera".
        """
        if self.yes_ask is None:
            return None
        return self.yes_ask


@dataclass
class PortfolioBalance:
    """Balance de efectivo disponible en tu cuenta de Kalshi."""

    balance_dollars: float
    portfolio_value_dollars: float
    updated_ts: str | None


@dataclass
class MarketPosition:
    """Posicion abierta en un mercado individual, tal como la reporta Kalshi."""

    ticker: str
    position: float  # contratos netos; positivo = Yes, negativo = No
    market_exposure_dollars: float  # costo aproximado de la posicion abierta
    realized_pnl_dollars: float
    fees_paid_dollars: float
    total_traded_dollars: float
    last_updated_ts: str | None


@dataclass
class Fill:
    """Una operacion individual (compra o venta ejecutada) de tu cuenta."""

    ticker: str
    outcome_side: str  # "yes" | "no"
    is_taker: bool
    count: float
    price_dollars: float  # precio del lado que compraste/vendiste (yes o no)
    fee_cost_dollars: float
    created_time: str | None


@dataclass
class Settlement:
    """Un mercado ya resuelto en el que tenias posicion -- aqui se ve si ganaste o perdiste.

    LIMITACION: los campos `revenue` y `value` que devuelve Kalshi no
    siguen la convencion "_dollars" del resto de la API (que usa strings
    como "0.5600"); llegan como enteros y aqui se asumen centavos, sin
    verificar todavia contra una liquidacion real de tu cuenta. Si el
    monto que ves aqui no coincide con Kalshi, es la primera cifra a
    revisar.
    """

    ticker: str
    event_ticker: str
    market_result: str  # "yes" | "no" | "scalar"
    yes_count: float
    yes_total_cost_dollars: float
    no_count: float
    no_total_cost_dollars: float
    revenue_dollars: float  # pago recibido (asumiendo centavos -- ver limitacion arriba)
    fee_cost_dollars: float
    settled_time: str | None

    @property
    def won(self) -> bool:
        return (self.market_result == "yes" and self.yes_count > 0) or (self.market_result == "no" and self.no_count > 0)

    @property
    def net_result_dollars(self) -> float:
        cost = self.yes_total_cost_dollars if self.yes_count > 0 else self.no_total_cost_dollars
        return self.revenue_dollars - cost


class KalshiClient:
    """Cliente HTTP minimo para los endpoints de lectura de mercados de Kalshi."""

    def __init__(
        self,
        api_key_id: str | None = None,
        private_key_path: str | None = None,
        base_url: str | None = None,
        session: requests.Session | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.host = (base_url or os.environ.get("KALSHI_API_BASE_URL") or DEFAULT_HOST).rstrip("/")
        self.api_key_id = api_key_id or os.environ.get("KALSHI_API_KEY_ID")
        key_path = private_key_path or os.environ.get("KALSHI_PRIVATE_KEY_PATH")
        self._private_key: RSAPrivateKey | None = None
        if key_path:
            self._private_key = self._load_private_key(key_path)
        self._session = session or requests.Session()
        self.timeout = timeout

    @staticmethod
    def _load_private_key(path: str) -> RSAPrivateKey:
        expanded = os.path.expanduser(path)
        if not os.path.isfile(expanded):
            raise KalshiClientError(
                f"No se encontro el archivo de clave privada en '{expanded}'. "
                "Configura KALSHI_PRIVATE_KEY_PATH en tu .env."
            )
        with open(expanded, "rb") as key_file:
            key = serialization.load_pem_private_key(key_file.read(), password=None)
        if not isinstance(key, RSAPrivateKey):
            raise KalshiClientError("La clave privada configurada no es una clave RSA.")
        return key

    def _sign(self, timestamp_ms: str, method: str, path_without_query: str) -> str:
        assert self._private_key is not None
        message = f"{timestamp_ms}{method}{path_without_query}".encode("utf-8")
        signature = self._private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")

    def _auth_headers(self, method: str, path_without_query: str) -> dict[str, str]:
        if not (self.api_key_id and self._private_key):
            return {}
        timestamp_ms = str(int(datetime.datetime.now(tz=datetime.timezone.utc).timestamp() * 1000))
        signature = self._sign(timestamp_ms, method, path_without_query)
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not path.startswith(_ALLOWED_PATH_PREFIXES):
            raise KalshiClientError(
                f"Ruta '{path}' no permitida: este cliente es de solo lectura de "
                f"mercados ({_ALLOWED_PATH_PREFIXES})."
            )
        api_path = API_PREFIX + path
        headers = self._auth_headers("GET", api_path)
        response = self._session.get(
            self.host + api_path,
            params=params,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_series_list(self, category: str, include_volume: bool = False) -> list[dict[str, Any]]:
        """Lista las series de Kalshi dentro de una categoria (ej. "Sports")."""
        data = self._get("/series", {"category": category, "include_volume": include_volume})
        return data.get("series", [])

    def get_events(
        self,
        series_ticker: str,
        status: str = "open",
        with_nested_markets: bool = True,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Trae eventos (partidos) abiertos de una serie, con sus mercados anidados."""
        events: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {
                "series_ticker": series_ticker,
                "status": status,
                "with_nested_markets": with_nested_markets,
                "limit": limit,
            }
            if cursor:
                params["cursor"] = cursor
            data = self._get("/events", params)
            events.extend(data.get("events", []))
            cursor = data.get("cursor") or None
            if not cursor or not data.get("events"):
                break
        return events

    def get_active_markets_for_series(self, series_ticker: str) -> list[MarketQuote]:
        """Devuelve los mercados activos (abiertos) de una serie como MarketQuote."""
        events = self.get_events(series_ticker, status="open", with_nested_markets=True)
        quotes: list[MarketQuote] = []
        for event in events:
            for market in event.get("markets", []):
                quotes.append(self._market_to_quote(market, series_ticker))
        return quotes

    @staticmethod
    def _market_to_quote(market: dict[str, Any], series_ticker: str) -> MarketQuote:
        def to_float(value: Any) -> float | None:
            if value in (None, ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        return MarketQuote(
            ticker=market["ticker"],
            event_ticker=market["event_ticker"],
            series_ticker=series_ticker,
            title=market.get("title", ""),
            subtitle=market.get("yes_sub_title", ""),
            status=market.get("status", ""),
            yes_bid=to_float(market.get("yes_bid_dollars")),
            yes_ask=to_float(market.get("yes_ask_dollars")),
            no_bid=to_float(market.get("no_bid_dollars")),
            no_ask=to_float(market.get("no_ask_dollars")),
            last_price=to_float(market.get("last_price_dollars")),
            volume=to_float(market.get("volume_fp")),
            volume_24h=to_float(market.get("volume_24h_fp")),
            open_interest=to_float(market.get("open_interest_fp")),
            close_time=market.get("close_time"),
            occurrence_datetime=market.get("occurrence_datetime"),
        )

    def get_markets_by_tickers(self, tickers: list[str]) -> list[MarketQuote]:
        """Trae la cotizacion actual de tickers especificos (para valuar posiciones)."""
        if not tickers:
            return []
        quotes: list[MarketQuote] = []
        # La API acepta una lista separada por comas; se trocea por si acaso
        # para no exceder limites razonables de longitud de query string.
        for start in range(0, len(tickers), 50):
            batch = tickers[start : start + 50]
            data = self._get("/markets", {"tickers": ",".join(batch), "limit": len(batch)})
            for market in data.get("markets", []):
                quotes.append(self._market_to_quote(market, market.get("event_ticker", "")))
        return quotes

    def _require_credentials(self) -> None:
        if not (self.api_key_id and self._private_key):
            raise KalshiClientError(
                "Esta operacion requiere tu API key de Kalshi. Configura "
                "KALSHI_API_KEY_ID y KALSHI_PRIVATE_KEY_PATH en tu .env "
                "(ver .env.example)."
            )

    def get_balance(self) -> PortfolioBalance:
        """Balance de efectivo de tu cuenta. Requiere credenciales configuradas."""
        self._require_credentials()
        data = self._get("/portfolio/balance")
        return PortfolioBalance(
            balance_dollars=float(data.get("balance_dollars", 0) or 0),
            portfolio_value_dollars=float(data.get("portfolio_value_dollars", data.get("balance_dollars", 0)) or 0),
            updated_ts=data.get("updated_ts"),
        )

    def get_positions(self) -> list[MarketPosition]:
        """Posiciones abiertas en tu cuenta. Requiere credenciales configuradas."""
        self._require_credentials()
        positions: list[MarketPosition] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"count_filter": "position", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = self._get("/portfolio/positions", params)
            for item in data.get("market_positions", []):
                position_fp = float(item.get("position_fp", 0) or 0)
                if position_fp == 0:
                    continue
                positions.append(
                    MarketPosition(
                        ticker=item["ticker"],
                        position=position_fp,
                        market_exposure_dollars=float(item.get("market_exposure_dollars", 0) or 0),
                        realized_pnl_dollars=float(item.get("realized_pnl_dollars", 0) or 0),
                        fees_paid_dollars=float(item.get("fees_paid_dollars", 0) or 0),
                        total_traded_dollars=float(item.get("total_traded_dollars", 0) or 0),
                        last_updated_ts=item.get("last_updated_ts"),
                    )
                )
            cursor = data.get("cursor") or None
            if not cursor or not data.get("market_positions"):
                break
        return positions

    def get_fills(self, ticker: str | None = None, limit: int = 200) -> list[Fill]:
        """Historial de operaciones (compras/ventas ejecutadas). Requiere credenciales."""
        self._require_credentials()
        fills: list[Fill] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": limit}
            if ticker:
                params["ticker"] = ticker
            if cursor:
                params["cursor"] = cursor
            data = self._get("/portfolio/fills", params)
            for item in data.get("fills", []):
                side = item.get("outcome_side", "yes")
                price = item.get("yes_price_dollars") if side == "yes" else item.get("no_price_dollars")
                fills.append(
                    Fill(
                        ticker=item["ticker"],
                        outcome_side=side,
                        is_taker=bool(item.get("is_taker", False)),
                        count=float(item.get("count_fp", 0) or 0),
                        price_dollars=float(price or 0),
                        fee_cost_dollars=float(item.get("fee_cost", 0) or 0),
                        created_time=item.get("created_time"),
                    )
                )
            cursor = data.get("cursor") or None
            if not cursor or not data.get("fills"):
                break
        return fills

    def get_settlements(self, ticker: str | None = None, limit: int = 200) -> list[Settlement]:
        """Mercados ya resueltos en los que tuviste posicion. Requiere credenciales."""
        self._require_credentials()
        settlements: list[Settlement] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": limit}
            if ticker:
                params["ticker"] = ticker
            if cursor:
                params["cursor"] = cursor
            data = self._get("/portfolio/settlements", params)
            for item in data.get("settlements", []):
                settlements.append(
                    Settlement(
                        ticker=item["ticker"],
                        event_ticker=item.get("event_ticker", ""),
                        market_result=item.get("market_result", ""),
                        yes_count=float(item.get("yes_count_fp", 0) or 0),
                        yes_total_cost_dollars=float(item.get("yes_total_cost_dollars", 0) or 0),
                        no_count=float(item.get("no_count_fp", 0) or 0),
                        no_total_cost_dollars=float(item.get("no_total_cost_dollars", 0) or 0),
                        revenue_dollars=float(item.get("revenue", 0) or 0) / 100,
                        fee_cost_dollars=float(item.get("fee_cost", 0) or 0),
                        settled_time=item.get("settled_time"),
                    )
                )
            cursor = data.get("cursor") or None
            if not cursor or not data.get("settlements"):
                break
        return settlements
