/** Point d'entrée : vérifie le serveur web de la veille (le démarre au besoin), puis lance la TUI.
 *
 *   bin/veille tui            (ou : cd tui && bun run start)
 *   VEILLE_URL=http://127.0.0.1:8000  VEILLE_NO_START=1  VEILLE_SCREEN=review
 */
import { createCliRenderer } from "@opentui/core"
import { createRoot } from "@opentui/react"
import { resolve } from "node:path"
import { App } from "./app"
import { SCREENS, type ScreenId } from "./context"
import { createApi, serverUrl } from "./lib/api"

const ROOT = resolve(import.meta.dir, "../..")
const SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

async function healthy(base: string): Promise<boolean> {
  try {
    return (await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(2000) })).ok
  } catch {
    return false
  }
}

async function ensureServer(): Promise<string> {
  let base = await serverUrl(ROOT)
  if (await healthy(base)) return base
  if (process.env.VEILLE_NO_START || process.env.VEILLE_URL) {
    console.error(`✗ Serveur de veille injoignable : ${base}\n  Démarrez-le : bin/veille start`)
    process.exit(1)
  }
  process.stdout.write("🛰️  Démarrage du serveur de veille (bin/veille start)… ")
  const child = Bun.spawn([`${ROOT}/bin/veille`, "start"], { cwd: ROOT, stdout: "pipe", stderr: "pipe" })
  let frame = 0
  const timer = setInterval(() => process.stdout.write(`\r🛰️  Démarrage du serveur de veille ${SPIN[frame++ % SPIN.length]} `), 90)
  const code = await child.exited
  clearInterval(timer)
  if (code !== 0) {
    console.error(`\n✗ Échec du démarrage :\n${await new Response(child.stderr).text()}`)
    process.exit(1)
  }
  base = await serverUrl(ROOT)
  console.log(`\r✅ Serveur démarré : ${base}                    `)
  return base
}

const base = await ensureServer()
const screen = (SCREENS.find((s) => s.id === process.env.VEILLE_SCREEN)?.id ?? "home") as ScreenId
const renderer = await createCliRenderer({ exitOnCtrlC: false, targetFps: 30 })
createRoot(renderer).render(<App api={createApi(base)} initialScreen={screen} />)
