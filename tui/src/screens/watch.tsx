/** 🛰️ Veille : lancement (ciblé ou non), progression en direct, validation humaine (publier / rejeter). */
import { useKeyboard } from "@opentui/react"
import { useEffect, useRef, useState } from "react"
import { useActive, useApp } from "../context"
import { Screen, Panel } from "../components/screen"
import { Md, ProgressBar, Spinner, Steps, submit } from "../components/ui"
import { RUN_STAGES, RUN_STATES, dateTime, runStage, truncate } from "../lib/format"
import { C } from "../theme"

const ICONS: Record<string, string> = {
  step: "🧭", collect: "📥", agent: "🤖", warning: "⚠️", error: "❌", awaiting_approval: "✋", published: "✅", decision: "👤", status: "●",
}

export function eventLine(e: any): string {
  switch (e.type) {
    case "step": return `supervisor · ${e.text}`
    case "collect": return `collecte ${e.source} : +${e.count} document(s)`
    case "agent": return `${e.node} : ${e.text}`
    case "awaiting_approval": return "digest prêt : validation humaine attendue"
    case "published": return "publication : fichiers, rapport daté" + (e.outputs?.notion ? ", Notion" : "")
    case "decision": return e.approved ? `publication demandée${e.note ? ` — « ${e.note} »` : ""}` : `rejet — « ${e.note} »`
    case "status": return `statut final : ${RUN_STATES[e.status]?.[1] ?? e.status}`
    default: return e.text ?? JSON.stringify(e)
  }
}

type Field = "keywords" | "approve" | "reject"

