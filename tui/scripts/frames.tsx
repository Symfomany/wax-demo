/** Affiche le rendu texte de chaque écran (avec la fausse API) : débogage de mise en page. */
import { act } from "react"
import { testRender } from "@opentui/react/test-utils"
import { App } from "../src/app"
import { fakeApi } from "../test/fake-api"

const only = process.argv[2]
for (const key of ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]) {
  if (only && only !== key) continue
  const t = await testRender(<App api={fakeApi()} onQuit={() => {}} />, { width: 120, height: 40 })
  const settle = async () => { for (let i = 0; i < 4; i++) { await act(async () => { await Bun.sleep(40) }); await t.renderOnce() } }
  await settle()
  await act(async () => { t.mockInput.pressKey(key) })
  await settle()
  if (key === "5") { await act(async () => { await Bun.sleep(1400) }); await settle() }
  console.log(`===== écran ${key}\n` + t.captureCharFrame())
  act(() => t.renderer.destroy())
}
process.exit(0)
