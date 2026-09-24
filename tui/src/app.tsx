/** Application : barre d'onglets, écrans, barre d'état, aide et notifications. */
import { useKeyboard, useRenderer, useTerminalDimensions } from "@opentui/react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Ctx, SCREENS, type AppContext, type Health, type Mode, type ScreenId, type ToastKind } from "./context"
import type { VeilleApi } from "./lib/api"
import { C } from "./theme"
import { Keys } from "./components/ui"
import { HomeScreen } from "./screens/home"
import { ChatScreen } from "./screens/chat"
import { SearchScreen } from "./screens/search"
import { ReviewScreen } from "./screens/review"
import { WatchScreen } from "./screens/watch"
import { KnowledgeScreen } from "./screens/knowledge"
import { SourcesScreen } from "./screens/sources"
import { ReportsScreen } from "./screens/reports"
import { MemoryScreen } from "./screens/memory"
import { GrillScreen } from "./screens/grill"

const GLOBAL_KEYS: [string, string][] = [
  ["1-0 / ←→", "écrans"], ["F1-F10", "écrans (partout)"], ["Échap", "navigation"], ["/ ou i", "saisie"],
  ["↑↓", "listes"], ["Tab", "panneau"], ["?", "aide"], ["q / Ctrl+C", "quitter"],
]

export function App({ api, initialScreen = "home", onQuit }: { api: VeilleApi; initialScreen?: ScreenId; onQuit?: () => void }) {
  const renderer = useRenderer()
  const { width, height } = useTerminalDimensions()
  const [screen, setScreen] = useState<ScreenId>(initialScreen)
  const [mode, setMode] = useState<Mode>(SCREENS.find((s) => s.id === initialScreen)?.edit ? "edit" : "nav")
  const [toastState, setToast] = useState<{ text: string; kind: ToastKind } | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [help, setHelp] = useState(false)
  const pending = useRef<Partial<Record<ScreenId, string>>>({})
  const [, force] = useState(0)

  const toast = useCallback((text: string, kind: ToastKind = "info") => setToast({ text, kind }), [])
  useEffect(() => {
    if (!toastState) return
    const timer = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(timer)
  }, [toastState])

  const go = useCallback((id: ScreenId, next?: Mode) => {
    setScreen(id)
    setMode(next ?? (SCREENS.find((s) => s.id === id)?.edit ? "edit" : "nav"))
  }, [])

  const quit = useCallback(() => {
    if (onQuit) return onQuit()
    renderer.destroy()
    process.exit(0)
  }, [renderer, onQuit])

  useEffect(() => {
    let alive = true
    const load = () => api.get<Health>("/api/health").then((h) => alive && setHealth(h)).catch(() => alive && setHealth(null))
    load()
    const timer = setInterval(load, 15000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [api])

  const ctx: AppContext = useMemo(() => ({
    api, screen, mode, setMode, go, toast, health, width, height,
    pending: pending.current,
    ask: (text: string) => {
      pending.current.chat = text
      go("chat", "edit")
      force((n) => n + 1)
    },
    consume: (id: ScreenId) => {
      const value = pending.current[id]
      delete pending.current[id]
      return value
    },
  }), [api, screen, mode, go, toast, health, width, height])

  useKeyboard((key) => {
    if (key.ctrl && key.name === "c") return quit()
    const index = SCREENS.findIndex((s) => s.id === screen)
    const fkey = key.name?.match(/^f(\d+)$/)
    if (fkey && Number(fkey[1]) >= 1 && Number(fkey[1]) <= SCREENS.length) return go(SCREENS[Number(fkey[1]) - 1].id)
    if (key.meta && /^[0-9]$/.test(key.name ?? "")) return go(SCREENS.find((s) => s.key === key.name)!.id)
    if (mode === "edit") {
      if (key.name === "escape") setMode("nav")
      return
    }
    if (help) {
      setHelp(false)
      return
    }
    if (key.name === "escape") return
    if (key.name === "q") return quit()
    if (key.sequence === "?") return setHelp(true)
    if (/^[0-9]$/.test(key.name ?? "") && !key.ctrl) return go(SCREENS.find((s) => s.key === key.name)!.id)
    if ((key.name === "i" || key.sequence === "/") && SCREENS[index].input) return setMode("edit")
    if (key.name === "right") return go(SCREENS[(index + 1) % SCREENS.length].id)
    if (key.name === "left") return go(SCREENS[(index - 1 + SCREENS.length) % SCREENS.length].id)
  })

  // Terminal étroit : libellé complet pour l'onglet actif seulement.
  const compact = width < 160
  const modeLabel = mode === "edit" ? " ✎ SAISIE " : " ◆ NAVIGATION "
  const toastColor = toastState?.kind === "error" ? C.bad : toastState?.kind === "ok" ? C.ok : C.accent

  return (
    <Ctx.Provider value={ctx}>
      <box flexDirection="column" width="100%" height="100%" backgroundColor={C.bg}>
        <box flexDirection="row" height={1} paddingLeft={1} backgroundColor={C.panel}>
          <text>
            <strong fg={C.accent}>✦ Veille GenAI </strong>
            {SCREENS.map((s) => (
              <span key={s.id} fg={s.id === screen ? C.bg : C.muted} bg={s.id === screen ? C.accent : undefined}>
                {s.id === screen || compact === false ? ` ${s.key} ${s.icon} ${s.label} ` : ` ${s.key} ${s.icon} `}
              </span>
            ))}
          </text>
        </box>
        <box flexGrow={1} flexDirection="column" paddingLeft={1} paddingRight={1}>
          <HomeScreen />
          <ChatScreen />
          <SearchScreen />
          <ReviewScreen />
          <WatchScreen />
          <KnowledgeScreen />
          <SourcesScreen />
          <ReportsScreen />
          <MemoryScreen />
          <GrillScreen />
        </box>
        {help ? (
          <box position="absolute" top={3} left={Math.max(2, Math.floor(width / 2) - 32)} width={64} border borderStyle="rounded"
            borderColor={C.accent} title=" ⌨️  Raccourcis " backgroundColor={C.panel} padding={1} flexDirection="column" zIndex={10}>
            {GLOBAL_KEYS.map(([k, label]) => (
              <text key={k}>
                <span fg={C.accent}>{k.padEnd(14)}</span>
                <span fg={C.text}>{label}</span>
              </text>
            ))}
            <text fg={C.muted}>Chaque écran affiche ses propres raccourcis en bas. Une touche pour fermer.</text>
          </box>
        ) : null}
        <box flexDirection="row" height={1} backgroundColor={C.panel}>
          <text>
            <span fg={C.bg} bg={mode === "edit" ? C.warn : C.accent}>{modeLabel}</span>
            <span fg={health ? (health.ollama && health.model_available ? C.ok : C.bad) : C.dim}>  ● </span>
            <span fg={C.muted}>{health ? (health.ollama ? health.model : "LLM injoignable") : "serveur…"}</span>
            <span fg={health?.notion ? C.ok : C.dim}>  ● </span>
            <span fg={C.muted}>Notion</span>
            <span fg={health?.tracing?.langfuse ? C.ok : C.dim}>  ● </span>
            <span fg={C.muted}>Langfuse</span>
            <span fg={C.dim}>  {api.base}</span>
            {toastState ? <span fg={toastColor}>   {toastState.text}</span> : <span fg={C.dim}>   ? aide · q quitter</span>}
          </text>
        </box>
      </box>
    </Ctx.Provider>
  )
}

export { Keys }
