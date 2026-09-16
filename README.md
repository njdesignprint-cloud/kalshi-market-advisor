# Kalshi Market Advisor

Herramienta de investigacion y analisis de solo lectura para mercados de
prediccion de Kalshi. Compara la probabilidad implicita de cada mercado
contra un modelo propio basado en datos publicos, calcula el valor
esperado (EV) real, y genera una recomendacion de asignacion de bankroll
en texto explicado -- para que la decidas y la ejecutes tu mismo,
manualmente, en kalshi.com.

Empieza con NBA, NFL, MLB y NHL. La arquitectura (`config.py` + `connectors/` +
`models/` + `analysis/`) esta pensada para agregar categorias nuevas
(NHL, etc.) sin reescribir el resto del pipeline.

## Principios de diseno (no negociables)

- **Solo lectura.** El cliente de Kalshi (`connectors/kalshi_client.py`)
  unicamente expone endpoints GET de mercados/eventos/series. No existe
  ningun metodo para colocar, modificar o cancelar ordenes, ni para mover
  fondos. Esta herramienta nunca opera por ti.
- **No promete ganar todos los dias.** En mercados razonablemente
  eficientes, los dias sin ninguna oportunidad de valor esperado positivo
  real son normales. Cuando eso pasa, la herramienta dice explicitamente
  "no hay recomendacion hoy" en vez de forzar una sugerencia.
- **No arma combinadas/parlays.** Si combinar varias jugadas reduce el
  valor esperado ajustado por riesgo (lo usual), la herramienta lo dice y
  recomienda en contra.
- **Gestion de riesgo por defecto.** El tamano de cada posicion sugerida
  usa Kelly fraccionado (1/4) con un tope maximo por jugada y un tope
  maximo de bankroll total desplegado. Nunca recomienda el 100% del
  bankroll en una sola jugada.

## Instalacion

Requiere Python 3.11 o superior.

