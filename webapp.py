"""Servidor web local para usar kalshi-market-advisor desde el navegador.

Abre una pagina con un formulario (fecha, monto, ligas). Al enviarlo,
corre el mismo pipeline de siempre (analysis/analyzer.py + portfolio.py)
y muestra el dashboard resultante en el navegador, sin volver a tocar la
terminal.

Este servidor corre SOLO en tu maquina (127.0.0.1) y no expone nada a
internet. Sigue siendo de solo lectura: no coloca ordenes ni mueve dinero.

Uso:
    python webapp.py
    (o hacer doble clic en iniciar_servidor.bat en Windows)
"""

from __future__ import annotations

import threading
import webbrowser
from datetime import date, timedelta

from dotenv import load_dotenv
from flask import Flask, request

from config import LEAGUES
from main import render_dashboard_html, run_pipeline

load_dotenv()

app = Flask(__name__)

HOST = "127.0.0.1"
PORT = 5000

BACK_LINK = '<a class="nav-back" href="/">&larr; Nueva consulta</a>'

FORM_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kalshi Market Advisor</title>
<style>
  :root {{
    --bg: #f7f7f8; --surface: #ffffff; --border: #e2e2e6; --text: #1a1a1e;
    --text-muted: #63636c; --accent: #2f5fda; --risk-bg: #fdecec; --risk-border: #e39a97;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #121214; --surface: #1c1c1f; --border: #303035; --text: #f0f0f2;
      --text-muted: #a0a0a8; --accent: #7ea1ff; --risk-bg: #3a1e1d; --risk-border: #7a3b38;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.5; padding: 16px;
  }}
  .wrap {{ max-width: 560px; margin: 60px auto; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 4px; }}
  .subtitle {{ color: var(--text-muted); font-size: 0.9rem; margin-bottom: 24px; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 22px 24px; }}
  .field {{ margin-bottom: 16px; display: flex; flex-direction: column; gap: 6px; }}
  .field label {{ font-size: 0.85rem; color: var(--text-muted); }}
  .field input {{
    background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 10px 12px; color: var(--text); font-size: 1rem;
  }}
  .checks {{ display: flex; gap: 16px; flex-wrap: wrap; }}
  .checks label {{ display: flex; align-items: center; gap: 6px; font-size: 0.92rem; color: var(--text); }}
  button {{
    width: 100%; padding: 12px; border: none; border-radius: 8px;
    background: var(--accent); color: white; font-size: 1rem; font-weight: 600;
    cursor: pointer; margin-top: 8px;
  }}
  button:hover {{ opacity: 0.92; }}
  .risks {{
    background: var(--risk-bg); border: 1px solid var(--risk-border); border-radius: 10px;
    padding: 12px 14px; font-size: 0.8rem; margin-top: 18px; color: var(--text);
  }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Kalshi Market Advisor</h1>
  <div class="subtitle">Herramienta de investigacion de solo lectura. No coloca ordenes ni mueve dinero.</div>
  <div class="card">
    <form action="/analizar" method="post">
      <div class="field">
        <label for="date">Fecha de los partidos</label>
        <input type="date" id="date" name="date" value="{default_date}" required>
      </div>
      <div class="field">
        <label for="bankroll">Monto total a asignar (USD)</label>
        <input type="number" id="bankroll" name="bankroll" min="1" step="1" value="500" required>
      </div>
      <div class="field">
        <label>Ligas a analizar</label>
        <div class="checks">{league_checkboxes}</div>
      </div>
      <button type="submit">Analizar</button>
    </form>
  </div>
  <div class="risks">
    No es garantia de ganancia. El modelo de probabilidad es simple y no reemplaza tu propio
    criterio. Toda decision de operar es tuya, manual, directamente en kalshi.com.
  </div>
</div>
</body>
</html>
"""


def render_form() -> str:
    default_date = (date.today() + timedelta(days=1)).isoformat()
    checkboxes = "".join(
        f'<label><input type="checkbox" name="leagues" value="{key}" checked> {cfg.display_name}</label>'
        for key, cfg in LEAGUES.items()
    )
    return FORM_PAGE.format(default_date=default_date, league_checkboxes=checkboxes)


@app.route("/", methods=["GET"])
def index() -> str:
    return render_form()


@app.route("/analizar", methods=["POST"])
def analizar() -> str:
    fecha = request.form["date"]
    bankroll = float(request.form["bankroll"])
    leagues = request.form.getlist("leagues") or list(LEAGUES.keys())

    data = run_pipeline(fecha, bankroll, leagues)
    return render_dashboard_html(data, nav_html=BACK_LINK)


def _open_browser() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}/")


if __name__ == "__main__":
    threading.Timer(1.0, _open_browser).start()
    print(f"Kalshi Market Advisor corriendo en http://{HOST}:{PORT} (solo en esta maquina)")
    app.run(host=HOST, port=PORT, debug=False)
