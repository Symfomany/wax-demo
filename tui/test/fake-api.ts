/** Fausse API (mêmes formes que le serveur FastAPI) pour les tests et les captures d'écran. */
import type { VeilleApi } from "../src/lib/api"

export const REVIEW = {
  id: "r1", url: "https://arxiv.org/abs/2309.06180", title: "Efficient Memory Management for LLM Serving with PagedAttention",
  conversation_id: "c-review", revision: 1, model: "gemma-3-4b-it",
  page: { final_url: "https://arxiv.org/abs/2309.06180", site: "arXiv.org", published_at: "2023-09-12", word_count: 556, truncated: false,
          domains: ["Inférence", "Recherche"], glossary: [{ id: "glossaire/kv-cache", title: "KV cache" }] },
  analysis: {
    summary: "PagedAttention gère le **KV cache** par pages, comme la mémoire virtuelle ; vLLM en tire un meilleur débit.",
    why_it_matters: "Le KV cache limite la taille des lots en serving.",
    key_points: ["KV cache paginé"], source_type: "primaire", source_type_reason: "Article des auteurs.",
    relevance: 9, novelty: 8, confidence: 5,
    claims: [
      { claim: "Débit 2-4× supérieur", quote: "", kind: "benchmark", status: "non_etaye" },
      { claim: "Inspiré de la pagination des OS", quote: "inspired by the classical virtual memory and paging techniques", kind: "fait", status: "etaye" },
    ],
    rule_checks: [
      { rule_id: 1, domain: "Transverse", rule: "Toute affirmation doit être rattachée à un passage.", verdict: "ok", note: "citations vérifiées" },
      { rule_id: 11, domain: "Recherche", rule: "Un preprint arXiv n'est pas relu par les pairs.", verdict: "ko", note: "non signalé" },
    ],
    risks: ["Benchmark des auteurs"], questions: ["Quel matériel pour le 2-4× ?"], tags: ["vllm"],
  },
  warnings: ["Citation introuvable dans la page, affirmation marquée non étayée : « Débit 2-4× »"],
}

export const ROUTES: Record<string, any> = {
  "GET /api/health": { ollama: true, model: "gemma-3-4b-it", model_available: true, notion: true,
    tracing: { langfuse: false, langsmith: false }, memory: { documents: 200, digests: 1, published_items: 8, conversations: 4, reviews: 1, traces: 12 } },
  "GET /api/runs": [{ id: "4bbe7eb7-bbb4", status: "awaiting_approval", started_at: "2026-09-24 14:11:14" }],
  "GET /api/runs/4bbe7eb7-bbb4?after=0": {
    id: "4bbe7eb7-bbb4", status: "awaiting_approval", started_at: "2026-09-24 14:11:14", options: { keywords: ["open weights", "vLLM"] },
    outputs: {}, next: 5, markdown: "# Veille LLM — 24/09/2026\n\n## Synthèse\nLes mises à jour de **vLLM** dominent.\n\n## Signaux\n- vllm-project/vllm: v0.30.0",
    events: [
      { at: "2026-09-24T14:11:15", type: "step", node: "supervisor", text: "collecte parallèle (4 sources)" },
      { at: "2026-09-24T14:11:20", type: "collect", source: "rss", count: 24 },
      { at: "2026-09-24T14:12:02", type: "agent", node: "research", text: "11 signal(aux) proposé(s)" },
      { at: "2026-09-24T14:12:40", type: "agent", node: "review", text: "8 signal(aux) accepté(s)" },
      { at: "2026-09-24T14:13:05", type: "agent", node: "editorial", text: "digest rédigé" },
      { at: "2026-09-24T14:13:05", type: "awaiting_approval" },
    ],
  },
  "GET /api/reports": [{ name: "2026/veille-2026-09-24-0926.md", date: "2026-09-24-0926", html: "/reports/2026/x.html" }],
  "GET /api/reports/2026/veille-2026-09-24-0926.md": { name: "2026/veille-2026-09-24-0926.md", markdown: "# Veille LLM\n\n## Synthèse\nLes avancées récentes portent sur **vLLM** et l'inférence.\n\n## Signaux\n- NVIDIA : GPU cluster readiness\n- vllm-project/vllm: v0.30.0" },
  "GET /api/reviews": [{ id: "r1", title: REVIEW.title, site: "arXiv.org", relevance: 9, revision: 1, updated_at: "2026-09-24 15:00:00" }],
  "GET /api/reviews/r1": REVIEW,
  "GET /api/conversations/c-review": [],
  "GET /api/knowledge": {
    files: [{ name: "glossaire.md" }, { name: "regles-metiers.md" }, { name: "prompts-veille.md" }], domains: ["Inférence"], errors: [],
    entries: [
      { id: "glossaire/kv-cache", kind: "glossaire", title: "KV cache", domain: "Inférence", aliases: ["cache clé-valeur"], keywords: ["mémoire GPU"],
        sources: [], body: "Mémorisation des *keys* et *values* déjà calculées.", rules: [], file: "glossaire.md", origin: "base", url: "#k=glossaire/kv-cache" },
      { id: "glossaire/rag", kind: "glossaire", title: "RAG", domain: "RAG", aliases: ["retrieval-augmented generation"], keywords: ["recherche"],
        sources: ["https://arxiv.org/abs/2005.11401"], body: "Récupère des passages puis génère en les citant.", rules: [], file: "glossaire.md", origin: "base", url: "https://arxiv.org/abs/2005.11401" },
      { id: "regles-metiers/agents", kind: "regles", title: "Agents et outils", domain: "Agents", aliases: [], keywords: ["mcp"], sources: [], body: "",
        rules: ["Préciser le niveau d'autonomie et les garde-fous.", "Un score d'agent doit nommer le benchmark."], file: "regles-metiers.md", origin: "base" },
      { id: "prompts-veille/quoi-de-neuf", kind: "prompts", title: "Quoi de neuf", domain: "Transverse", target: "chat", aliases: [], keywords: [],
        sources: [], body: "Quoi de neuf dans les dernières veilles ?", rules: [], file: "prompts-veille.md", origin: "base" },
      { id: "prompts-veille/source-primaire", kind: "prompts", title: "Source primaire", domain: "Transverse", target: "review", aliases: [], keywords: [],
        sources: [], body: "Cet article est-il une source primaire ?", rules: [], file: "prompts-veille.md", origin: "base" },
    ],
  },
  "GET /api/knowledge/index": [{ term: "KV cache", entries: [{ id: "glossaire/kv-cache", title: "KV cache", kind: "glossaire", role: "terme" }] }],
  "GET /api/sources": {
    rss: [{ name: "Hugging Face Blog", url: "https://huggingface.co/blog/feed.xml" }], arxiv: ["https://rss.arxiv.org/rss/cs.CL"],
    github: ["vllm-project/vllm"], github_mcp: ["llm inference"],
    health: { rss: { ok: 3, failures: 0 }, github_releases: { ok: 2, failures: 1 } },
  },
  "POST /api/sources/inspect": { kind: "rss", label: "flux RSS/Atom", name: "vLLM Blog", value: "https://vllm.ai/blog/rss.xml",
    input_url: "https://blog.vllm.ai", entries: 50, sample_title: "vLLM v0.30", sample_link: "https://vllm.ai/blog/x", sample_date: "2026-09-22",
    warnings: [], already_present: false },
  "POST /api/sources": { added: {}, sources: {} },
  "GET /api/memory": {
    lessons: [{ note: "Écarter les tutoriels ; privilégier les releases", approved: false, created_at: "2026-09-24T10:00:00" }],
    tags: [["gpu", 6], ["vllm", 4], ["agents", 2]], sources: { rss: { ok: 3, failures: 0 }, arxiv: { ok: 2, failures: 1 } },
    interests: { summary: "Tu suis les LLM open weights et vLLM.", keywords: ["vLLM", "Qwen", "DeepSeek"], exclusions: ["tutoriels"] },
    notion: { url: "https://app.notion.com/p/veille", created_at: "2026-09-24" },
  },
  "POST /api/notion/sync": { url: "https://app.notion.com/p/veille", digests: 1 },
  "GET /api/grill/profile": {},
  "POST /api/grill": { session: "g1", question: { id: "domains", branch: "Domaines", text: "Sur quels pans de l'actualité IA veux-tu une veille ?",
    why: "Cadrer les branches.", multi: true, options: [{ id: "llm", label: "LLM & modèles" }, { id: "robotics", label: "Robotique" }],
    recommended: ["llm"], hint: "", progress: { index: 1, remaining: 5 } } },
  "POST /api/search": { count: 1, results: [{ url: "https://example.org/fp8", title: "FP8 quantization lands in vLLM", source: "rss",
    published_at: "2026-09-20T10:00:00", summary: "FP8 kernels for Ampere GPUs.", published: true }] },
}

