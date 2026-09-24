/** 💬 Chat : questions sourcées en streaming (outils, sources numérotées, parcours du graphe). */
import { useKeyboard } from "@opentui/react"
import { useEffect, useRef, useState } from "react"
import { useActive, useApp } from "../context"
import { Screen, Panel } from "../components/screen"
import { Messages } from "../components/messages"
import { applyChatEvent, type ChatMessage } from "../lib/chat"
import { submit } from "../components/ui"
import { C } from "../theme"

const FALLBACK = [
  "Quoi de neuf dans les dernières veilles ?",
  "C'est quoi le KV cache ?",
  "Trouve des nouveaux repos GitHub sur les agents MCP",
  "Que disent mes sources sur la quantization et l'inférence locale ?",
  "Qu'as-tu en mémoire ?",
]

export function ChatScreen() {
  const { api, toast, consume } = useApp()
  const { active, nav, edit } = useActive("chat")
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [conversation, setConversation] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [input, setInput] = useState("")
  const [suggestions, setSuggestions] = useState<string[]>(FALLBACK)
  const busyRef = useRef(false)

  useEffect(() => {
    api.get("/api/knowledge").then((kb: any) => {
      const prompts = kb.entries.filter((e: any) => e.kind === "prompts" && e.target !== "review" && !/\{/.test(e.body))
      if (prompts.length) setSuggestions(prompts.map((e: any) => e.body.trim()))
    }).catch(() => {})
  }, [api])

  const send = async (text: string) => {
    const message = text.trim()
    if (!message || busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setInput("")
    setMessages((m) => [...m, { role: "user", text: message }, { role: "assistant", text: "", streaming: true }])
    try {
      await api.stream("/api/chat", { message, conversation_id: conversation }, (event) => {
        if (event.type === "conversation") setConversation(event.id)
        else setMessages((m) => applyChatEvent(m, event))
      })
    } catch (error: any) {
      setMessages((m) => applyChatEvent(m, { type: "error", text: error.message }))
    }
    setMessages((m) => (m.at(-1)?.streaming ? applyChatEvent(m, { type: "final", text: m.at(-1)!.text }) : m))
    busyRef.current = false
    setBusy(false)
  }

  useEffect(() => {
    if (!active) return
    const pending = consume("chat")
    if (pending) send(pending)
  })

  useKeyboard((key) => {
    if (nav && key.name === "n") {
      setMessages([])
      setConversation(null)
      toast("Nouvelle conversation")
    }
  })

  return (
    <Screen id="chat" keys={edit ? [["Entrée", "envoyer"], ["Échap", "navigation"]] : [["i", "écrire"], ["n", "nouvelle conversation"], ["↑↓ Entrée", "suggestion"], ["1-0", "écrans"]]}>
      <Panel title={`💬 Conversation${conversation ? ` · ${conversation.slice(0, 8)}` : ""}`}>
        {messages.length ? (
          <scrollbox flexGrow={1} stickyScroll stickyStart="bottom" focused={nav}>
            <Messages messages={messages} />
          </scrollbox>
        ) : (
          <box flexDirection="column" flexGrow={1}>
            <text fg={C.muted}>Questions sourcées sur ta veille : chaque réponse cite ses sources [n] et montre les agents engagés.</text>
            <text fg={C.muted}>Suggestions (Échap puis ↑↓ Entrée) :</text>
            <select focused={nav} flexGrow={1} options={suggestions.map((s) => ({ name: `⚡ ${s}`, description: "", value: s }))}
              showDescription={false} backgroundColor={C.bg} focusedBackgroundColor={C.bg} selectedBackgroundColor={C.panel2}
              selectedTextColor={C.accent} onSelect={(_, option) => option && send(option.value)} />
          </box>
        )}
      </Panel>
      <box border borderStyle="rounded" borderColor={edit ? C.accent : C.line} height={3} title={busy ? " ⏳ réponse en cours… " : " ✎ Message "}>
        <input focused={edit} value={input} onInput={setInput} onSubmit={submit((value: string) => send(value))}
          placeholder="Demandez à votre veille… (Entrée pour envoyer)" />
      </box>
    </Screen>
  )
}

