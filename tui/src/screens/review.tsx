/** 🔬 Review d'une actu par URL : étapes en direct, synthèse vérifiée, challenge par chat, révision. */
import { useKeyboard } from "@opentui/react"
import { useEffect, useRef, useState } from "react"
import { useActive, useApp } from "../context"
import { Screen, Panel } from "../components/screen"
import { Messages } from "../components/messages"
import { Md, ProgressBar, Score, Spinner, submit } from "../components/ui"
import { applyChatEvent, type ChatMessage } from "../lib/chat"
import { REVIEW_LABELS, REVIEW_STEPS, VERDICTS, truncate } from "../lib/format"
import { C } from "../theme"

type Field = "url" | "challenge"

export function ReviewScreen() {
  const { api, toast, setMode, consume, height } = useApp()
  const { active, nav, edit } = useActive("review")
  const [list, setList] = useState<any[]>([])
  const [review, setReview] = useState<any | null>(null)
  const [field, setField] = useState<Field>("url")
  const [url, setUrl] = useState("")
  const [question, setQuestion] = useState("")
  const [steps, setSteps] = useState<{ node: string; detail: string; ms: number }[]>([])
  const [running, setRunning] = useState(false)
  const [error, setError] = useState("")
  const [pane, setPane] = useState<"list" | "detail" | "challenge">("list")
  const [chat, setChat] = useState<ChatMessage[]>([])
  const busy = useRef(false)

  const loadList = () => api.get("/api/reviews").then(setList).catch(() => {})
  const open = async (id: string) => {
    try {
      const record = await api.get(`/api/reviews/${id}`)
      setReview(record)
      const history = await api.get(`/api/conversations/${record.conversation_id}`).catch(() => [])
      setChat(history.map((m: any) => ({ role: m.role === "human" ? "user" : "assistant", text: m.content, sources: m.sources, engaged: m.engaged })))
    } catch (e: any) {
      toast(e.message, "error")
    }
  }
  useEffect(() => {
    if (!active) return
    loadList()
    const prefill = consume("review")
    if (prefill) {
      setField("challenge")
      setQuestion(prefill)
      setMode("edit")
    }
  }, [active])
  useEffect(() => {
    if (!review && list[0]) open(list[0].id)
  }, [list])

  const run = async (path: string, body: unknown) => {
    if (busy.current) return
    busy.current = true
    setRunning(true)
    setSteps([])
    setError("")
    setMode("nav")
    try {
      await api.stream(path, body, (event) => {
        if (event.type === "step") setSteps((s) => [...s, event])
        if (event.type === "review") {
          setReview({ ...event.review, trace_url: event.trace_url })
          if (event.review.revision === 1) setChat([])
          toast(event.review.revision > 1 ? `♻️ Review révisée (révision ${event.review.revision})` : "🔬 Review prête : challengez-la (c)", "ok")
          loadList()
        }
        if (event.type === "error") setError(event.text)
      })
    } catch (e: any) {
      setError(e.message)
    }
    busy.current = false
    setRunning(false)
  }

  const challenge = async (text: string) => {
    if (!review || !text.trim() || busy.current) return
    busy.current = true
    setQuestion("")
    setMode("nav")
    setPane("challenge")
    setChat((c) => [...c, { role: "user", text: text.trim() }, { role: "assistant", text: "", streaming: true }])
    try {
      await api.stream("/api/chat", { message: text.trim(), review_id: review.id }, (event) => {
        if (event.type !== "conversation") setChat((c) => applyChatEvent(c, event))
      })
    } catch (e: any) {
      setChat((c) => applyChatEvent(c, { type: "error", text: e.message }))
    }
    busy.current = false
  }

  useKeyboard((key) => {
    if (!nav) return
    if (key.name === "u") { setField("url"); setMode("edit") }
    if (key.name === "c" && review) { setField("challenge"); setMode("edit") }
    if (key.name === "r" && review) run(`/api/reviews/${review.id}/revise`, null)
    if (key.name === "tab") setPane((p) => (p === "list" ? "detail" : p === "detail" && chat.length ? "challenge" : "list"))
  })

  const a = review?.analysis
  const p = review?.page
  const current = steps.length
  return (
    <Screen id="review" keys={edit ? [["Entrée", field === "url" ? "analyser" : "challenger"], ["Échap", "navigation"]]
      : [["u", "URL"], ["c", "challenger"], ["r", "réviser"], ["Tab", "liste/review/challenge"], ["↑↓", "parcourir"]]}>
      <box border borderStyle="rounded" borderColor={edit ? C.accent : C.line} height={3}
        title={field === "url" ? " 🔗 URL de l'actualité (article, release notes, arXiv…) " : ` 💬 Challenger « ${truncate(review?.title ?? "", 40)} » `}>
        {field === "url" ? (
          <input focused={edit} value={url} onInput={setUrl} placeholder="https://…"
            onSubmit={submit((value: string) => value.trim() && run("/api/reviews", { url: value.trim() }))} />
        ) : (
          <input focused={edit} value={question} onInput={setQuestion} onSubmit={submit(challenge)}
            placeholder="Ex. « Le gain annoncé est-il mesuré avec un protocole ? »" />
        )}
      </box>
      {running || steps.length || error ? (
        // Hauteur explicite : le bloc grandit après le premier rendu (sinon chevauchement).
        <box flexDirection="column" marginBottom={1}
          height={1 + steps.length + (running && current < REVIEW_STEPS.length ? 1 : 0) + (error ? 2 : 0)}>
          <ProgressBar value={current} max={REVIEW_STEPS.length} width={30} label="Reviewer" color={error ? C.bad : C.accent} />
          {steps.map((s) => (
            <text key={s.node} height={1}>
              <span fg={C.ok}>  ✅ {REVIEW_LABELS[s.node] ?? s.node}</span>
              <span fg={C.dim}> — {s.detail} · {(s.ms / 1000).toFixed(1)} s</span>
            </text>
          ))}
          {running && current < REVIEW_STEPS.length ? <Spinner label={REVIEW_LABELS[REVIEW_STEPS[current]] + "…"} /> : null}
          {error ? <text fg={C.bad} wrapMode="word" height={2}>  ❌ {error}</text> : null}
        </box>
      ) : null}
      <box flexDirection="row" flexGrow={1}>
        <Panel title={`🗂️ Reviews (${list.length})`} width={36} focused={nav && pane === "list"}>
          {list.length ? (
            <select focused={nav && pane === "list"} flexGrow={1} backgroundColor={C.bg} focusedBackgroundColor={C.bg}
              selectedBackgroundColor={C.panel2} selectedTextColor={C.accent} descriptionColor={C.dim}
              options={list.map((r) => ({ name: truncate(r.title, 30), description: `${r.site} · ${r.relevance}/10${r.revision > 1 ? ` · rév. ${r.revision}` : ""}`, value: r.id }))}
              onChange={(_, option) => option && open(option.value)} />
          ) : <text fg={C.muted}>Aucune review : tapez u puis une URL.</text>}
        </Panel>
        <box flexDirection="column" flexGrow={1}>
        <Panel title={review ? `🔬 ${truncate(review.title, 60)}` : "🔬 Review"} focused={nav && pane === "detail"}>
          {review ? (
            // Remonté à chaque review/révision : l'affichage repart du haut.
            <scrollbox key={`${review.id}-${review.revision}`} flexGrow={1} focused={nav && pane === "detail"}>
              <text fg={C.muted}>
                {p.site} · {p.published_at ?? "date non précisée"} · {p.word_count} mots · révision {review.revision} · {review.model}
              </text>
              <text fg={C.link}>{p.final_url}</text>
              <text>
                {(p.domains.length ? p.domains : ["domaine non détecté"]).map((d: string) => <span key={d} fg={C.accent}>[{d}] </span>)}
                <span fg={a.source_type === "primaire" ? C.ok : C.warn}>source {a.source_type}</span>
              </text>
              <Score label="Pertinence" value={a.relevance} />
              <Score label="Nouveauté" value={a.novelty} />
              <Score label="Confiance" value={a.confidence} />
              <text> </text>
              <text><strong fg={C.accent}>📝 Synthèse</strong></text>
              <Md content={a.summary} />
              <text wrapMode="word"><strong>💡 Pourquoi c'est important : </strong>{a.why_it_matters}</text>
              <text> </text>
              <text>
                <strong fg={C.accent}>🔎 Affirmations </strong>
                <span fg={C.muted}>({a.claims.filter((c: any) => c.status === "etaye").length}/{a.claims.length} étayées par la page)</span>
              </text>
              {a.claims.map((c: any, i: number) => (
                <box key={i} flexDirection="column">
                  <text wrapMode="word">
                    <span fg={c.status === "etaye" ? C.ok : C.warn}>{c.status === "etaye" ? "  ✅ " : "  ⚠️ "}</span>
                    <span fg={C.dim}>[{c.kind}] </span>{c.claim}
                  </text>
                  {c.quote ? <text fg={C.muted} wrapMode="word">     « {c.quote} »</text> : null}
                </box>
              ))}
              <text> </text>
              <text><strong fg={C.accent}>📏 Règles métiers</strong></text>
              {a.rule_checks.map((c: any) => (
                <text key={c.rule_id} wrapMode="word">
                  <span>  {VERDICTS[c.verdict][0]} </span>
                  <span fg={C.accent}>R{c.rule_id} </span>
                  <span fg={C.dim}>({c.domain}) </span>{c.rule}
                  {c.note ? <span fg={C.muted}> — {c.note}</span> : null}
                </text>
              ))}
              {a.risks.length ? <text> </text> : null}
              {a.risks.length ? <text><strong fg={C.warn}>⚠️ Risques</strong></text> : null}
              {a.risks.map((r: string, i: number) => <text key={i} wrapMode="word">  • {r}</text>)}
              {a.questions.length ? <text> </text> : null}
              {a.questions.length ? <text><strong fg={C.accent}>❓ Questions pour challenger (c)</strong></text> : null}
              {a.questions.map((q: string, i: number) => <text key={i} wrapMode="word" fg={C.muted}>  • {q}</text>)}
              {review.warnings?.length ? <text fg={C.warn} wrapMode="word">🛡️ Guard : {review.warnings.join(" · ")}</text> : null}
            </scrollbox>
          ) : <text fg={C.muted}>Tapez u, collez une URL, Entrée : téléchargement → Reviewer → guard → enregistrement.</text>}
        </Panel>
        {review && (chat.length || field === "challenge") ? (
          <Panel title="💬 Challenge — c pour poser une question · r pour réviser la synthèse avec ce débat" height={Math.max(10, Math.floor(height * 0.4))}
            focused={nav && pane === "challenge"}>
            <scrollbox flexGrow={1} stickyScroll stickyStart="bottom" focused={nav && pane === "challenge"}>
              {chat.length ? <Messages messages={chat} /> : <text fg={C.muted}>Posez une question : les réponses s'appuient sur les extraits de l'article [1].</text>}
            </scrollbox>
          </Panel>
        ) : null}
        </box>
      </box>
    </Screen>
  )
}
