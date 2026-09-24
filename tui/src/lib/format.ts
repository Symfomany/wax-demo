/** Fonctions pures d'affichage : barres, spinners, dates, étapes de veille. */

export const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

/** Barre de progression texte : ████████░░░░ (fraction arrondie à la cellule). */
export function bar(value: number, max: number, width = 20): string {
  const ratio = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0
  const full = Math.round(ratio * width)
  return "█".repeat(full) + "░".repeat(width - full)
}

export function percent(value: number, max: number): string {
  return `${Math.round(max > 0 ? (100 * Math.min(value, max)) / max : 0)}%`
}

export function truncate(text: string, width: number): string {
  const clean = String(text ?? "").replace(/\s+/g, " ").trim()
  return clean.length > width ? clean.slice(0, Math.max(0, width - 1)) + "…" : clean
}

/** Découpe un texte en lignes de `width` colonnes (mots entiers si possible). */
export function wrap(text: string, width: number): string[] {
  const lines: string[] = []
  for (const paragraph of String(text ?? "").split("\n")) {
    let line = ""
    for (const word of paragraph.split(/\s+/).filter(Boolean)) {
      if (line && (line + " " + word).length > width) {
        lines.push(line)
        line = word
      } else line = line ? `${line} ${word}` : word
      while (line.length > width) {
        lines.push(line.slice(0, width))
        line = line.slice(width)
      }
    }
    lines.push(line)
  }
  return lines
}

export const SOURCE_ICONS: Record<string, string> = {
  rss: "📰", arxiv: "📄", github: "🐙", "github-mcp": "🆕", github_releases: "🐙", github_mcp: "🔌", knowledge: "📚",
}

export function sourceIcon(source: string): string {
  return SOURCE_ICONS[source] ?? (source?.startsWith("knowledge") ? "📚" : "🔗")
}

export function shortDate(value?: string | null): string {
  return value ? value.slice(0, 10) : "date ?"
}

export const RUN_STATES: Record<string, [string, string]> = {
  running: ["⏳", "en cours"],
  awaiting_approval: ["✋", "validation attendue"],
  published: ["✅", "publiée"],
  rejected: ["🗑️", "rejetée"],
  blocked: ["🛡️", "bloquée par les guards"],
  failed: ["❌", "en échec"],
  error: ["❌", "erreur"],
}

/** Étapes d'une veille, dans l'ordre du Task Graph. */
export const RUN_STAGES = ["Collecte", "Scout", "Critic", "Editor", "Validation", "Publication"]

/** Étape atteinte d'après les événements du run (0 → rien, 6 → publié). */
export function runStage(events: { type: string; node?: string; text?: string }[], status: string): number {
  if (status === "published") return 6
  let stage = 0
  for (const e of events) {
    const text = `${e.node ?? ""} ${e.text ?? ""}`.toLowerCase()
    if (e.type === "collect") stage = Math.max(stage, 1)
    if (e.type === "agent" && /scout|research/.test(text)) stage = Math.max(stage, 2)
    if (e.type === "agent" && /critic|review/.test(text)) stage = Math.max(stage, 3)
    if (e.type === "agent" && /editor|editorial/.test(text)) stage = Math.max(stage, 4)
    if (e.type === "awaiting_approval") stage = Math.max(stage, 5)
  }
  if (status === "awaiting_approval") stage = Math.max(stage, 5)
  return stage
}

export const REVIEW_STEPS = ["fetch", "analyze", "guard", "save"]
export const REVIEW_LABELS: Record<string, string> = {
  fetch: "📥 Téléchargement et extraction",
  analyze: "🤖 Reviewer (skill review-actu + règles métiers)",
  guard: "🛡️ Guard : citations vérifiées dans la page",
  save: "💾 Validation Pydantic et enregistrement",
}

export const VERDICTS: Record<string, [string, string]> = {
  ok: ["✅", "respectée"], ko: ["❌", "non respectée"], na: ["➖", "non applicable"],
}

/** Couleur d'un score 0-10. */
export function scoreColor(value: number): string {
  return value >= 7 ? "#6fcf97" : value >= 4 ? "#e6b85c" : "#f08a80"
}

/** « 2026-09-24T16:57:47.24+00:00 » → « 2026-09-24 16:57 ». */
export function dateTime(value?: string | null): string {
  return value ? value.replace("T", " ").slice(0, 16) : ""
}
