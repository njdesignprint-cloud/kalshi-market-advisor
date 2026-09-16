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

from analysis.portfolio_monitor import analyze_portfolio_health
from config import LEAGUES
from connectors.kalshi_client import KalshiClient, KalshiClientError
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
  .nav-back {{ color: var(--accent); text-decoration: none; font-size: 0.9rem; }}
  .nav-back:hover {{ text-decoration: underline; }}
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
  <p style="text-align:center;margin-top:16px;"><a class="nav-back" href="/portafolio">Ver mi portafolio real en Kalshi &rarr;</a></p>
  <div class="risks">
    No es garantia de ganancia. El modelo de probabilidad es simple y no reemplaza tu propio
    criterio. Toda decision de operar es tuya, manual, directamente en kalshi.com.
  </div>
</div>
</body>
</html>
"""

PORTFOLIO_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mi Portafolio · Kalshi Market Advisor</title>
<style>
  :root {{
    --bg: #f7f7f8; --surface: #ffffff; --border: #e2e2e6; --text: #1a1a1e;
    --text-muted: #63636c; --accent: #2f5fda;
    --risk-bg: #fdecec; --risk-border: #e39a97;
    --positive: #0f7a3d; --positive-bg: #e8f6ee;
    --negative: #b3261e; --negative-bg: #fbeceb;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #121214; --surface: #1c1c1f; --border: #303035; --text: #f0f0f2;
      --text-muted: #a0a0a8; --accent: #7ea1ff;
      --risk-bg: #3a1e1d; --risk-border: #7a3b38;
      --positive: #3ecf7e; --positive-bg: #123322;
      --negative: #ff6b63; --negative-bg: #3a1c1a;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.5; padding: 16px;
  }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  .nav-back {{ display: inline-block; margin: 16px 0; font-size: 0.85rem; color: var(--accent); text-decoration: none; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  .subtitle {{ color: var(--text-muted); font-size: 0.85rem; margin-bottom: 20px; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 18px 20px; margin-bottom: 16px; }}
  .stat-row {{ display: flex; gap: 24px; flex-wrap: wrap; }}
  .stat {{ min-width: 140px; }}
  .stat .label {{ font-size: 0.78rem; color: var(--text-muted); text-transform: uppercase; }}
  .stat .value {{ font-size: 1.3rem; font-weight: 700; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
  th, td {{ text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--text-muted); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .pos {{ color: var(--positive); font-weight: 600; }}
  .neg {{ color: var(--negative); font-weight: 600; }}
  .warnings {{ background: var(--risk-bg); border: 1px solid var(--risk-border); border-radius: 10px; padding: 14px 16px; margin-bottom: 16px; }}
  .warnings ul {{ margin: 8px 0 0; padding-left: 20px; }}
  .warnings li {{ margin-bottom: 6px; font-size: 0.88rem; }}
  .empty-state {{ text-align: center; color: var(--text-muted); padding: 40px 10px; }}
  .risks {{ background: var(--risk-bg); border: 1px solid var(--risk-border); border-radius: 10px; padding: 12px 14px; font-size: 0.8rem; margin-top: 18px; }}
</style>
</head>
<body>
<div class="wrap">
  <a class="nav-back" href="/">&larr; Volver</a>
  <h1>Mi Portafolio en Kalshi</h1>
  <div class="subtitle">Lectura directa de tu cuenta real. Esta herramienta nunca coloca ni modifica ordenes.</div>
  {content}
  <div class="risks">
    El calculo de ganancia/perdida no realizada es una aproximacion (usa el precio de venta
    actual del mercado menos el costo reportado por Kalshi); verificala contra tu cuenta real.
    No es garantia de ganancia ni una recomendacion de compra o venta.
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


def _money(value: float) -> str:
    return f"${value:,.2f}"


def render_portfolio_content() -> str:
    client = KalshiClient()
    try:
        report = analyze_portfolio_health(client)
    except KalshiClientError as error:
        return f"""<div class="card empty-state">
      <p><strong>Todavia no configuraste tu API key de Kalshi.</strong></p>
      <p>{error}</p>
      <p>Copia <code>.env.example</code> a <code>.env</code> en la carpeta del proyecto y completa
      <code>KALSHI_API_KEY_ID</code> y <code>KALSHI_PRIVATE_KEY_PATH</code> con los datos de tu cuenta,
      luego reinicia el servidor.</p>
    </div>"""

    warnings_html = ""
    if report.warnings:
        items = "".join(f"<li>{w}</li>" for w in report.warnings)
        warnings_html = f'<div class="warnings"><strong>Atencion</strong><ul>{items}</ul></div>'

    if not report.positions:
        return f"""
    <div class="card">
      <div class="stat-row">
        <div class="stat"><div class="label">Balance disponible</div><div class="value">{_money(report.balance_dollars)}</div></div>
      </div>
    </div>
    <div class="card empty-state"><p>No tienes posiciones abiertas ahora mismo.</p></div>"""

    rows = []
    for p in report.positions:
        pnl_class = "pos" if (p.unrealized_pnl_dollars or 0) >= 0 else "neg"
        pnl_text = _money(p.unrealized_pnl_dollars) if p.unrealized_pnl_dollars is not None else "sin verificar"
        price_text = f"{p.current_price:.0%}" if p.current_price is not None else "-"
        concentration_text = f"{p.concentration_pct:.0%}" if p.concentration_pct is not None else "-"
        rows.append(f"""<tr>
          <td>{p.subtitle or p.title or p.ticker}<div style="color:var(--text-muted);font-size:0.78rem;">{p.ticker}</div></td>
          <td>{p.side}</td>
          <td class="num">{p.contracts:.0f}</td>
          <td class="num">{_money(p.market_exposure_dollars)}</td>
          <td class="num">{price_text}</td>
          <td class="num {pnl_class}">{pnl_text}</td>
          <td class="num">{concentration_text}</td>
        </tr>""")

    total_pnl_html = ""
    if report.total_unrealized_pnl_dollars is not None:
        cls = "pos" if report.total_unrealized_pnl_dollars >= 0 else "neg"
        total_pnl_html = f'<div class="stat"><div class="label">P&amp;L no realizado (estimado)</div><div class="value {cls}">{_money(report.total_unrealized_pnl_dollars)}</div></div>'

    return f"""
    {warnings_html}
    <div class="card">
      <div class="stat-row">
        <div class="stat"><div class="label">Balance disponible</div><div class="value">{_money(report.balance_dollars)}</div></div>
        <div class="stat"><div class="label">Valor total del portafolio</div><div class="value">{_money(report.total_value_dollars)}</div></div>
        {total_pnl_html}
      </div>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>Mercado</th><th>Lado</th><th class="num">Contratos</th><th class="num">Costo</th><th class="num">Precio actual</th><th class="num">P&amp;L estimado</th><th class="num">% portafolio</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </div>"""


@app.route("/portafolio", methods=["GET"])
def portafolio() -> str:
    return PORTFOLIO_PAGE.format(content=render_portfolio_content())


def _open_browser() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}/")


if __name__ == "__main__":
    threading.Timer(1.0, _open_browser).start()
    print(f"Kalshi Market Advisor corriendo en http://{HOST}:{PORT} (solo en esta maquina)")
    app.run(host=HOST, port=PORT, debug=False)