```bash
cd kalshi-market-advisor
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

## Configuracion de credenciales

Los endpoints de datos de mercado de Kalshi que usa esta herramienta son
**publicos** y funcionan sin ninguna credencial. Configurar una API key es
opcional y solo te da limites de tasa mas altos.

1. Genera una API key en Kalshi (seccion "API Keys" de tu perfil) y guarda
   el archivo `.pem` de la clave privada que te entregan (Kalshi no lo
   vuelve a mostrar despues).
2. Copia la plantilla de variables de entorno:
   ```bash
   cp .env.example .env
   ```
3. Completa `.env` con tus propios valores:
   ```
   KALSHI_API_KEY_ID=tu-key-id
   KALSHI_PRIVATE_KEY_PATH=C:\ruta\a\tu\private_key.pem
   ```
4. **Nunca subas tu `.env` real a un repositorio.** `.gitignore` ya lo
   excluye.

## Como ejecutar

```bash
python main.py --date 2026-10-20 --bankroll 500
```

Parametros:
- `--date` (obligatorio): fecha de los partidos a considerar, `YYYY-MM-DD`.
- `--bankroll` (obligatorio): monto total disponible para asignar, en dolares.
- `--leagues` (opcional, por defecto todas): lista separada por comas, ej. `--leagues NBA`.

El script:
1. Trae los mercados activos de Kalshi para cada liga (`KXNBAGAME`, `KXNFLGAME`).
2. Trae estadisticas, calendario y lesiones desde ESPN (publico, sin API key).
3. Calcula la probabilidad del modelo y la compara contra el precio de Kalshi.
4. Genera una recomendacion de asignacion (o "no hay recomendacion hoy").
5. Escribe `output/dashboard.html` (abrelo en tu navegador) y
   `output/recommendation_latest.json` con el detalle completo.

En el dashboard puedes cambiar el monto y ver la asignacion recalculada
al instante (misma logica que `analysis/portfolio.py`, reimplementada en
JavaScript). Para analizar otra fecha o traer precios actualizados hay
que volver a correr `main.py`.

## Backtesting y recalibracion del modelo

`tests/backtest_probability_model.py` reconstruye, para cada partido ya
jugado de una liga, la probabilidad que el modelo hubiera dado usando
solo informacion anterior a ese partido, y la compara contra el
resultado real. Esto valida **calibracion** (si el modelo dice 65%, gano
el equipo como el 65% de las veces?), no rentabilidad contra Kalshi (la
API publica no expone precios historicos por fecha pasada).

```bash
python tests/backtest_probability_model.py                # calibracion con los pesos actuales de la liga
python tests/backtest_probability_model.py --search        # grid search train/test para recalibrar
python tests/backtest_probability_model.py --league NBA    # otra liga, si ya tiene partidos suficientes
```

**Hallazgo real (MLB, temporada 2026, 2225 partidos):** los pesos
heuristicos originales (pensados para NBA/NFL) dieron un Brier score de
**0.2556 en el split de prueba fuera de muestra** -- peor que simplemente
adivinar 50/50 (0.25). El modelo estaba sobrestimando su propia confianza
de forma sistemática en un deporte de mucha mas varianza por partido.
Se recalibraron los pesos especificamente para MLB con un split
cronologico 70/30 entrenamiento/prueba (grid search en el 70%, verificado
en el 30% restante para no sobreajustar). El resultado recalibrado
(`MLB_WEIGHTS` en `models/probability_model.py`) da 0.2486 en el mismo
split de prueba -- una mejora real pero **modesta**, muy cerca de la
linea base ingenua de "siempre predecir la tasa real de victoria de
local". Conclusion honesta: en MLB, la señal de forma reciente y
diferencial de carreras es mucho mas debil que en NBA/NFL, y cualquier
edge que el analyzer reporte ahi merece escepticismo extra.

Cada liga nueva deberia pasar por este mismo proceso (`--search`) antes
de confiar en los pesos por defecto -- no asumas que los pesos de
NBA/NFL sirven para un deporte con una distribucion de varianza distinta.

## Como agregar una categoria de mercado nueva

1. Confirma que la liga tiene en Kalshi una serie de "ganador de partido"
   con un mercado binario por equipo (patron `KX<LIGA>GAME`).
2. Confirma que ESPN publica esa liga bajo el mismo patron de URLs
   (`site.api.espn.com/apis/site/v2/sports/<deporte>/<liga>/...`).
3. Agrega una entrada a `LEAGUES` en `config.py` con los tickers/slugs
   correspondientes.
4. Si los codigos de equipo de Kalshi no coinciden exactamente con las
   abreviaturas de ESPN para esa liga, agrega los alias necesarios en
   `_KALSHI_TO_ESPN_ABBR_ALIASES` (`connectors/sports_data.py`).

Ningun otro modulo deberia necesitar cambios estructurales.

## Limitaciones honestas (leelas antes de usar la herramienta)

- **No es garantia de ganancia.** Es una herramienta de apoyo a la
  decision basada en datos objetivos y publicos, no una prediccion
  confiable por si sola.
- **El modelo de probabilidad es simple a proposito** (record reciente,
  local/visitante, diferencial de puntos, ajuste crudo por lesiones).
  Para NBA, NFL y NHL sus pesos son heuristicos y **no estan validados
  con backtesting historico** (NHL todavia no tiene partidos jugados de
  la temporada nueva para poder validarlos). Para MLB si se corrio el
  backtest (ver seccion arriba) y el resultado fue honesto: incluso
  recalibrado, el modelo apenas mejora una linea base ingenua -- tratalo
  con escepticismo extra en esa liga.
- El ajuste por lesiones es una senal cruda: penaliza jugadores marcados
  "Out" por igual, sin distinguir una estrella de un suplente.
- Al inicio de temporada (pretemporada, primeras semanas) puede no haber
  suficientes partidos recientes para confiar en la estimacion; en ese
  caso el modelo reduce su confianza hacia 50/50 en vez de inventar una
  senal, y esos mercados no entran en la recomendacion de portfolio.
- La estimacion de comision de Kalshi usa la formula general publicada
  (`0.07 * precio * (1-precio)` por contrato, escalada por el
  `fee_multiplier` de cada serie); algunos mercados pueden tener
  condiciones especiales que esta aproximacion no capture. Verifica el
  fee schedule real antes de operar.
- Los precios de Kalshi cambian en tiempo real; el edge calculado en un
  reporte puede haber desaparecido para cuando decidas operar.
- Toda decision de operar es tuya, manual, directamente en kalshi.com.
  Esta herramienta nunca ejecuta nada por ti.
