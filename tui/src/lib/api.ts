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

export function createApi(base: string, fetcher: typeof fetch = fetch, token?: string): VeilleApi {
  base = base.replace(/\/$/, "")
  // Jeton WEB_API_TOKEN du serveur, s'il en exige un.
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  if (token) headers.Authorization = `Bearer ${token}`
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
