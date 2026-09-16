"""Cliente de solo lectura para la API REST de Kalshi (trade-api/v2).

ALCANCE DELIBERADO: este cliente expone UNICAMENTE endpoints GET publicos
de datos de mercado (series, eventos, mercados). No existe ningun metodo
para crear, modificar o cancelar ordenes, ni para mover fondos. Esto es
intencional: la herramienta es de investigacion, nunca de ejecucion.
Si en el futuro alguien necesita colocar ordenes, debe hacerlo manualmente
en kalshi.com o con otro codigo -- no agregues esa capacidad aqui.

Autenticacion: los endpoints de datos de mercado de Kalshi son publicos y
no requieren autenticacion. Aun asi, este cliente firma las solicitudes
con RSA-PSS (SHA-256) cuando hay credenciales configuradas, porque Kalshi
aplica limites de tasa mas altos a las solicitudes autenticadas. Si no hay
credenciales, el cliente sigue funcionando sin firmar.

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
# cualquier ruta que no empiece con uno de estos prefijos, como barrera
# adicional contra el uso accidental de endpoints de trading/portfolio.
_ALLOWED_PATH_PREFIXES = ("/series", "/events", "/markets")


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