export function WatchScreen() {
  const { api, toast, setMode, consume } = useApp()
  const { active, nav, edit } = useActive("watch")
  const [keywords, setKeywords] = useState("")
  const [note, setNote] = useState("")
  const [field, setField] = useState<Field>("keywords")
  const [run, setRun] = useState<any | null>(null)
  const [events, setEvents] = useState<any[]>([])
  const [history, setHistory] = useState<any[]>([])
  const cursor = useRef(0)
  const runId = useRef<string | null>(null)

  const follow = (id: string) => {
    if (runId.current !== id) {
      runId.current = id
      cursor.current = 0
      setEvents([])
    }
  }

  useEffect(() => {
    if (!active) return
    const prefill = consume("watch")
    if (prefill) {
      setKeywords(prefill)
      setField("keywords")
      setMode("edit")
    }
    api.get("/api/runs").then((runs: any[]) => {
      setHistory(runs)
      const current = runs.find((r) => ["running", "awaiting_approval"].includes(r.status)) ?? runs[0]
      if (current && !runId.current) follow(current.id)
    }).catch(() => {})
  }, [active])

  useEffect(() => {
    const timer = setInterval(async () => {
      if (!runId.current) return
      try {
        const data = await api.get(`/api/runs/${runId.current}?after=${cursor.current}`)
        cursor.current = data.next
        if (data.events.length) setEvents((e) => [...e, ...data.events])
        setRun(data)
      } catch {}
    }, 1200)
    return () => clearInterval(timer)
  }, [api])

  const start = async (text: string) => {
    const focus = text.split(/[,;]/).map((k) => k.trim()).filter(Boolean)
    try {
      const { id } = await api.post("/api/runs", { keywords: focus })
      follow(id)
      setMode("nav")
      toast(`🛰️ Veille lancée${focus.length ? ` · focus ${focus.join(", ")}` : ""}`, "ok")
    } catch (e: any) {
      toast(e.message, "error")
    }
  }

  const decide = async (approved: boolean, text: string) => {
    if (!approved && !text.trim()) return toast("Le motif du rejet est obligatoire : il devient une leçon", "error")
    try {
      await api.post(`/api/runs/${runId.current}/decision`, { approved, note: text.trim() })
      setNote("")
      setField("keywords")
      setMode("nav")
      toast(approved ? "✅ Publication en cours…" : "🗑️ Digest rejeté, leçon mémorisée", "ok")
    } catch (e: any) {
      toast(e.message, "error")
    }
  }

  useKeyboard((key) => {
    if (!nav) return
    if (key.name === "l" || key.name === "return") { setField("keywords"); setMode("edit") }
    if (run?.status === "awaiting_approval") {
      if (key.name === "p") { setField("approve"); setMode("edit") }
      if (key.name === "x") { setField("reject"); setMode("edit") }
    }
  })

  const status = run?.status ?? "none"
  const stage = runStage(events, status)
  const running = status === "running"
  const waiting = status === "awaiting_approval"
  const titles: Record<Field, string> = {
    keywords: " 🎯 Focus (mots-clés, virgules) — Entrée pour lancer ",
    approve: " ✅ Note de publication (facultative, mémorisée) ",
    reject: " 🗑️ Motif du rejet (obligatoire, devient une leçon) ",
  }
  return (
    <Screen id="watch" keys={edit ? [["Entrée", field === "keywords" ? "lancer" : "valider"], ["Échap", "annuler"]]
      : waiting ? [["p", "publier"], ["x", "rejeter"], ["↑↓", "digest"], ["l", "nouvelle veille"]]
        : [["l / Entrée", "lancer une veille"], ["↑↓", "journal"]]}>
      <box border borderStyle="rounded" borderColor={edit ? (field === "reject" ? C.bad : C.accent) : C.line} height={3} title={titles[field]}>
        {field === "keywords" ? (
          <input focused={edit} value={keywords} onInput={setKeywords} onSubmit={submit(start)} placeholder="vide = veille complète · ex. MCP, agents, quantization" />
        ) : (
          <input focused={edit} value={note} onInput={setNote} onSubmit={submit((v: string) => decide(field === "approve", v))}
            placeholder={field === "approve" ? "Ex. Garder le focus inférence GPU" : "Ex. Trop de tutoriels, privilégier les releases"} />
        )}
      </box>
      <box flexDirection="column" marginBottom={1} height={running ? 4 : 3}>
        <text height={1}>
          <strong>{RUN_STATES[status]?.[0] ?? "○"} {RUN_STATES[status]?.[1] ?? "aucune veille"}</strong>
          {run ? <span fg={C.dim}>  {run.id.slice(0, 8)} · {dateTime(run.started_at)}</span> : null}
          {run?.options?.keywords?.length ? <span fg={C.accent}>  🎯 {run.options.keywords.join(run.options.match_all ? " ET " : " OU ")}</span> : null}
        </text>
        <ProgressBar value={stage} max={RUN_STAGES.length} width={36} color={status === "failed" || status === "blocked" ? C.bad : C.accent} />
        <Steps steps={RUN_STAGES} current={stage} running={running || waiting} />
        {running ? <Spinner label="les agents travaillent… (collecte parallèle, Scout, Critic, Editor)" /> : null}
      </box>
      <box flexDirection="row" flexGrow={1}>
        <Panel title="🧭 Journal" width={waiting ? "40%" : undefined} focused={nav && !waiting}>
          <scrollbox flexGrow={1} stickyScroll stickyStart="bottom" focused={nav && !waiting}>
            {events.length ? events.map((e, i) => (
              <text key={i} wrapMode="word">
                <span fg={C.dim}>{(e.at ?? "").slice(11, 19)} </span>
                <span>{ICONS[e.type] ?? "•"} </span>
                <span fg={e.type === "warning" ? C.warn : e.type === "error" ? C.bad : C.text}>{eventLine(e)}</span>
              </text>
            )) : <text fg={C.muted}>Aucun événement. l ou Entrée pour lancer une veille.</text>}
            {history.length > 1 ? <text fg={C.dim}>{"\n"}Veilles de cette session : {history.slice(0, 5).map((r) => `${RUN_STATES[r.status]?.[0] ?? "●"} ${r.id.slice(0, 6)}`).join("  ")}</text> : null}
          </scrollbox>
        </Panel>
        {waiting ? (
          <Panel title="📰 Digest à valider — p publier · x rejeter" focused={nav}>
            <scrollbox flexGrow={1} focused={nav}>
              <Md content={run.markdown ?? ""} />
            </scrollbox>
          </Panel>
        ) : null}
        {status === "published" && run?.outputs ? (
          <Panel title="✅ Publiée" width="40%">
            <text wrapMode="char">📄 {run.outputs.report ?? run.outputs.markdown}</text>
            {run.outputs.notion ? <text fg={C.link} wrapMode="char">📝 {run.outputs.notion}</text> : null}
          </Panel>
        ) : null}
      </box>
    </Screen>
  )
}

export { truncate }
