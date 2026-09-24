/** Découpe un flux Server-Sent Events (« data: {…}\n\n ») en événements JSON. */
export function parseSSE(buffer: string): { events: any[]; rest: string } {
  const events: any[] = []
  let rest = buffer
  let index: number
  while ((index = rest.indexOf("\n\n")) >= 0) {
    const chunk = rest.slice(0, index)
    rest = rest.slice(index + 2)
    for (const line of chunk.split("\n")) {
      if (!line.startsWith("data: ")) continue
      try {
        events.push(JSON.parse(line.slice(6)))
      } catch {
        // ligne tronquée ou non JSON : ignorée
      }
    }
  }
  return { events, rest }
}
