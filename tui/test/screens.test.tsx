import { afterEach, expect, test } from "bun:test"
import { act } from "react"
import { testRender } from "@opentui/react/test-utils"
import { App } from "../src/app"
import { fakeApi } from "./fake-api"

let setup: Awaited<ReturnType<typeof testRender>> | null = null
afterEach(() => {
  act(() => setup?.renderer.destroy())
  setup = null
})

async function start(calls: string[] = [], screen: any = "home") {
  const quits: number[] = []
  setup = await testRender(<App api={fakeApi(undefined, calls)} initialScreen={screen} onQuit={() => quits.push(1)} />, { width: 150, height: 44 })
  await settle()
  return { quits }
}

async function settle(ms = 30) {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await Bun.sleep(ms) })
    await setup!.renderOnce()
  }
}

async function press(key: string, modifiers?: any) {
  await act(async () => { setup!.mockInput.pressKey(key, modifiers) })
  await settle()
}

async function type(text: string) {
  await act(async () => { await setup!.mockInput.typeText(text) })
  await act(async () => { setup!.mockInput.pressEnter() })
  await settle(40)
}

const frame = () => setup!.captureCharFrame()

test("accueil : services, compteurs, raccourcis et dernier rapport", async () => {
  await start()
  const f = frame()
  expect(f).toContain("gemma-3-4b-it")
  expect(f).toContain("200 documents")
  expect(f).toContain("Faire la review d'une URL")
  expect(f).toContain("validation attendue")
  expect(f).toContain("Les avancées récentes portent sur vLLM")
})

test("navigation : chiffres, flèches, F-keys, aide et quitter", async () => {
  const { quits } = await start()
  await press("4")
  expect(frame()).toContain("Reviews (1)")
  await press("ARROW_RIGHT")
  expect(frame()).toContain("Journal")
  await press("F6")
  expect(frame()).toContain("Filtre (terme, alias")
  await press("?")
  expect(frame()).toContain("Raccourcis")
  await press("x")
  await press("q")
  expect(quits.length).toBe(1)
})

test("chat : streaming, outil, sources et parcours du graphe", async () => {
  const calls: string[] = []
  await start(calls, "chat")
  await type("C'est quoi le KV cache ?")
  const f = frame()
  expect(calls).toContain('STREAM /api/chat {"message":"C\'est quoi le KV cache ?","conversation_id":null}')
  expect(f).toContain("Base de connaissances · 3 entrée(s)")
  expect(f).toContain("Le KV cache mémorise les clés et valeurs [1].")
  expect(f).toContain("[1] 📚 KV cache")
  expect(f).toContain("route → act → respond → guard")
})

test("review : liste, scores, affirmations vérifiées, règles et challenge", async () => {
  const calls: string[] = []
  await start(calls)
  await press("4")
  let f = frame()
  expect(f).toContain("Pertinence")
  expect(f).toContain("9/10")
  expect(f).toContain("non étayée")
  expect(f).toContain("R11")
  await press("c")
  await type("Le 2-4× est-il mesuré ?")
  expect(calls.some((c) => c.startsWith('STREAM /api/chat {"message":"Le 2-4× est-il mesuré ?","review_id":"r1"}'))).toBe(true)
  expect(frame()).toContain("Challenge")
})

test("review : nouvelle URL avec étapes et barre de progression", async () => {
  const calls: string[] = []
  await start(calls)
  await press("4")
  await press("u")
  await type("https://arxiv.org/abs/2309.06180")
  const f = frame()
  expect(calls).toContain('STREAM /api/reviews {"url":"https://arxiv.org/abs/2309.06180"}')
  expect(f).toContain("100%")
  expect(f).toContain("Guard : citations vérifiées dans la page")
})

test("veille : étapes, journal et digest à valider", async () => {
  const calls: string[] = []
  await start(calls)
  await press("5")
  await act(async () => { await Bun.sleep(1400) })
  await settle()
  const f = frame()
  expect(f).toContain("validation attendue")
  expect(f).toContain("collecte rss : +24")
  expect(f).toContain("Digest à valider")
  expect(f).toContain("✅ Collecte")
  await press("x")
  expect(frame()).toContain("Motif du rejet")
})

test("sources : ajout par URL vérifiée puis confirmation", async () => {
  const calls: string[] = []
  await start(calls)
  await press("7")
  expect(frame()).toContain("Hugging Face Blog")
  await press("/")
  await type("https://blog.vllm.ai")
  expect(frame()).toContain("flux RSS/Atom vérifiée")
  await press("y")
  expect(calls).toContain('BODY /api/sources {"url":"https://blog.vllm.ai","name":"vLLM Blog"}')
})

test("knowledge : onglets, détail et prompt envoyé au chat", async () => {
  const calls: string[] = []
  await start(calls)
  await press("6")
  expect(frame()).toContain("Mémorisation des keys et values")
  await press("r")
  expect(frame()).toContain("Préciser le niveau d'autonomie")
  await press("p")
  await act(async () => { setup!.mockInput.pressEnter() })
  await settle()
  expect(calls.some((c) => c.startsWith('STREAM /api/chat {"message":"Quoi de neuf dans les dernières veilles ?"'))).toBe(true)
})

test("mémoire et grill-me", async () => {
  const calls: string[] = []
  await start(calls)
  await press("9")
  expect(frame()).toContain("Écarter les tutoriels")
  await press("s")
  expect(calls).toContain("POST /api/notion/sync")
  await press("0")
  await act(async () => { setup!.mockInput.pressEnter() })
  await settle()
  expect(frame()).toContain("Sur quels pans de l'actualité IA")
  expect(frame()).toContain("★ recommandé")
})