export const CHAT_EVENTS = [
  { type: "conversation", id: "c1" },
  { type: "tool_start", tool: "knowledge_search", args: { query: "KV cache" } },
  { type: "tool_end", tool: "knowledge_search", summary: "3 entrée(s)", sources: [], data: {} },
  { type: "token", text: "Le KV cache mémorise " },
  { type: "token", text: "les clés et valeurs [1]." },
  { type: "final", text: "Le KV cache mémorise les clés et valeurs [1].", tool: "knowledge_search",
    sources: [{ n: 1, title: "KV cache", url: "#k=glossaire/kv-cache", source: "knowledge · glossaire", date: "" }], warnings: [],
    engaged: { nodes: ["route", "act", "respond", "guard"], tool: "knowledge_search", agents: [], skills: [], mcp: [] }, total_ms: 2100 },
]

export const REVIEW_EVENTS = [
  { type: "step", node: "fetch", detail: "arXiv.org · 556 mots", ms: 900 },
  { type: "step", node: "analyze", detail: "domaines : Inférence, Recherche · 13 règle(s)", ms: 41000 },
  { type: "step", node: "guard", detail: "1 correction(s)", ms: 5 },
  { type: "step", node: "save", detail: "révision 1", ms: 12 },
  { type: "review", review: REVIEW, trace_url: "/trace/t1" },
]

export function fakeApi(routes = ROUTES, calls: string[] = []): VeilleApi {
  const answer = async (key: string) => {
    calls.push(key)
    if (!(key in routes)) throw new Error(`route inconnue : ${key}`)
    return structuredClone(routes[key])
  }
  return {
    base: "http://127.0.0.1:8000",
    get: (path) => answer(`GET ${path}`),
    post: (path, body) => {
      calls.push(`BODY ${path} ${JSON.stringify(body ?? {})}`)
      return answer(`POST ${path}`)
    },
    del: (path) => answer(`DELETE ${path}`),
    async stream(path, body, onEvent) {
      calls.push(`STREAM ${path} ${JSON.stringify(body)}`)
      const events = path.startsWith("/api/chat") ? CHAT_EVENTS : REVIEW_EVENTS
      for (const event of events) {
        await Bun.sleep(1)
        onEvent(structuredClone(event))
      }
    },
  }
}
