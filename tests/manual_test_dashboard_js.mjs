import { readFileSync } from "fs";

const html = readFileSync(new URL("../output/dashboard.html", import.meta.url), "utf-8");
const start = html.indexOf("<script>") + "<script>".length;
const end = html.indexOf("</script>");
let js = html.slice(start, end);

// Quitamos la ultima linea que ejecuta render() contra el DOM real (no
// disponible en este harness); probamos solo la logica pura.
js = js.replace(/\nrender\(\);\s*$/, "");

// Inyectamos un DATA sintetico con una oportunidad claramente positiva
// para verificar que buildRecommendation genera una asignacion real.
const syntheticData = {
  generated_at: new Date().toISOString(),
  date: "2026-11-01",
  bankroll: 500,
  leagues_analyzed: ["NBA"],
  opportunities_for_date: [
    {
      market_ticker: "KXNBAGAME-TEST-AAA", league: "NBA",
      team_abbreviation: "AAA", opponent_abbreviation: "BBB", is_home: true,
      kalshi_implied_probability: 0.40, kalshi_yes_ask: 0.40,
      model_probability: 0.58, model_confidence: "alta",
      edge: 0.18, ev_per_contract_after_fee: 0.16, ev_return_on_cost: 0.40,
      model_notes: ["Nota de prueba"],
    },
    {
      market_ticker: "KXNBAGAME-TEST-CCC", league: "NBA",
      team_abbreviation: "CCC", opponent_abbreviation: "DDD", is_home: false,
      kalshi_implied_probability: 0.55, kalshi_yes_ask: 0.55,
      model_probability: 0.50, model_confidence: "ninguna",
      edge: -0.05, ev_per_contract_after_fee: -0.06, ev_return_on_cost: -0.11,
      model_notes: ["Sin datos suficientes"],
    },
  ],
};

const wrapped = js + `
globalThis.__test_exports = { buildRecommendation, renderOpportunitiesTable, renderRecommendation, pct, money };
`;
eval(wrapped);
const { buildRecommendation, renderOpportunitiesTable, renderRecommendation, pct, money } = globalThis.__test_exports;

const rec = buildRecommendation(syntheticData.opportunities_for_date, syntheticData.bankroll, syntheticData.date);
console.log("has_recommendation:", rec.has_recommendation);
console.log("allocations:", JSON.stringify(rec.allocations, null, 2));

if (!rec.has_recommendation) throw new Error("Se esperaba una recomendacion con la oportunidad sintetica de alto edge");
if (rec.allocations.length !== 1) throw new Error("Se esperaba exactamente 1 allocation (la de confianza 'ninguna' debe quedar excluida)");
const a = rec.allocations[0];
if (a.stake_fraction_of_bankroll > 0.10 + 1e-9) throw new Error("El tope de 10% por jugada no se respeto");
console.log("OK: tope de 10% por jugada respetado ->", (a.stake_fraction_of_bankroll * 100).toFixed(1) + "%");

const tableHtml = renderOpportunitiesTable(syntheticData.opportunities_for_date);
if (!tableHtml.includes("AAA") || !tableHtml.includes("58.0%")) throw new Error("La tabla de oportunidades no genero el contenido esperado");
console.log("OK: tabla de oportunidades genera HTML con los valores esperados");

const recHtml = renderRecommendation(rec);
if (!recHtml.toLowerCase().includes("combinadas")) throw new Error("Falta el aviso anti-parlay en el HTML renderizado");
if (!recHtml.includes("AAA")) throw new Error("La recomendacion no incluye la jugada esperada");
console.log("OK: HTML de recomendacion incluye aviso anti-parlay y la jugada esperada");

console.log("\nTodas las pruebas del JS del dashboard pasaron.");
