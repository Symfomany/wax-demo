/** 🔎 Recherche plein texte dans les documents collectés (FTS5), puis chat ou veille ciblée. */
import { useKeyboard } from "@opentui/react"
import { useState } from "react"
import { useActive, useApp } from "../context"
import { Screen, Panel } from "../components/screen"
import { Spinner, submit } from "../components/ui"
import { shortDate, sourceIcon, truncate } from "../lib/format"
import { C } from "../theme"

export function SearchScreen() {
  const { api, ask, go, toast, pending } = useApp()
  const { nav, edit } = useActive("search")
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<any[]>([])
  const [selected, setSelected] = useState(0)
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)
  const { setMode } = useApp()

  const keywords = (text: string) => text.split(/[,;]/).map((k) => k.trim()).filter(Boolean)

  const search = async (text: string) => {
    const words = keywords(text)
    if (!words.length) return
    setLoading(true)
    try {
      const response = await api.post("/api/search", { keywords: words, limit: 40 })
      setResults(response.results)
      setSelected(0)
      setDone(true)
      setMode("nav")
    } catch (error: any) {
      toast(error.message, "error")
    }
    setLoading(false)
  }

  useKeyboard((key) => {
    if (!nav || !keywords(query).length) return
    if (key.name === "a") ask(`Que disent mes sources sur ${keywords(query).join(", ")} ?`)
    if (key.name === "w") {
      pending.watch = keywords(query).join(", ")
      go("watch")
    }
  })

  const current = results[selected]
  return (
    <Screen id="search" keys={edit ? [["Entrée", "rechercher"], ["Échap", "résultats"]]
      : [["/", "mots-clés"], ["↑↓", "résultats"], ["a", "demander au chat"], ["w", "veille ciblée"]]}>
      <box border borderStyle="rounded" borderColor={edit ? C.accent : C.line} height={3} title=" 🔎 Mots-clés (séparés par des virgules) ">
        <input focused={edit} value={query} onInput={setQuery} onSubmit={submit(search)} placeholder="MCP, quantization, RAG…" />
      </box>
      <box height={1}>{loading ? <Spinner label="recherche…" /> : null}</box>
      <box flexDirection="row" flexGrow={1}>
        <Panel title={`📄 ${results.length} résultat(s)`} focused={nav} width="55%">
          {results.length ? (
            <select focused={nav} flexGrow={1} showDescription={false} backgroundColor={C.bg} focusedBackgroundColor={C.bg}
              selectedBackgroundColor={C.panel2} selectedTextColor={C.accent}
              options={results.map((r) => ({
                name: `${sourceIcon(r.source)} ${shortDate(r.published_at)} ${r.published ? "✅" : "  "} ${truncate(r.title, 60)}`,
                description: "", value: r.url,
              }))}
              onChange={(index) => setSelected(index)} />
          ) : (
            <text fg={C.muted}>{done ? "Aucun document : élargissez les mots-clés ou lancez une veille ciblée (w)." : "Tapez des mots-clés puis Entrée."}</text>
          )}
        </Panel>
        <Panel title="🔍 Détail">
          {current ? (
            <box flexDirection="column">
              <text wrapMode="word"><strong>{current.title}</strong></text>
              <text fg={C.muted}>{sourceIcon(current.source)} {current.source} · {shortDate(current.published_at)}{current.published ? " · ✅ publié dans un digest" : ""}</text>
              <text fg={C.link} wrapMode="char">{current.url}</text>
              <text> </text>
              <text wrapMode="word">{current.summary}</text>
            </box>
          ) : <text fg={C.muted}>—</text>}
        </Panel>
      </box>
    </Screen>
  )
}
