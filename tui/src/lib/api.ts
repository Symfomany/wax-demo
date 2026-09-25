/** Client HTTP de l'API web de la veille (python -m app.main web). */
import { parseSSE } from "./sse"

export type Json = any

export interface VeilleApi {
  base: string
  get<T = Json>(path: string): Promise<T>
  post<T = Json>(path: string, body?: unknown): Promise<T>
  del<T = Json>(path: string): Promise<T>
  stream(path: string, body: unknown, onEvent: (event: Json) => void): Promise<void>
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function detail(response: Response): Promise<string> {
  const body: any = await response.json().catch(() => ({}))
  return typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`
}

/** Identifiants du serveur : jeton WEB_API_TOKEN (Bearer) ou login WEB_USERNAME / WEB_PASSWORD (Basic). */
export interface Credentials {
  token?: string
  username?: string
  password?: string
}

export function authorization(credentials: Credentials | string | undefined): string | undefined {
  const c = typeof credentials === "string" ? { token: credentials } : credentials ?? {}
  if (c.username && c.password) return `Basic ${Buffer.from(`${c.username}:${c.password}`).toString("base64")}`
  if (c.token) return `Bearer ${c.token}`
  return undefined
}

export function createApi(base: string, fetcher: typeof fetch = fetch, credentials?: Credentials | string): VeilleApi {
  base = base.replace(/\/$/, "")
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  const auth = authorization(credentials)
  if (auth) headers.Authorization = auth
  const call = async (method: string, path: string, body?: unknown): Promise<any> => {
    const response = await fetcher(base + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!response.ok) throw new ApiError(response.status, await detail(response))
    return response.json()
  }
  return {
    base,
    get: (path) => call("GET", path),
    post: (path, body) => call("POST", path, body ?? {}),
    del: (path) => call("DELETE", path),
    async stream(path, body, onEvent) {
      const response = await fetcher(base + path, {
        method: "POST",
        headers,
        body: body === null ? undefined : JSON.stringify(body),
      })
      if (!response.ok || !response.body) throw new ApiError(response.status, await detail(response))
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ""
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        const parsed = parseSSE(buffer + decoder.decode(value, { stream: true }))
        buffer = parsed.rest
        parsed.events.forEach(onEvent)
      }
      parseSSE(buffer + "\n\n").events.forEach(onEvent)
    },
  }
}

/** URL du serveur : VEILLE_URL, sinon celle de « veille start » (data/web.url), sinon le défaut. */
export async function serverUrl(root: string): Promise<string> {
  if (process.env.VEILLE_URL) return process.env.VEILLE_URL
  try {
    const file = Bun.file(`${process.env.VEILLE_RUN_DIR ?? root + "/data"}/web.url`)
    if (await file.exists()) return (await file.text()).trim()
  } catch {}
  return "http://127.0.0.1:8000"
}

/** Clés d'un fichier .env (KEY=valeur, guillemets simples ou doubles retirés) ; seules `keys` sont lues. */
export function parseEnv(text: string, keys: string[]): Record<string, string> {
  const found: Record<string, string> = {}
  for (const line of text.split("\n")) {
    const match = line.match(/^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$/)
    if (!match || !keys.includes(match[1])) continue
    let value = match[2]
    if (/^(['"]).*\1$/.test(value)) value = value.slice(1, -1)
    else value = value.replace(/(^|\s+)#.*$/, "")
    if (value) found[match[1]] = value
  }
  return found
}

/** Identifiants : variables d'environnement, sinon WEB_USERNAME / WEB_PASSWORD / WEB_API_TOKEN du .env du projet. */
export async function loadCredentials(root: string): Promise<Credentials> {
  const keys = ["WEB_USERNAME", "WEB_PASSWORD", "WEB_API_TOKEN"]
  let fromFile: Record<string, string> = {}
  try {
    const file = Bun.file(`${root}/.env`)
    if (await file.exists()) fromFile = parseEnv(await file.text(), keys)
  } catch {}
  const pick = (key: string) => process.env[key] || fromFile[key]
  return { username: pick("WEB_USERNAME"), password: pick("WEB_PASSWORD"), token: pick("WEB_API_TOKEN") }
}
