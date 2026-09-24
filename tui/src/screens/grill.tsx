/** 🎯 Grill-me : entretien, une question à la fois avec recommandation, puis profil de veille. */
import { useKeyboard } from "@opentui/react"
import { useEffect, useState } from "react"
import { useActive, useApp } from "../context"
import { Screen, Panel } from "../components/screen"
import { ProgressBar, Spinner, submit } from "../components/ui"
import { C } from "../theme"

export function GrillScreen() {
  const { api, toast, setMode, pending, go } = useApp()
  const { active, nav, edit } = useActive("grill")
  const [session, setSession] = useState<string | null>(null)
  const [question, setQuestion] = useState<any | null>(null)
  const [profile, setProfile] = useState<any | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [cursor, setCursor] = useState(0)
  const [text, setText] = useState("")
  const [loading, setLoading] = useState(false)
  const [log, setLog] = useState<string[]>([])

  useEffect(() => {
    if (active && !session && !profile) api.get("/api/grill/profile").then((p) => p?.summary && setProfile(p)).catch(() => {})
  }, [active])

  const step = (result: any) => {
    setPicked(new Set())
    setCursor(0)
    setText("")
    if (result.question) {
      setQuestion(result.question)
      setProfile(null)
    } else {
      setQuestion(null)
      setProfile(result.profile)
      setSession(null)
      toast("🎯 Profil enregistré dans la mémoire des agents", "ok")
    }
  }
  const start = async () => {
    setLoading(true)
    setLog([])
    try {
      const result = await api.post("/api/grill")
      setSession(result.session)
      step(result)
    } catch (e: any) {
      toast(e.message, "error")
    }
    setLoading(false)
  }
  const answer = async (payload: any, label: string) => {
    if (!session || !question) return
    setLoading(true)
    setLog((l) => [...l, `${question.text} → ${label}`])
    setMode("nav")
    try {
      step(await api.post(`/api/grill/${session}/answer`, payload))
    } catch (e: any) {
      toast(e.message, "error")
    }
    setLoading(false)
  }
  const labels = (ids: string[]) => question.options.filter((o: any) => ids.includes(o.id)).map((o: any) => o.label).join(", ")

  useKeyboard((key) => {
    if (!nav || loading) return
    if (!question) {
      if (key.name === "return" || key.name === "s") start()
      if (profile && key.name === "l") {
        pending.watch = (profile.keywords ?? []).slice(0, 5).join(", ")
        go("watch")
      }
      return
    }
    const options = question.options
    if (key.name === "down") setCursor((c) => Math.min(options.length - 1, c + 1))
    if (key.name === "up") setCursor((c) => Math.max(0, c - 1))
    if (key.name === "space" && options[cursor]) {
      const id = options[cursor].id
      setPicked((p) => {
        const next = new Set(question.multi ? p : [])
        if (p.has(id)) next.delete(id)
        else next.add(id)
        return next
      })
    }
    if (key.name === "return") answer({ options: [...picked], text }, [labels([...picked]), text].filter(Boolean).join(" · ") || "(passé)")
    if (key.name === "r" && question.recommended.length) answer({ recommended: true }, `✓ ${labels(question.recommended)}`)
    if (key.name === "k") answer({ options: [], text: "" }, "(passé)")
  })

  const total = question ? question.progress.index + question.progress.remaining : 1
  return (
    <Screen id="grill" keys={edit ? [["Entrée", "répondre"], ["Échap", "options"]]
      : question ? [["↑↓", "option"], ["Espace", "cocher"], ["/", "texte libre"], ["Entrée", "répondre"], ["r", "ma reco"], ["k", "passer"]]
        : [["Entrée", profile ? "refaire l'entretien" : "commencer"], ...(profile ? [["l", "veille ciblée"]] as [string, string][] : [])]}>
      <box height={1}>{loading ? <Spinner label="l'agent Grill-me réfléchit…" /> : null}</box>
      {question ? (
        <box flexDirection="column" flexGrow={1}>
          <text>
            <span fg={C.bg} bg={C.accent}> {question.branch} </span>
            <span fg={C.muted}>  Question {question.progress.index}
              {question.id === "domains" ? " · les suivantes dépendent de tes domaines"
                : question.progress.remaining ? ` · encore ~${question.progress.remaining}` : " · dernière"}</span>
          </text>
          <ProgressBar value={question.progress.index - 1} max={total} width={40} />
          <text> </text>
          <text wrapMode="word"><strong fg={C.text}>{question.text}</strong></text>
          <text fg={C.muted} wrapMode="word">💭 Pourquoi je te demande ça : {question.why}</text>
          <text> </text>
          {question.options.map((o: any, i: number) => (
            <text key={o.id}>
              <span fg={i === cursor ? C.accent : C.dim}>{i === cursor ? " ▶ " : "   "}</span>
              <span fg={picked.has(o.id) ? C.ok : C.text}>{question.multi ? (picked.has(o.id) ? "☑ " : "☐ ") : (picked.has(o.id) ? "◉ " : "○ ")}{o.label}</span>
              {question.recommended.includes(o.id) ? <span fg={C.ok}>  ★ recommandé</span> : null}
            </text>
          ))}
          <box border borderStyle="rounded" borderColor={edit ? C.accent : C.line} height={3} marginTop={1} title=" ✎ Texte libre (facultatif) ">
            <input focused={edit} value={text} onInput={setText} placeholder={question.hint || "Précise si besoin"}
              onSubmit={submit((value: string) => answer({ options: [...picked], text: value }, [labels([...picked]), value].filter(Boolean).join(" · ") || "(passé)"))} />
          </box>
          {log.length ? (
            <Panel title="📜 Réponses">
              {log.slice(-6).map((l, i) => <text key={i} fg={C.muted} wrapMode="word">• {l}</text>)}
            </Panel>
          ) : null}
        </box>
      ) : (
        <Panel title={profile ? "🎯 Ton profil de veille" : "🎯 Grill-me"}>
          {profile ? (
            <box flexDirection="column">
              <text wrapMode="word">{profile.summary}</text>
              <text> </text>
              <text fg={C.accent} wrapMode="word">⭐ Priorités : {(profile.priorities ?? []).join(", ")}</text>
              <text fg={C.accent} wrapMode="word">🔑 Mots-clés : {(profile.keywords ?? []).join(", ")}</text>
              <text fg={C.bad} wrapMode="word">🚫 À écarter : {(profile.exclusions ?? []).join(", ") || "—"}</text>
              <text fg={C.muted}>⏱ {profile.frequency ?? ""} · {profile.depth ?? ""}</text>
              <text> </text>
              <text fg={C.muted}>Entrée : refaire l'entretien · l : lancer une veille ciblée sur ces mots-clés</text>
            </box>
          ) : (
            <box flexDirection="column">
              <text wrapMode="word">Je t'interroge, une question à la fois et avec ma recommandation, pour cerner ce que tu cherches en actu IA :</text>
              <text wrapMode="word" fg={C.muted}>LLM, nouveaux modèles, robotique, agents, architecture, recherche, événements… Ton profil guide ensuite le Scout, l'Editor et les veilles ciblées.</text>
              <text> </text>
              <text fg={C.accent}>Entrée pour commencer.</text>
            </box>
          )}
        </Panel>
      )}
    </Screen>
  )
}
