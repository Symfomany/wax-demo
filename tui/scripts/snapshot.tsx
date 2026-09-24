/** Captures d'écran réelles de la TUI (pour le README) : pilote l'application contre un serveur
 * de veille, puis convertit chaque image du terminal (caractères + couleurs) en HTML.
 *
 *   VEILLE_URL=http://127.0.0.1:8766 bun run scripts/snapshot.tsx [scénario…]
 *   → ../docs/screenshots/tui-<scénario>.html (le PNG est produit ensuite par Chromium)
 */
import { act } from "react"
import { testRender } from "@opentui/react/test-utils"
import { mkdirSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"
import { App } from "../src/app"
import { createApi } from "../src/lib/api"

const OUT = resolve(import.meta.dir, "../../docs/screenshots")
const API = createApi(Bun.env.VEILLE_URL ?? "http://127.0.0.1:8766")
const [COLS, ROWS] = [150, 44]
mkdirSync(OUT, { recursive: true })

type Setup = Awaited<ReturnType<typeof testRender>>
const sleep = (ms: number) => act(async () => { await Bun.sleep(ms) })

async function settle(t: Setup, rounds = 4) {
  for (let i = 0; i < rounds; i++) {
    await sleep(60)
    await t.renderOnce()
  }
}
async function key(t: Setup, name: string) {
  await act(async () => { t.mockInput.pressKey(name) })
  await settle(t)
}
async function type(t: Setup, text: string) {
  await act(async () => { await t.mockInput.typeText(text) })
  await act(async () => { t.mockInput.pressEnter() })
  await settle(t)
}
async function until(t: Setup, text: string | RegExp, seconds: number) {
  for (let waited = 0; waited < seconds * 2; waited++) {
    await settle(t, 2)
    const frame = t.captureCharFrame()
    if (typeof text === "string" ? frame.includes(text) : text.test(frame)) return true
    await sleep(380)
  }
  console.warn(`  ⚠ « ${text} » non atteint après ${seconds} s`)
  return false
}

function html(t: Setup, title: string): string {
  const frame = t.captureSpans()
  const color = (c: any, fallback: string) => {
    const [r, g, b, a] = c.toInts()
    return a === 0 ? fallback : `rgb(${r},${g},${b})`
  }
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;")
  const lines = frame.lines.map((line: any) => line.spans.map((s: any) => {
    const style = [`color:${color(s.fg, "#ecebf2")}`, `background:${color(s.bg, "transparent")}`, `width:${s.width}ch`]
    if (s.attributes & 1) style.push("font-weight:700")
    if (s.attributes & 4) style.push("font-style:italic")
    if (s.attributes & 8) style.push("text-decoration:underline")
    return `<span style="${style.join(";")}">${esc(s.text)}</span>`
  }).join("")).join("\n")
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Color+Emoji&display=block" rel="stylesheet">
<style>
body{margin:0;background:#0b0b0e;display:flex;justify-content:center;padding:18px}
.term{background:#121216;border-radius:10px;box-shadow:0 10px 40px #0008;overflow:hidden}
.bar{height:26px;background:#24242c;display:flex;align-items:center;gap:7px;padding:0 12px}
.bar i{width:11px;height:11px;border-radius:50%;display:inline-block}
.bar b{color:#a09fae;font:600 12px system-ui,sans-serif;margin-left:8px}
pre{margin:0;padding:10px 12px;font:15px/1.28 "DejaVu Sans Mono","Noto Color Emoji",monospace;white-space:pre}
pre span{display:inline-block;overflow:hidden;vertical-align:top}
</style></head><body><div class="term"><div class="bar"><i style="background:#f08a80"></i><i style="background:#e6b85c"></i><i style="background:#6fcf97"></i><b>${esc(title)}</b></div><pre>${lines}</pre></div></body></html>`
}

const SCENARIOS: Record<string, { title: string; run: (t: Setup) => Promise<void> }> = {
  home: { title: "bin/veille tui — 🏠 Accueil", run: async (t) => { await until(t, "documents", 20) } },
  chat: {
    title: "bin/veille tui — 💬 Chat (réponse sourcée en streaming)",
    run: async (t) => {
      await key(t, "2")
      await type(t, "C'est quoi le KV cache et pourquoi il limite le serving ?")
      await until(t, "⏱", 180)
    },
  },
  review: {
    title: "bin/veille tui — 🔬 Review d'une URL",
    run: async (t) => {
      await key(t, "4")
      await key(t, "u")
      await type(t, Bun.env.SNAPSHOT_REVIEW_URL ?? "https://arxiv.org/abs/2309.06180")
      await until(t, "Pertinence", 400)
    },
  },
  challenge: {
    title: "bin/veille tui — 🔬 Challenge de la review",
    run: async (t) => {
      await key(t, "4")
      await until(t, "Pertinence", 20)
      await key(t, "c")
      await type(t, "Le gain de débit annoncé est-il mesuré contre quels systèmes ?")
      await until(t, "⏱", 180)
    },
  },
  watch: {
    title: "bin/veille tui — 🛰️ Veille en direct",
    run: async (t) => {
      await key(t, "5")
      await until(t, /validation attendue|publiée|en cours/, 20)
      await settle(t, 6)
    },
  },
  knowledge: { title: "bin/veille tui — 📚 Knowledge", run: async (t) => { await key(t, "6"); await until(t, "entrées", 20) } },
  sources: {
    title: "bin/veille tui — 🧭 Sources (ajout par URL vérifiée)",
    run: async (t) => {
      await key(t, "7")
      await key(t, "/")
      await type(t, "https://blog.vllm.ai")
      await until(t, "vérifiée", 60)
    },
  },
  reports: { title: "bin/veille tui — 📰 Rapports", run: async (t) => { await key(t, "8"); await until(t, "📖", 20) } },
  memory: { title: "bin/veille tui — 🧠 Mémoire", run: async (t) => { await key(t, "9"); await until(t, "Leçons", 20) } },
  grill: {
    title: "bin/veille tui — 🎯 Grill-me",
    run: async (t) => {
      await key(t, "0")
      await key(t, "RETURN")
      await until(t, "Pourquoi je te demande", 30)
    },
  },
}

const wanted = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(SCENARIOS)
for (const name of wanted) {
  const scenario = SCENARIOS[name]
  if (!scenario) throw new Error(`scénario inconnu : ${name}`)
  console.log(`📸 ${name}…`)
  const t = await testRender(<App api={API} onQuit={() => {}} />, { width: COLS, height: ROWS })
  await settle(t)
  await scenario.run(t)
  await settle(t, 6)
  const file = `${OUT}/tui-${name}.html`
  writeFileSync(file, html(t, scenario.title))
  console.log(`   → ${file}`)
  act(() => t.renderer.destroy())
}
process.exit(0)
