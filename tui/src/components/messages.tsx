/** Affichage des messages du chat (partagé par le Chat et le challenge d'une review). */
import { C } from "../theme"
import { Md, Spinner } from "./ui"
import { TOOL_LABELS, type ChatMessage } from "../lib/chat"
import { sourceIcon, truncate } from "../lib/format"

export function Messages({ messages }: { messages: ChatMessage[] }) {
  return (
    <box flexDirection="column">
      {messages.map((m, index) =>
        m.role === "user" ? (
          <box key={index} flexDirection="column" marginTop={1}>
            <text>
              <strong fg={C.warn}>🧑 Vous</strong>
            </text>
            <text wrapMode="word" fg={C.text}>{m.text}</text>
          </box>
        ) : (
          <box key={index} flexDirection="column" marginTop={1}>
            <text>
              <strong fg={C.accent}>✦ Veille</strong>
              {m.tool ? (
                <span fg={C.muted}>
                  {"  "}{m.toolDone ? "✓" : "…"} {TOOL_LABELS[m.tool] ?? m.tool}
                  {m.toolSummary ? ` · ${m.toolSummary}` : ""}
                </span>
              ) : null}
            </text>
            {m.streaming && !m.text ? <Spinner label={m.tool ? "rédaction de la réponse…" : "routage…"} /> : null}
            {m.text ? <Md content={m.text + (m.streaming ? " ▍" : "")} /> : null}
            {m.error ? <text fg={C.bad}>⚠ {m.error}</text> : null}
            {m.sources?.map((s) => (
              <text key={s.n} wrapMode="word">
                <span fg={C.accent}>  [{s.n}] </span>
                <span>{sourceIcon(s.source)} {truncate(s.title, 70)}</span>
                <span fg={C.dim}>  {s.url.startsWith("#k=") ? `📚 ${s.url.slice(3)}` : s.url}{s.date ? ` · ${s.date}` : ""}</span>
              </text>
            ))}
            {m.warnings?.length ? <text fg={C.warn}>  🛡️ Guard : {m.warnings.join(" · ")}</text> : null}
            {m.engaged && !m.streaming ? (
              <text fg={C.dim}>
                {"  "}🤖 {m.engaged.nodes?.join(" → ")}
                {m.engaged.tool ? ` · 🛠 ${m.engaged.tool}` : ""}
                {m.engaged.agents?.length ? ` · 👥 ${m.engaged.agents.join(", ")}` : ""}
                {m.engaged.skills?.length ? ` · 📚 ${m.engaged.skills.join(", ")}` : ""}
                {m.engaged.mcp?.length ? ` · 🔌 ${m.engaged.mcp.join(", ")}` : ""}
                {m.totalMs ? ` · ⏱ ${(m.totalMs / 1000).toFixed(1)} s` : ""}
              </text>
            ) : null}
          </box>
        ),
      )}
    </box>
  )
}
