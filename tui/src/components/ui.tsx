/** Briques visuelles : spinner, barres de progression, badges, étapes, aide clavier, Markdown. */
import { useEffect, useState, type ReactNode } from "react"
import { C } from "../theme"
import { SPINNER, bar } from "../lib/format"

export function Spinner({ label, color = C.accent }: { label?: string; color?: string }) {
  const [frame, setFrame] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setFrame((f) => (f + 1) % SPINNER.length), 80)
    return () => clearInterval(timer)
  }, [])
  return (
    <text height={1}>
      <span fg={color}>{SPINNER[frame]}</span> {label ?? ""}
    </text>
  )
}

export function ProgressBar({ value, max, width = 24, label, color = C.accent }:
  { value: number; max: number; width?: number; label?: string; color?: string }) {
  const full = bar(value, max, width)
  const filled = full.replace(/░/g, "")
  return (
    <text height={1}>
      {label ? <span fg={C.muted}>{label} </span> : null}
      <span fg={color}>{filled}</span>
      <span fg={C.line}>{full.slice(filled.length)}</span>
      <span fg={C.muted}> {Math.round((100 * Math.min(value, max)) / Math.max(1, max))}%</span>
    </text>
  )
}

/** Score 0-10 : « Pertinence ████████░░ 8/10 ». */
export function Score({ label, value }: { label: string; value: number }) {
  const color = value >= 7 ? C.ok : value >= 4 ? C.warn : C.bad
  const full = bar(value, 10, 10)
  const filled = full.replace(/░/g, "")
  return (
    <text height={1}>
      <span fg={C.muted}>{label.padEnd(11)}</span>
      <span fg={color}>{filled}</span>
      <span fg={C.line}>{full.slice(filled.length)}</span>
      <span fg={color}> {value}/10</span>
    </text>
  )
}

/** Étapes horizontales : ✅ Collecte ─ ⠋ Scout ─ ○ Critic… */
export function Steps({ steps, current, running }: { steps: string[]; current: number; running: boolean }) {
  return (
    <text height={1}>
      {steps.map((step, index) => {
        const done = index < current
        const active = index === current && running
        const icon = done ? "✅" : active ? "⏳" : "○"
        return (
          <span key={step} fg={done ? C.ok : active ? C.accent : C.dim}>
            {index ? " ─ " : ""}
            {icon} {step}
          </span>
        )
      })}
    </text>
  )
}

export function Badge({ children, color = C.accent }: { children: ReactNode; color?: string }) {
  return (
    <span fg={color}>
      [{children}]
    </span>
  )
}

export function Keys({ items }: { items: [string, string][] }) {
  return (
    <text height={1}>
      {items.map(([key, label], index) => (
        <span key={key}>
          {index ? <span fg={C.dim}>  </span> : null}
          <span fg={C.accent}>{key}</span>
          <span fg={C.muted}> {label}</span>
        </span>
      ))}
    </text>
  )
}

export function Title({ icon, children, hint }: { icon: string; children: ReactNode; hint?: string }) {
  return (
    <text height={1}>
      <strong fg={C.text}>
        {icon} {children}
      </strong>
      {hint ? <span fg={C.muted}>  {hint}</span> : null}
    </text>
  )
}

/** Markdown de terminal : titres, listes, citations, **gras**, `code`, [liens](url). */
export function Md({ content, maxLines }: { content: string; maxLines?: number }) {
  let lines = stripFrontMatter(String(content ?? "")).split("\n")
  if (maxLines && lines.length > maxLines) lines = [...lines.slice(0, maxLines), "…"]
  return (
    <box flexDirection="column">
      {lines.map((raw, index) => {
        const line = raw.trimEnd()
        let m: RegExpMatchArray | null
        if ((m = line.match(/^(#{1,4})\s+(.*)/)))
          return (
            <text key={index} wrapMode="word">
              <strong fg={m[1].length <= 2 ? C.accent : C.text}>{inline(m[2])}</strong>
            </text>
          )
        if ((m = line.match(/^\s*[-*]\s+(.*)/)))
          return (
            <text key={index} wrapMode="word">
              <span fg={C.accent}>  • </span>
              {inline(m[1])}
            </text>
          )
        if ((m = line.match(/^\s*(\d+)[.)]\s+(.*)/)))
          return (
            <text key={index} wrapMode="word">
              <span fg={C.accent}>  {m[1]}. </span>
              {inline(m[2])}
            </text>
          )
        if (/^\s*(-{3,}|\*{3,})\s*$/.test(line))
          return <text key={index} height={1} fg={C.line}>{"─".repeat(60)}</text>
        if ((m = line.match(/^>\s?(.*)/)))
          return (
            <text key={index} wrapMode="word" fg={C.muted}>
              <span fg={C.accent}>▎ </span>
              {inline(m[1])}
            </text>
          )
        return (
          <text key={index} wrapMode="word">
            {line ? inline(line) : " "}
          </text>
        )
      })}
    </box>
  )
}

/** Segments en ligne : **gras**, `code`, [texte](url) et citations [1]. */
export function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = []
  const pattern = /\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?:[^)\s]+)\)|\[(\d+(?:,\s*\d+)*)\]|(?<![\w*])[*_]([^*_\n]+)[*_](?![\w*])/g
  let last = 0
  let m: RegExpExecArray | null
  let key = 0
  while ((m = pattern.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    if (m[1]) parts.push(<strong key={key++}>{inline(m[1])}</strong>)
    else if (m[2]) parts.push(<span key={key++} fg={C.code}>{m[2]}</span>)
    else if (m[3]) parts.push(<span key={key++} fg={C.link}><u>{m[3]}</u><span fg={C.dim}> ↗ {domain(m[4])}</span></span>)
    else if (m[5]) parts.push(<span key={key++} fg={C.accent}>[{m[5]}]</span>)
    else if (m[6]) parts.push(<em key={key++}>{inline(m[6])}</em>)
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

/** `onSubmit` d'un <input> : OpenTUI le type comme l'intersection textarea/input ; il reçoit la valeur (string). */
export function submit(handler: (value: string) => unknown): any {
  return (value: unknown) => void handler(typeof value === "string" ? value : "")
}

/** Retire l'en-tête YAML (--- … ---) d'un document Markdown. */
export function stripFrontMatter(text: string): string {
  const match = text.match(/^---\n[\s\S]*?\n---\n?/)
  return match ? text.slice(match[0].length).replace(/^\n+/, "") : text
}

function domain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "")
  } catch {
    return ""
  }
}
