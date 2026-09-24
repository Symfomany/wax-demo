/** 🏠 Accueil : état des services, mémoire, dernière veille, raccourcis. */
import { useEffect, useState } from "react"
import { useActive, useApp, SCREENS } from "../context"
import { Screen, Panel } from "../components/screen"
import { Md, ProgressBar, Spinner, Steps } from "../components/ui"
import { RUN_STAGES, RUN_STATES, dateTime, runStage } from "../lib/format"
import { C } from "../theme"

const SHORTCUTS: { name: string; description: string; value: string }[] = [
  { name: "💬 Poser une question à la veille", description: "Chat sourcé : recherche, digests, GitHub via MCP, glossaire", value: "chat" },
  { name: "🔬 Faire la review d'une URL", description: "Synthèse selon les règles métiers, citations vérifiées, challenge", value: "review" },
  { name: "🛰️ Lancer une veille", description: "Collecte → Scout → Critic → Editor → validation humaine", value: "watch" },
  { name: "🧭 Ajouter une source par URL", description: "Blog, flux RSS, dépôt GitHub ou catégorie arXiv (vérifiée)", value: "sources" },
  { name: "📚 Explorer la base de connaissances", description: "Glossaire, index, règles métiers, prompts", value: "knowledge" },
  { name: "🎯 Préciser ma veille (Grill-me)", description: "Entretien pour cerner tes centres d'intérêt", value: "grill" },
]

export function HomeScreen() {
  const { api, go, health } = useApp()
  const { active, nav } = useActive("home")
  const [runs, setRuns] = useState<any[]>([])
  const [report, setReport] = useState<string>("")
  const [counts, setCounts] = useState<{ reviews: number; knowledge: number } | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!active) return
    setLoading(true)
    Promise.allSettled([
      api.get("/api/runs").then(setRuns),
      api.get("/api/reports").then(async (list: any[]) => {
        if (list[0]) setReport((await api.get(`/api/reports/${list[0].name}`)).markdown)
      }),
      Promise.all([api.get("/api/reviews"), api.get("/api/knowledge")]).then(([reviews, kb]: any[]) =>
        setCounts({ reviews: reviews.length, knowledge: kb.entries.length })),
    ]).finally(() => setLoading(false))
  }, [active, api])

  const run = runs[0]
  const memory = health?.memory ?? {}
  const synthesis = report

  return (
    <Screen id="home" keys={[["↑↓ Entrée", "ouvrir"], ["1-0", "écrans"], ["?", "aide"], ["q", "quitter"]]}>
      <box flexDirection="row" height={6}>
        <box flexDirection="column" width={46}>
          <ascii-font text="VEILLE" font="tiny" color={C.accent} />
          <text fg={C.muted}>LLM Watch Harness · veille LLM/GenAI sourcée</text>
        </box>
        <box flexDirection="column" flexGrow={1}>
          <text>
            <span fg={health?.ollama && health.model_available ? C.ok : C.bad}>● </span>
            <span fg={C.text}>LLM </span>
            <span fg={C.muted}>{health ? `${health.model}${health.model_available ? "" : " (absent)"}` : "serveur injoignable"}</span>
          </text>
          <text>
            <span fg={health?.notion ? C.ok : C.dim}>● </span><span fg={C.text}>Notion  </span>
            <span fg={health?.tracing?.langfuse ? C.ok : C.dim}>● </span><span fg={C.text}>Langfuse  </span>
            <span fg={health?.tracing?.langsmith ? C.ok : C.dim}>● </span><span fg={C.text}>LangSmith</span>
          </text>
          <text fg={C.muted}>
            📄 {memory.documents ?? "–"} documents · 🗞️ {memory.digests ?? "–"} digests · ✅ {memory.published_items ?? "–"} publiés
          </text>
          <text fg={C.muted}>
            🔬 {counts?.reviews ?? "–"} reviews · 📚 {counts?.knowledge ?? "–"} connaissances · 💬 {memory.conversations ?? "–"} conversations
          </text>
          {loading ? <Spinner label="chargement…" /> : null}
        </box>
      </box>
      <box flexDirection="row" flexGrow={1}>
        <box flexDirection="column" width="45%">
          <Panel title="⚡ Raccourcis" focused={nav}>
            <select focused={nav} options={SHORTCUTS} flexGrow={1} backgroundColor={C.bg} focusedBackgroundColor={C.bg}
              selectedBackgroundColor={C.panel2} selectedTextColor={C.accent} descriptionColor={C.dim}
              onSelect={(_, option) => option && go(option.value)} />
          </Panel>
          <Panel title="🛰️ Dernière veille" height={7}>
            {run ? (
              <box flexDirection="column">
                <text>
                  {RUN_STATES[run.status]?.[0] ?? "●"} <strong>{RUN_STATES[run.status]?.[1] ?? run.status}</strong>
                  <span fg={C.dim}>  {run.id.slice(0, 8)} · {dateTime(run.started_at)}</span>
                </text>
                <ProgressBar value={runStage([], run.status)} max={RUN_STAGES.length} width={26} />
                <Steps steps={RUN_STAGES} current={runStage([], run.status)} running={run.status === "running"} />
              </box>
            ) : (
              <text fg={C.muted}>Aucune veille dans cette session du serveur. Écran 5 pour en lancer une.</text>
            )}
          </Panel>
        </box>
        <Panel title="🗞️ Dernier rapport">
          <scrollbox flexGrow={1}>
            {synthesis ? <Md content={synthesis} maxLines={36} /> : <text fg={C.muted}>Aucun rapport publié.</text>}
          </scrollbox>
        </Panel>
      </box>
    </Screen>
  )
}

export { SCREENS }
