/** Cadre d'un écran : masqué (pas démonté) quand il n'est pas actif, raccourcis en pied. */
import type { ReactNode } from "react"
import { useActive, type ScreenId } from "../context"
import { Keys } from "./ui"
import { C } from "../theme"

export function Screen({ id, keys, children }: { id: ScreenId; keys: [string, string][]; children: ReactNode }) {
  const { active } = useActive(id)
  return (
    <box visible={active} flexDirection="column" flexGrow={1} paddingTop={1}>
      <box flexDirection="column" flexGrow={1}>
        {children}
      </box>
      <box height={1}>
        <Keys items={keys} />
      </box>
    </box>
  )
}

export function Panel({ title, children, grow = 1, width, focused = false, height }:
  { title: string; children: ReactNode; grow?: number; width?: number | `${number}%`; focused?: boolean; height?: number }) {
  return (
    <box border borderStyle="rounded" borderColor={focused ? C.accent : C.line} title={` ${title} `} titleAlignment="left"
      flexDirection="column" flexGrow={width || height ? 0 : grow} width={width} height={height} paddingLeft={1} paddingRight={1}>
      {children}
    </box>
  )
}
