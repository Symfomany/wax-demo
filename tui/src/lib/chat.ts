/** État d'une conversation alimenté par les événements SSE de /api/chat. */
export interface ChatSource {
  n: number
  title: string
  url: string
  source: string
  date?: string
}

export interface ChatMessage {
  role: "user" | "assistant"
  text: string
  tool?: string
  toolSummary?: string
  toolDone?: boolean
  sources?: ChatSource[]
  warnings?: string[]
  engaged?: { nodes?: string[]; tool?: string | null; agents?: string[]; skills?: string[]; mcp?: string[] }
  totalMs?: number
  streaming?: boolean
  error?: string
}

export const TOOL_LABELS: Record<string, string> = {
  search_watch: "🔎 Recherche dans la veille",
  latest_digests: "🗞️ Dernières veilles",
  run_watch: "🛰️ Lancement de veille",
  github_search: "🐙 GitHub via MCP",
  memory_status: "🧠 Mémoire",
  notion_sync: "📝 Notion via MCP",
  list_reports: "📰 Rapports",
  grill_me: "🎯 Grill-me",
  knowledge_search: "📚 Base de connaissances",
  challenge_review: "🔬 Challenge de la review",
}

/** Applique un événement au dernier message (assistant) ; renvoie une nouvelle liste. */
export function applyChatEvent(messages: ChatMessage[], event: any): ChatMessage[] {
  const next = [...messages]
  const last = { ...next[next.length - 1] }
  switch (event.type) {
    case "tool_start":
      last.tool = event.tool
      break
    case "tool_end":
      last.toolDone = true
      last.toolSummary = event.summary
      break
    case "token":
      last.text += event.text
      break
    case "final":
      Object.assign(last, {
        text: event.text, sources: event.sources ?? [], warnings: event.warnings ?? [], engaged: event.engaged,
        totalMs: event.total_ms, streaming: false, toolDone: true,
      })
      break
    case "error":
      Object.assign(last, { error: event.text, streaming: false })
      break
    default:
      return messages
  }
  next[next.length - 1] = last
  return next
}
