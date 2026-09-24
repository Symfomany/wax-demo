/** 🧭 Sources : liste, ajout par URL vérifiée (aperçu puis confirmation), retrait confirmé. */
import { useKeyboard } from "@opentui/react"
import { useEffect, useState } from "react"
import { useActive, useApp } from "../context"
import { Screen, Panel } from "../components/screen"
import { Spinner, submit } from "../components/ui"
import { bar, truncate } from "../lib/format"
import { C } from "../theme"

const KINDS: Record<string, [string, string, string]> = {
  rss: ["📰", "Flux RSS/Atom", "rss"], arxiv: ["📄", "arXiv", "arxiv"], github: ["🐙", "Releases GitHub", "github_releases"],
  github_mcp: ["🔌", "Découverte MCP", "github_mcp"],
}

export function SourcesScreen() {
  const { api, toast, setMode } = useApp()
  const { active, nav, edit } = useActive("sources")
  const [data, setData] = useState<any | null>(null)
  const [url, setUrl] = useState("")
  const [checking, setChecking] = useState(false)
  const [candidate, setCandidate] = useState<any | null>(null)
  const [error, setError] = useState("")
  const [selected, setSelected] = useState(0)
  const [confirm, setConfirm] = useState<string | null>(null)

  const load = () => api.get("/api/sources").then(setData).catch((e) => toast(e.message, "error"))
  useEffect(() => { if (active) load() }, [active])

  const rows: { kind: string; value: string; label: string }[] = data ? [
    ...data.rss.map((r: any) => ({ kind: "rss", value: r.url, label: `${r.name} — ${r.url}` })),
    ...data.arxiv.map((v: string) => ({ kind: "arxiv", value: v, label: v })),
    ...data.github.map((v: string) => ({ kind: "github", value: v, label: v })),
    ...data.github_mcp.map((v: string) => ({ kind: "github_mcp", value: v, label: `requête « ${v} »` })),
  ] : []

  const inspect = async (value: string) => {
    if (!value.trim()) return
    setChecking(true)
    setCandidate(null)
    setError("")
    setMode("nav")
    try {
      setCandidate(await api.post("/api/sources/inspect", { url: value.trim() }))
    } catch (e: any) {
      setError(e.message)
    }
    setChecking(false)
  }

  useKeyboard(async (key) => {
    if (!nav) return
    if (candidate && !candidate.already_present && key.name === "y") {
      try {
        await api.post("/api/sources", { url: candidate.input_url, name: candidate.name })
        toast(`➕ ${candidate.name} ajoutée : collectée à la prochaine veille`, "ok")
        setCandidate(null)
        setUrl("")
        load()
      } catch (e: any) {
        toast(e.message, "error")
      }
    }
    if (candidate && key.name === "n") setCandidate(null)
    const row = rows[selected]
    if (key.name === "d" && row && row.kind !== "github_mcp") {
      if (confirm !== row.value) {
        setConfirm(row.value)
        toast(`Retirer ${truncate(row.value, 50)} ? d pour confirmer`)
        return
      }
      try {
        await api.del(`/api/sources?kind=${row.kind}&value=${encodeURIComponent(row.value)}`)
        toast("🗑️ Source retirée", "ok")
        load()
      } catch (e: any) {
        toast(e.message, "error")
      }
      setConfirm(null)
    }
  })

  const health = data?.health ?? {}
  return (
    <Screen id="sources" keys={edit ? [["Entrée", "vérifier"], ["Échap", "liste"]]
      : candidate ? [["y", "ajouter"], ["n", "annuler"]] : [["/", "ajouter une URL"], ["↑↓", "sources"], ["d d", "retirer"]]}>
      <box border borderStyle="rounded" borderColor={edit ? C.accent : C.line} height={3}
        title=" ➕ URL d'une source : blog, flux RSS/Atom, dépôt GitHub, catégorie arXiv ">
        <input focused={edit} value={url} onInput={setUrl} onSubmit={submit(inspect)} placeholder="https://blog.vllm.ai · github.com/ggml-org/llama.cpp · arxiv.org/list/cs.LG" />
      </box>
      <box height={1}>{checking ? <Spinner label="vérification : réponse, entrées datées, source primaire…" /> : null}</box>
      {error ? <text fg={C.bad} wrapMode="word" height={2}>❌ {error}</text> : null}
      {candidate ? (
        <box border borderStyle="rounded" borderColor={candidate.already_present ? C.warn : C.ok} flexDirection="column" paddingLeft={1}
          height={5 + candidate.warnings.length}
          title={` ✅ ${candidate.label} vérifiée `}>
          <text><strong>{candidate.name}</strong><span fg={C.dim}>  {candidate.value}</span></text>
          <text fg={C.muted} wrapMode="word">{candidate.entries} entrée(s) · dernière : {candidate.sample_date ?? "?"} — {candidate.sample_title}</text>
          {candidate.warnings.map((w: string) => <text key={w} fg={C.warn}>⚠️ {w}</text>)}
          <text fg={candidate.already_present ? C.warn : C.ok}>
            {candidate.already_present ? "Déjà présente dans sources.toml." : "y : ajouter à sources.toml · n : annuler"}
          </text>
        </box>
      ) : null}
      <box flexDirection="row" flexGrow={1}>
        <Panel title={`🧭 Sources (${rows.length})`} focused={nav}>
          {rows.length ? (
            <select focused={nav && !candidate} flexGrow={1} showDescription={false} backgroundColor={C.bg} focusedBackgroundColor={C.bg}
              selectedBackgroundColor={C.panel2} selectedTextColor={C.accent}
              options={rows.map((r) => ({ name: `${KINDS[r.kind][0]} ${truncate(r.label, 90)}`, description: "", value: r.value }))}
              onChange={(i) => { setSelected(i); setConfirm(null) }} />
          ) : <Spinner label="chargement…" />}
        </Panel>
        <Panel title="🩺 Santé des collecteurs" width={44}>
          {Object.entries(KINDS).map(([kind, [icon, label, collector]]) => {
            const h = health[collector]
            const total = h ? h.ok + h.failures : 0
            return (
              <box key={kind} flexDirection="column">
                <text>{icon} <strong>{label}</strong><span fg={C.dim}> · {rows.filter((r) => r.kind === kind).length} source(s)</span></text>
                <text>
                  <span fg={C.ok}>{h ? bar(h.ok, Math.max(1, total), 16).replace(/░/g, "") : ""}</span>
                  <span fg={C.bad}>{h ? "█".repeat(16 - bar(h.ok, Math.max(1, total), 16).replace(/░/g, "").length) : ""}</span>
                  <span fg={C.muted}> {h ? `${h.ok} OK · ${h.failures} échec(s)` : "pas encore collecté"}</span>
                </text>
              </box>
            )
          })}
        </Panel>
      </box>
    </Screen>
  )
}
