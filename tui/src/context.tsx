/** Contexte partagé : API, écran actif, mode (navigation / saisie), notifications, envoi au chat. */
import { createContext, useContext } from "react"
import type { VeilleApi } from "./lib/api"

export type ScreenId =
  | "home" | "chat" | "search" | "review" | "watch" | "knowledge" | "sources" | "reports" | "memory" | "grill"

/** `edit` : mode à l'ouverture ; `input` : l'écran a une zone de saisie (i ou / pour l'activer). */
export const SCREENS: { id: ScreenId; key: string; icon: string; label: string; edit: boolean; input: boolean }[] = [
  { id: "home", key: "1", icon: "🏠", label: "Accueil", edit: false, input: false },
  { id: "chat", key: "2", icon: "💬", label: "Chat", edit: true, input: true },
  { id: "search", key: "3", icon: "🔎", label: "Recherche", edit: true, input: true },
  { id: "review", key: "4", icon: "🔬", label: "Review", edit: false, input: true },
  { id: "watch", key: "5", icon: "🛰️", label: "Veille", edit: false, input: true },
  { id: "knowledge", key: "6", icon: "📚", label: "Knowledge", edit: false, input: true },
  { id: "sources", key: "7", icon: "🧭", label: "Sources", edit: false, input: true },
  { id: "reports", key: "8", icon: "📰", label: "Rapports", edit: false, input: false },
  { id: "memory", key: "9", icon: "🧠", label: "Mémoire", edit: false, input: false },
  { id: "grill", key: "0", icon: "🎯", label: "Grill-me", edit: false, input: true },
]

export type Mode = "nav" | "edit"
export type ToastKind = "info" | "ok" | "error"

export interface Health {
  ollama: boolean
  model: string
  model_available: boolean
  notion: boolean
  tracing: { langfuse: boolean; langsmith: boolean }
  memory: Record<string, number>
}

export interface AppContext {
  api: VeilleApi
  screen: ScreenId
  mode: Mode
  setMode: (mode: Mode) => void
  go: (screen: ScreenId, mode?: Mode) => void
  toast: (text: string, kind?: ToastKind) => void
  /** Pose une question au chat (ouvre l'écran Chat et l'envoie). */
  ask: (text: string) => void
  /** Message en attente pour un écran, consommé une seule fois. */
  pending: Partial<Record<ScreenId, string>>
  consume: (screen: ScreenId) => string | undefined
  health: Health | null
  width: number
  height: number
}

export const Ctx = createContext<AppContext | null>(null)

export function useApp(): AppContext {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error("useApp hors de <App>")
  return ctx
}

/** Vrai si l'écran est affiché : ses raccourcis ne s'appliquent qu'à ce moment-là. */
export function useActive(id: ScreenId): { active: boolean; nav: boolean; edit: boolean } {
  const { screen, mode } = useApp()
  const active = screen === id
  return { active, nav: active && mode === "nav", edit: active && mode === "edit" }
}
