/** 🧠 Mémoire : leçons humaines, thèmes appris, santé des sources, profil Grill-me, publication Notion. */
import { useKeyboard } from "@opentui/react"
import { useEffect, useState } from "react"
import { useActive, useApp } from "../context"
import { Screen, Panel } from "../components/screen"
import { Spinner } from "../components/ui"
import { bar } from "../lib/format"
import { C } from "../theme"

export function MemoryScreen() {
  const { api, toast, health } = useApp()
  const { active, nav } = useActive("memory")
  const [memory, setMemory] = useState<any | null>(null)
  const [syncing, setSyncing] = useState(false)

  const load = () => api.get("/api/memory").then(setMemory).catch((e) => toast(e.message, "error"))
  useEffect(() => { if (active) load() }, [active])

  useKeyboard(async (key) => {
    if (!nav || key.name !== "s" || syncing) return
    setSyncing(true)
    try {
      const result = await api.post("/api/notion/sync")
      toast(`📝 Notion à jour (${result.digests} veille(s))`, "ok")
      load()
    } catch (e: any) {
      toast(e.message, "error")
    }
    setSyncing(false)
  })

  const tags: [string, number][] = memory?.tags ?? []
  const maxTag = Math.max(1, ...tags.map(([, s]) => s))
  const counts = health?.memory ?? {}
  return (
    <Screen id="memory" keys={[["s", "publier dans Notion"], ["5", "lancer une veille"]]}>
      {!memory ? <Spinner label="chargement de la mémoire…" /> : (
        <box flexDirection="row" flexGrow={1}>
          <box flexDirection="column" width="50%">
            <Panel title="👤 Leçons humaines (validations)">
              {memory.lessons.length ? memory.lessons.map((l: any, i: number) => (
                <text key={i} wrapMode="word">
                  {l.approved ? "✅" : "❌"} {l.note}<span fg={C.dim}> · {String(l.created_at).slice(0, 10)}</span>
                </text>
              )) : <text fg={C.muted}>Aucune leçon : ajoutez une note en validant une veille.</text>}
            </Panel>
            <Panel title="🏷️ Thèmes appris">
              {tags.length ? tags.map(([tag, score]) => (
                <text key={tag}>
                  <span>{tag.padEnd(18).slice(0, 18)}</span>
                  <span fg={C.accent}>{bar(score, maxTag, 20).replace(/░/g, "")}</span>
                  <span fg={C.dim}> {score}</span>
                </text>
              )) : <text fg={C.muted}>Aucune préférence apprise.</text>}
            </Panel>
          </box>
          <box flexDirection="column" flexGrow={1}>
            <Panel title="🎯 Profil Grill-me">
              {memory.interests?.summary ? (
                <box flexDirection="column">
                  <text wrapMode="word">{memory.interests.summary}</text>
                  <text fg={C.accent} wrapMode="word">🔑 {(memory.interests.keywords ?? []).join(", ")}</text>
                  {memory.interests.exclusions?.length ? <text fg={C.bad} wrapMode="word">🚫 {memory.interests.exclusions.join(", ")}</text> : null}
                </box>
              ) : <text fg={C.muted}>Aucun profil : écran 0 (Grill-me).</text>}
            </Panel>
            <Panel title="🩺 Santé des sources">
              {Object.entries(memory.sources).length ? Object.entries(memory.sources).sort().map(([name, v]: [string, any]) => (
                <text key={name}>
                  <span>{name.padEnd(16)}</span>
                  <span fg={C.ok}>{"█".repeat(Math.min(12, v.ok))}</span>
                  <span fg={C.bad}>{"█".repeat(Math.min(12, v.failures))}</span>
                  <span fg={C.muted}> {v.ok} OK · {v.failures} échec(s)</span>
                </text>
              )) : <text fg={C.muted}>Aucune donnée.</text>}
            </Panel>
            <Panel title="📝 Notion · 🗄️ SQLite" height={7}>
              {syncing ? <Spinner label="publication Notion via MCP (images, couverture, sections)…" /> : (
                <text fg={memory.notion ? C.link : C.muted} wrapMode="char">
                  {memory.notion ? `Page : ${memory.notion.url}` : "Aucune page publiée. s pour publier."}
                </text>
              )}
              <text fg={C.muted}>
                📄 {counts.documents ?? "–"} documents · 🗞️ {counts.digests ?? "–"} digests · 🔬 {counts.reviews ?? "–"} reviews · 🧭 {counts.traces ?? "–"} traces
              </text>
            </Panel>
          </box>
        </box>
      )}
    </Screen>
  )
}
