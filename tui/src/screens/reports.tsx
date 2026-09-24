/** 📰 Rapports datés (Markdown) de chaque veille publiée. */
import { useKeyboard } from "@opentui/react"
import { useEffect, useState } from "react"
import { useActive, useApp } from "../context"
import { Screen, Panel } from "../components/screen"
import { Md, Spinner } from "../components/ui"
import { C } from "../theme"

export function ReportsScreen() {
  const { api, toast } = useApp()
  const { active, nav } = useActive("reports")
  const [reports, setReports] = useState<any[] | null>(null)
  const [content, setContent] = useState<{ name: string; markdown: string; html_url?: string } | null>(null)
  const [pane, setPane] = useState<"list" | "read">("list")

  const open = (name: string) => api.get(`/api/reports/${name}`).then(setContent).catch((e) => toast(e.message, "error"))
  useEffect(() => {
    if (!active) return
    api.get("/api/reports").then((list: any[]) => {
      setReports(list)
      if (list[0] && !content) open(list[0].name)
    }).catch((e) => toast(e.message, "error"))
  }, [active])
  useKeyboard((key) => { if (nav && key.name === "tab") setPane((p) => (p === "list" ? "read" : "list")) })

  return (
    <Screen id="reports" keys={[["↑↓", pane === "list" ? "rapports" : "défiler"], ["Tab", "liste/lecture"]]}>
      <box flexDirection="row" flexGrow={1}>
        <Panel title={`📰 Rapports (${reports?.length ?? "…"})`} width={30} focused={nav && pane === "list"}>
          {!reports ? <Spinner label="chargement…" /> : reports.length ? (
            <select focused={nav && pane === "list"} flexGrow={1} backgroundColor={C.bg} focusedBackgroundColor={C.bg}
              selectedBackgroundColor={C.panel2} selectedTextColor={C.accent} descriptionColor={C.dim}
              options={reports.map((r) => ({ name: `📄 ${r.date}`, description: r.html ? "Markdown + HTML" : "Markdown", value: r.name }))}
              onChange={(_, option) => option && open(option.value)} />
          ) : <text fg={C.muted}>Aucun rapport : publiez une veille (écran 5).</text>}
        </Panel>
        <Panel title={content ? `📖 ${content.name}` : "Lecture"} focused={nav && pane === "read"}>
          {content ? (
            <scrollbox flexGrow={1} focused={nav && pane === "read"}>
              <Md content={content.markdown} />
              {content.html_url ? <text fg={C.dim}>{"\n"}Version HTML : {api.base}{content.html_url}</text> : null}
            </scrollbox>
          ) : <text fg={C.muted}>—</text>}
        </Panel>
      </box>
    </Screen>
  )
}
