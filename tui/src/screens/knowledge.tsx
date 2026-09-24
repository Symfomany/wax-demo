/** 📚 Base de connaissances : glossaire, règles métiers, prompts cliquables, notes, index des mots-clés. */
import { useKeyboard } from "@opentui/react"
import { useEffect, useMemo, useState } from "react"
import { useActive, useApp } from "../context"
import { Screen, Panel } from "../components/screen"
import { Md, Spinner, submit } from "../components/ui"
import { truncate } from "../lib/format"
import { C } from "../theme"

type Tab = "glossaire" | "regles" | "prompts" | "note" | "index"
const TABS: [Tab, string, string][] = [
  ["glossaire", "g", "📖 Glossaire"], ["regles", "r", "📏 Règles métiers"], ["prompts", "p", "⚡ Prompts"],
  ["note", "n", "📝 Notes"], ["index", "x", "🔤 Index"],
]
const fold = (s: string) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()

export function KnowledgeScreen() {
  const { api, ask, go, pending, toast } = useApp()
  const { active, nav, edit } = useActive("knowledge")
  const [data, setData] = useState<any | null>(null)
  const [index, setIndex] = useState<any[]>([])
  const [tab, setTab] = useState<Tab>("glossaire")
  const [filter, setFilter] = useState("")
  const [selected, setSelected] = useState(0)

  useEffect(() => {
    if (!active || data) return
    api.get("/api/knowledge").then(setData).catch((e) => toast(e.message, "error"))
    api.get("/api/knowledge/index").then(setIndex).catch(() => {})
  }, [active])

  const entries = useMemo(() => {
    const q = fold(filter.trim())
    const match = (text: string) => !q || fold(text).includes(q)
    if (tab === "index")
      return index.filter((i) => match(i.term + " " + i.entries.map((r: any) => r.title).join(" ")))
        .map((i) => ({ id: i.term, title: i.term, kind: "index", refs: i.entries }))
    return (data?.entries ?? [])
      .filter((e: any) => e.kind === tab && match([e.title, e.domain, ...e.aliases, ...e.keywords, e.body, ...e.rules].join(" ")))
      .sort((a: any, b: any) => (tab === "glossaire" ? fold(a.title).localeCompare(fold(b.title)) : 0))
  }, [data, index, tab, filter])
  useEffect(() => setSelected(0), [tab, filter])

  const current: any = entries[selected]
  const byId = (id: string) => data?.entries.find((e: any) => e.id === id)
  const usePrompt = (entry: any) => {
    if (entry.target === "review") {
      pending.review = entry.body.trim()
      go("review")
    } else ask(entry.body.trim())
  }

  useKeyboard((key) => {
    if (!nav) return
    const t = TABS.find(([, k]) => k === key.name)
    if (t) setTab(t[0])
    if (key.name === "a" && current && tab !== "index")
      ask(current.kind === "regles" ? `Quelles sont les règles métiers pour le domaine ${current.domain || current.title} ?` : `C'est quoi ${current.title} ?`)
  })

  const detail = tab === "index" && current ? byId(current.refs[0]?.id) : current
  return (
    <Screen id="knowledge" keys={edit ? [["Entrée", "filtrer"], ["Échap", "liste"]]
      : [["g r p n x", "onglets"], ["/", "filtrer"], ["↑↓", "entrées"], ["Entrée", tab === "prompts" ? "envoyer le prompt" : "—"], ["a", "demander au chat"]]}>
      <text>
        {TABS.map(([id, k, label]) => (
          <span key={id} fg={id === tab ? C.bg : C.muted} bg={id === tab ? C.accent : undefined}>{` ${k} ${label} `}</span>
        ))}
        <span fg={C.dim}>   {data ? `${data.entries.length} entrées · ${data.files.length} fichiers` : ""}</span>
      </text>
      <box border borderStyle="rounded" borderColor={edit ? C.accent : C.line} height={3} title=" 🔍 Filtre (terme, alias, domaine, règle…) ">
        <input focused={edit} value={filter} onInput={setFilter} onSubmit={submit(() => {})} placeholder="ex. quantification, RAG, agents" />
      </box>
      {!data ? <box height={1}><Spinner label="chargement de la base de connaissances…" /></box> : null}
      <box flexDirection="row" flexGrow={1}>
        <Panel title={`${TABS.find(([id]) => id === tab)![2]} (${entries.length})`} width="38%" focused={nav}>
          {entries.length ? (
            <select focused={nav} flexGrow={1} showDescription={tab !== "glossaire"} backgroundColor={C.bg} focusedBackgroundColor={C.bg}
              selectedBackgroundColor={C.panel2} selectedTextColor={C.accent} descriptionColor={C.dim}
              options={entries.map((e: any) => ({
                name: tab === "prompts" ? `${e.target === "review" ? "🔬" : "💬"} ${e.title}` : tab === "index" ? `${e.title}` : e.title,
                description: tab === "index" ? truncate(e.refs.map((r: any) => r.title).join(", "), 40) : e.domain ?? "",
                value: e.id,
              }))}
              onChange={(i) => setSelected(i)}
              onSelect={(i) => tab === "prompts" && entries[i] && usePrompt(entries[i])} />
          ) : <text fg={C.muted}>Aucune entrée.</text>}
        </Panel>
        <Panel title={detail ? `📖 ${detail.title}` : "Détail"}>
          {detail ? (
            <scrollbox flexGrow={1}>
              <text>
                {detail.domain ? <span fg={C.accent}>[{detail.domain}] </span> : null}
                <span fg={C.dim}>{detail.kind} · {detail.file}{detail.origin === "upload" ? " (téléversé)" : ""}</span>
              </text>
              {tab === "index" ? <text fg={C.muted} wrapMode="word">Renvoie à : {current.refs.map((r: any) => `${r.title} (${r.role})`).join(", ")}</text> : null}
              {detail.rules?.length
                ? detail.rules.map((r: string, i: number) => <text key={i} wrapMode="word"><span fg={C.accent}>  {i + 1}. </span>{r}</text>)
                : <Md content={detail.body} />}
              {detail.aliases?.length ? <text fg={C.muted} wrapMode="word">Alias : {detail.aliases.join(", ")}</text> : null}
              {detail.keywords?.length ? <text fg={C.muted} wrapMode="word">Mots-clés : {detail.keywords.join(", ")}</text> : null}
              {detail.sources?.map((u: string) => <text key={u} fg={C.link} wrapMode="char">🔗 {u}</text>)}
              {detail.kind === "prompts" ? <text fg={C.accent}>Entrée : {detail.target === "review" ? "poser dans le challenge de la review" : "envoyer au chat"}</text> : null}
            </scrollbox>
          ) : <text fg={C.muted}>—</text>}
        </Panel>
      </box>
    </Screen>
  )
}
