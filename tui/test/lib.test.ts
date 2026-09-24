import { describe, expect, test } from "bun:test"
import { parseSSE } from "../src/lib/sse"
import { bar, runStage, truncate, wrap, percent } from "../src/lib/format"
import { applyChatEvent } from "../src/lib/chat"
import { createApi } from "../src/lib/api"
import { CHAT_EVENTS } from "./fake-api"

describe("SSE", () => {
  test("découpe les événements et garde le reste incomplet", () => {
    const { events, rest } = parseSSE('data: {"type":"a"}\n\ndata: {"type":"b"}\n\ndata: {"ty')
    expect(events).toEqual([{ type: "a" }, { type: "b" }])
    expect(rest).toBe('data: {"ty')
  })
  test("ignore les lignes non JSON", () => {
    expect(parseSSE("data: pas du json\n\n: ping\n\n").events).toEqual([])
  })
})

describe("format", () => {
  test("barre de progression bornée", () => {
    expect(bar(5, 10, 10)).toBe("█████░░░░░")
    expect(bar(20, 10, 4)).toBe("████")
    expect(bar(1, 0, 3)).toBe("░░░")
    expect(percent(3, 6)).toBe("50%")
  })
  test("troncature et retour à la ligne", () => {
    expect(truncate("  un   texte  trop long ", 8)).toBe("un text…")
    expect(wrap("aaa bbb ccc", 7)).toEqual(["aaa bbb", "ccc"])
  })
  test("étapes d'une veille d'après ses événements", () => {
    expect(runStage([], "running")).toBe(0)
    expect(runStage([{ type: "collect" }, { type: "agent", node: "research" }], "running")).toBe(2)
    expect(runStage([{ type: "agent", node: "editorial" }], "running")).toBe(4)
    expect(runStage([], "awaiting_approval")).toBe(5)
    expect(runStage([], "published")).toBe(6)
  })
})

describe("chat", () => {
  test("les événements SSE construisent la réponse", () => {
    let messages: any[] = [{ role: "user", text: "q" }, { role: "assistant", text: "", streaming: true }]
    for (const event of CHAT_EVENTS.slice(1, 5)) messages = applyChatEvent(messages, event)
    expect(messages[1]).toMatchObject({ tool: "knowledge_search", toolDone: true, text: "Le KV cache mémorise les clés et valeurs [1].", streaming: true })
    messages = applyChatEvent(messages, CHAT_EVENTS[5])
    expect(messages[1].streaming).toBe(false)
    expect(messages[1].sources[0].title).toBe("KV cache")
    expect(applyChatEvent(messages, { type: "inconnu" })).toBe(messages)
  })
})

describe("api", () => {
  const fetcher = (async (url: string, init: any) => {
    if (url.endsWith("/api/missing")) return new Response(JSON.stringify({ detail: "Review inconnue" }), { status: 404 })
    if (url.endsWith("/api/chat")) {
      const body = new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('data: {"type":"token","text":"a"}\n\ndata: {"ty'))
          controller.enqueue(new TextEncoder().encode('pe":"final"}\n\n'))
          controller.close()
        },
      })
      return new Response(body, { status: 200 })
    }
    return new Response(JSON.stringify({ url, method: init.method, body: init.body }), { status: 200 })
  }) as any

  test("requêtes JSON et erreurs lisibles", async () => {
    const api = createApi("http://x:1/", fetcher)
    expect(await api.post<any>("/api/search", { keywords: ["mcp"] })).toEqual({ url: "http://x:1/api/search", method: "POST", body: '{"keywords":["mcp"]}' })
    await expect(api.get("/api/missing")).rejects.toThrow("Review inconnue")
  })
  test("flux SSE découpé sur plusieurs paquets", async () => {
    const events: any[] = []
    await createApi("http://x:1", fetcher).stream("/api/chat", { message: "m" }, (e) => events.push(e))
    expect(events).toEqual([{ type: "token", text: "a" }, { type: "final" }])
  })
})

import { stripFrontMatter } from "../src/components/ui"

describe("markdown", () => {
  test("l'en-tête YAML des rapports est masqué", () => {
    expect(stripFrontMatter("---\ntitle: x\n---\n\n# Titre\ntexte")).toBe("# Titre\ntexte")
    expect(stripFrontMatter("# Sans en-tête")).toBe("# Sans en-tête")
  })
})
