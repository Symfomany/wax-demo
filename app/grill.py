"""Grill-me : un entretien serré pour cerner ce que l'utilisateur cherche dans l'actualité IA.

Inspiré du skill « grill-me » : une question à la fois, chacune accompagnée d'une
recommandation et de sa raison d'être ; on descend dans chaque branche choisie et on
challenge les réponses floues (trop de domaines, « tout », « je ne sais pas »).

Graphe LangGraph : `ask` s'interrompt (`interrupt`) à chaque question et reprend avec la
réponse ; `synthesize` produit un profil (mots-clés et exclusions déterministes, résumé
rédigé par le LLM) ; `save` l'enregistre dans la mémoire des agents.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.store.base import BaseStore
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app.harness.prompts import render_prompt
from app.llm import BudgetExceeded, LLMOutputError, StructuredLLM
from app.memory import WatchMemory


# --- Banque de questions ---------------------------------------------------------------


@dataclass(frozen=True)
class Option:
    id: str
    label: str
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class Question:
    id: str
    branch: str
    text: str
    why: str
    options: tuple[Option, ...]
    recommended: tuple[str, ...] = ()
    multi: bool = True
    hint: str = "Précise en texte libre si besoin"

    def payload(self, index: int, remaining: int) -> dict:
        return {
            "id": self.id,
            "branch": self.branch,
            "text": self.text,
            "why": self.why,
            "multi": self.multi,
            "options": [{"id": o.id, "label": o.label} for o in self.options],
            "recommended": list(self.recommended),
            "hint": self.hint,
            "progress": {"index": index, "remaining": remaining},
        }


DOMAINS = Question(
    "domains", "Périmètre",
    "Sur quels pans de l'actualité IA veux-tu une veille ? (plusieurs choix possibles)",
    "Tout suivre, c'est ne rien suivre : je pars de tes domaines pour creuser chaque branche.",
    (
        Option("llm", "LLM & modèles de langage", ("llm", "language model")),
        Option("models", "Nouveaux modèles (releases)", ("release", "open weights")),
        Option("robotics", "Robotique & IA incarnée", ("robot", "robotics")),
        Option("agents", "Agents & MCP", ("agent", "mcp")),
        Option("architecture", "Architecture & infra (inférence, GPU, edge)", ("inference", "gpu")),
        Option("research", "Recherche (arXiv)", ("arxiv", "paper")),
        Option("events", "Événements & conférences", ("conference", "keynote")),
        Option("tools", "Outils & frameworks", ("framework", "sdk")),
        Option("regulation", "Réglementation & société", ("ai act", "regulation")),
    ),
    recommended=("llm", "agents", "architecture"),
)

BRANCHES: dict[str, list[Question]] = {
    "llm": [
        Question("llm_kind", "LLM", "Quels modèles de langage t'intéressent ?",
                 "Ouverts ou fermés, gros ou petits : les sources et les critères ne sont pas les mêmes.",
                 (Option("open", "Modèles ouverts (open weights)", ("open weights", "open-source model")),
                  Option("closed", "Modèles propriétaires via API", ("api", "frontier model")),
                  Option("small", "Petits modèles pour le local (< 10B)", ("small language model", "quantized")),
                  Option("multimodal", "Multimodal (vision, audio)", ("multimodal", "vision-language")),
                  Option("reasoning", "Raisonnement", ("reasoning", "chain-of-thought"))),
                 recommended=("open", "small")),
        Question("llm_signal", "LLM", "Qu'est-ce qui rend une annonce LLM importante pour toi ?",
                 "Je veux savoir ce que le Critic doit exiger avant de garder un signal.",
                 (Option("bench", "Benchmarks avec méthodologie", ("benchmark", "evaluation")),
                  Option("license", "Licence et conditions d'usage", ("license",)),
                  Option("context", "Contexte long", ("long context",)),
                  Option("cost", "Coût et efficacité", ("efficiency", "cost"))),
                 recommended=("bench", "license")),
    ],
    "models": [
        Question("model_families", "Nouveaux modèles", "Quelles familles de modèles suivre de près ?",
                 "Une liste de familles transforme « nouveaux modèles » en mots-clés vérifiables.",
                 (Option("llama", "Llama", ("llama",)), Option("qwen", "Qwen", ("qwen",)),
                  Option("gemma", "Gemma", ("gemma",)), Option("mistral", "Mistral", ("mistral",)),
                  Option("deepseek", "DeepSeek", ("deepseek",)), Option("claude", "Claude", ("claude",)),
                  Option("gpt", "GPT", ("gpt",)), Option("phi", "Phi", ("phi",))),
                 recommended=("gemma", "qwen", "llama"), hint="Autre famille ? Écris-la"),
        Question("model_threshold", "Nouveaux modèles", "Toute release, ou seulement les majeures ?",
                 "Sans seuil, les patchs de version noient les vraies nouveautés.",
                 (Option("major", "Seulement les versions majeures"), Option("all", "Toutes les releases")),
                 recommended=("major",), multi=False),
    ],
    "robotics": [
        Question("robotics_topics", "Robotique", "Quels sujets robotique ?",
                 "La robotique IA va des humanoïdes aux modèles VLA : il faut choisir un angle.",
                 (Option("humanoid", "Humanoïdes", ("humanoid",)),
                  Option("vla", "Modèles vision-langage-action (VLA)", ("vision-language-action", "vla")),
                  Option("sim", "Simulation & sim-to-real", ("simulation", "sim-to-real")),
                  Option("manipulation", "Manipulation & préhension", ("manipulation", "grasping")),
                  Option("edge_robot", "Robotique embarquée (Jetson, ROS)", ("ros", "jetson"))),
                 recommended=("vla", "edge_robot")),
        Question("robotics_angle", "Robotique", "Plutôt recherche ou produits ?",
                 "La recherche passe par arXiv (cs.RO), les produits par les blogs éditeurs.",
                 (Option("research", "Recherche (papiers)"), Option("products", "Produits & démos"),
                  Option("both", "Les deux")),
                 recommended=("both",), multi=False),
    ],
    "agents": [
        Question("agents_aspects", "Agents", "Quels aspects des agents ?",
                 "« Agents » recouvre des sujets très différents ; je veux savoir lesquels trier en priorité.",
                 (Option("mcp", "MCP & outils", ("mcp", "model context protocol")),
                  Option("orchestration", "Orchestration (LangGraph…)", ("langgraph", "orchestration")),
                  Option("coding", "Agents de code", ("coding agent",)),
                  Option("agent_eval", "Évaluation d'agents", ("agent evaluation", "agent benchmark")),
                  Option("agent_safety", "Sécurité des agents", ("prompt injection", "agent security"))),
                 recommended=("mcp", "orchestration")),
    ],
    "architecture": [
        Question("arch_layer", "Architecture", "Quelle couche de la pile t'intéresse ?",
                 "Inférence, RAG ou observabilité ne se suivent pas aux mêmes endroits.",
                 (Option("inference", "Serveurs d'inférence (vLLM, TensorRT-LLM, llama.cpp)", ("vllm", "tensorrt-llm", "llama.cpp")),
                  Option("quant", "Quantification", ("quantization", "fp8", "gguf")),
                  Option("rag", "RAG & bases vectorielles", ("rag", "retrieval", "vector database")),
                  Option("obs", "Observabilité & évaluation", ("observability", "tracing")),
                  Option("edge", "Edge & embarqué", ("edge", "on-device"))),
                 recommended=("inference", "quant")),
        Question("arch_hardware", "Architecture", "Sur quel matériel fais-tu tourner tes modèles ?",
                 "Une annonce n'est utile que si elle s'applique à ton matériel.",
                 (Option("consumer_gpu", "GPU grand public (RTX)", ("rtx", "consumer gpu")),
                  Option("jetson", "Jetson / embarqué", ("jetson",)),
                  Option("apple", "Apple Silicon", ("apple silicon", "mlx")),
                  Option("cloud", "Cloud / datacenter", ("h100", "datacenter"))),
                 recommended=("consumer_gpu",)),
    ],
    "research": [
        Question("research_cats", "Recherche", "Quelles catégories arXiv suivre ?",
                 "Chaque catégorie publie des centaines d'articles par jour : il faut en choisir peu.",
                 (Option("cs.CL", "cs.CL — langage"), Option("cs.AI", "cs.AI — IA générale"),
                  Option("cs.LG", "cs.LG — apprentissage"), Option("cs.RO", "cs.RO — robotique"),
                  Option("cs.CV", "cs.CV — vision"), Option("cs.MA", "cs.MA — multi-agents")),
                 recommended=("cs.CL", "cs.AI")),
        Question("research_filter", "Recherche", "Quels articles garder ?",
                 "Le Scout doit savoir s'il privilégie le code, les benchmarks ou les synthèses.",
                 (Option("code", "Avec code publié", ("code", "github")),
                  Option("bench_paper", "Nouveaux benchmarks", ("benchmark",)),
                  Option("survey", "Surveys & synthèses", ("survey",))),
                 recommended=("code", "bench_paper")),
    ],
    "events": [
        Question("events_kind", "Événements", "Quels événements ?",
                 "Je dois savoir quels événements comptent, et donc quelles sources ajouter.",
                 (Option("conferences", "Grandes conférences (NeurIPS, ICML, ICLR…)", ("neurips", "icml", "iclr")),
                  Option("keynotes", "Keynotes éditeurs (GTC, I/O…)", ("keynote", "gtc")),
                  Option("meetups", "Meetups & communautés", ("meetup",)),
                  Option("hackathons", "Hackathons", ("hackathon",))),
                 recommended=("conferences", "keynotes")),
        Question("events_where", "Événements", "Quelle zone géographique ?",
                 "Un événement n'est actionnable que si tu peux y aller ou le suivre.",
                 (Option("france", "France"), Option("europe", "Europe"), Option("online", "En ligne"),
                  Option("world", "Monde entier")),
                 recommended=("online", "france")),
    ],
    "tools": [
        Question("tools_kind", "Outils", "Quels outils suivre ?",
                 "Je transforme tes outils en dépôts GitHub dont on suit les releases.",
                 (Option("langchain", "LangChain / LangGraph", ("langchain", "langgraph")),
                  Option("llamaindex", "LlamaIndex", ("llamaindex",)),
                  Option("ollama", "Ollama / llama.cpp", ("ollama", "llama.cpp")),
                  Option("ide", "Assistants de code & IDE", ("coding assistant", "ide")),
                  Option("obs_tools", "Langfuse / LangSmith", ("langfuse", "langsmith"))),
                 recommended=("langchain", "ollama")),
    ],
    "regulation": [
        Question("regulation_topics", "Réglementation", "Quels sujets réglementaires ?",
                 "La réglementation se suit par sources officielles ; je dois savoir lesquelles.",
                 (Option("ai_act", "AI Act (UE)", ("ai act",)),
                  Option("copyright", "Droit d'auteur & données", ("copyright", "training data")),
                  Option("safety", "Sécurité & alignement", ("ai safety", "alignment"))),
                 recommended=("ai_act",)),
    ],
}

COMMON: list[Question] = [
    Question("exclusions", "Filtre", "Qu'est-ce que tu ne veux PAS voir passer ?",
             "Les exclusions deviennent des règles pour le Scout et le Critic.",
             (Option("tutorials", "Tutoriels, cours, listes « awesome »", ("tutorial", "course", "awesome")),
              Option("funding", "Levées de fonds & business", ("funding", "raises")),
              Option("marketing", "Annonces marketing sans détail technique", ("marketing",)),
              Option("rumors", "Rumeurs & fuites", ("rumor", "leak"))),
             recommended=("tutorials", "funding", "marketing")),
    Question("depth", "Format", "Quel niveau de détail dans le digest ?",
             "Le prompt de l'Editor s'adapte à ce niveau.",
             (Option("headlines", "Titres + une phrase"), Option("summary", "Résumé + pourquoi c'est important"),
              Option("technical", "Analyse technique détaillée")),
             recommended=("summary",), multi=False),
    Question("frequency", "Format", "À quel rythme veux-tu ta veille ?",
             "Le rythme règle l'âge maximal des documents et le timer systemd.",
             (Option("daily", "Quotidienne (jours ouvrés)"), Option("weekly", "Hebdomadaire")),
             recommended=("daily",), multi=False),
    Question("keywords", "Précision", "Des mots-clés précis à surveiller absolument ?",
             "Un nom de projet ou de modèle précis vaut mieux que dix catégories.",
             (), hint="Ex. « vLLM, Gemma 3, MCP, Jetson Thor » — séparés par des virgules"),
]

QUESTIONS: dict[str, Question] = {q.id: q for q in [DOMAINS, *COMMON, *(q for qs in BRANCHES.values() for q in qs)]}
# Identifiants internes (« quant », « models »…) : jamais acceptés comme mots-clés venant du LLM.
OPTION_IDS = {o.id.lower() for q in QUESTIONS.values() for o in q.options}
VAGUE = re.compile(r"^\s*(tout|tous|toutes|n'importe|peu importe|je (ne )?sais pas|jsp|rien|aucune idée)\W*$", re.I)
ARXIV_FEED = "https://rss.arxiv.org/rss/{category}"  # modèle des flux arXiv vérifiés (sources.toml)
DOMAIN_CATEGORIES = {"robotics": "cs.RO", "agents": "cs.MA", "architecture": "cs.LG", "llm": "cs.CL"}


def dynamic_question(question_id: str, answers: list[dict]) -> Question:
    """Questions de relance construites à partir des réponses précédentes."""
    if question_id == "priorities":
        chosen = next(a for a in answers if a["id"] == "domains")["options"]
        options = tuple(o for o in DOMAINS.options if o.id in chosen)
        return Question("priorities", "Challenge",
                        f"Tu as choisi {len(chosen)} domaines. Lesquels sont vraiment prioritaires (3 maximum) ?",
                        "Au-delà de trois priorités, le digest se dilue : je classe tes domaines.",
                        options, recommended=tuple(chosen[:3]))
    if question_id.startswith("clarify:"):
        target = QUESTIONS[question_id.split(":", 1)[1]]
        return Question(question_id, "Challenge",
                        f"« {target.text} » — tu as répondu de façon vague. Donne un exemple concret de "
                        "sujet que tu aurais aimé lire cette semaine.",
                        "Une réponse vague ne se traduit en aucun filtre ; un exemple, si.",
                        (), hint="Ex. « la sortie d'un modèle 4B qui tient sur 8 Go »")
    return QUESTIONS[question_id]


# --- Graphe ---------------------------------------------------------------------------


class GrillSynthesis(BaseModel):
    summary: str = Field(description="3 phrases maximum, à la deuxième personne, en français")
    extra_keywords: list[str] = Field(default_factory=list, description="8 mots-clés en anglais au plus")


class SuggestedSource(BaseModel):
    name: str
    url: str
    reason: str


class GrillProfile(BaseModel):
    domains: list[str]
    priorities: list[str]
    keywords: list[str]
    exclusions: list[str]
    arxiv_categories: list[str] = Field(default_factory=list)
    suggested_sources: list[SuggestedSource] = Field(default_factory=list)
    depth: str = "summary"
    frequency: str = "daily"
    summary: str = ""
    answered_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class GrillState(TypedDict, total=False):
    queue: list[str]
    answers: Annotated[list[dict], operator.add]
    profile: dict


@dataclass
class GrillContext:
    llm: StructuredLLM | None = None
    configured_feeds: set[str] = field(default_factory=set)


def build_grill_graph(context: GrillContext, checkpointer=None, store: BaseStore | None = None):
    def ask(state: GrillState) -> dict:
        queue = list(state.get("queue", ["domains"]))
        answers = state.get("answers", [])
        question = dynamic_question(queue[0], answers)
        resumed = interrupt(question.payload(len(answers) + 1, len(queue) - 1))
        record = normalize_answer(question, (resumed or {}).get("reply"))
        first, last = follow_ups(question, record, answers)
        return {"queue": first + queue[1:] + last, "answers": [record]}

    def route(state: GrillState) -> str:
        return "ask" if state["queue"] else "synthesize"

    def synthesize(state: GrillState) -> dict:
        return {"profile": build_profile(state["answers"], context).model_dump()}

    def save(state: GrillState, store: BaseStore) -> dict:
        WatchMemory(store).set_interests(state["profile"])
        return {}

    builder = StateGraph(GrillState)
    builder.add_node("ask", ask)
    builder.add_node("synthesize", synthesize)
    builder.add_node("save", save)
    builder.add_edge(START, "ask")
    builder.add_conditional_edges("ask", route, ["ask", "synthesize"])
    builder.add_edge("synthesize", "save")
    builder.add_edge("save", END)
    return builder.compile(checkpointer=checkpointer, store=store)


def normalize_answer(question: Question, answer: dict | None) -> dict:
    """Réponse brute de l'interface → options valides, texte, drapeau « passé »."""
    answer = answer or {}
    valid = {o.id for o in question.options}
    options = [o for o in answer.get("options", []) if o in valid]
    if not question.multi:
        options = options[:1]
    if question.id == "priorities":
        options = options[:3]
    text = str(answer.get("text", "")).strip()[:500]
    if answer.get("recommended"):
        options = list(question.recommended)
    return {"id": question.id, "branch": question.branch, "question": question.text,
            "options": options, "text": text, "skipped": not options and not text}


def resume_payload(reply: dict | None) -> dict:
    """Enveloppe de reprise toujours non vide : LangGraph ignore `Command(resume={})`."""
    return {"reply": reply or {}}


def follow_ups(question: Question, record: dict, previous: list[dict]) -> tuple[list[str], list[str]]:
    """Descente dans les branches et challenges (logique « grill-me »).

    Renvoie (questions à poser tout de suite, questions à ajouter en fin de file)."""
    first: list[str] = []
    last: list[str] = []
    if question.id == "domains":
        domains = record["options"] or list(DOMAINS.recommended)
        record["options"] = domains
        if len(domains) > 3:
            first.append("priorities")
        for domain in domains:
            last += [q.id for q in BRANCHES[domain]]
        last += [q.id for q in COMMON]
    if record["text"] and VAGUE.match(record["text"]) and not question.id.startswith("clarify:"):
        record["text"] = ""
        first.insert(0, f"clarify:{question.id}")
    return first, last


def build_profile(answers: list[dict], context: GrillContext) -> GrillProfile:
    by_id = {a["id"]: a for a in answers}
    keywords: list[str] = []
    exclusions: list[str] = []

    def add(target: list[str], values) -> None:
        for value in values:
            value = value.strip()
            if value and value.lower() not in {v.lower() for v in target}:
                target.append(value)

    for answer in answers:
        question = dynamic_question(answer["id"], answers) if answer["id"] in QUESTIONS or answer["id"] == "priorities" else None
        options = {o.id: o for o in question.options} if question else {}
        chosen = [options[o] for o in answer["options"] if o in options]
        if answer["id"] == "exclusions":
            add(exclusions, [k for o in chosen for k in o.keywords])
            add(exclusions, re.split(r"[,;]", answer["text"]))
        elif answer["id"] not in {"domains", "priorities", "depth", "frequency", "model_threshold",
                                  "robotics_angle", "events_where", "research_cats"}:
            add(keywords, [k for o in chosen for k in o.keywords])
            if answer["id"] == "keywords" or answer["id"].startswith("clarify:"):
                add(keywords, re.split(r"[,;]", answer["text"]))

    domains = by_id.get("domains", {}).get("options", [])
    priorities = by_id.get("priorities", {}).get("options") or domains[:3]
    categories = list(by_id.get("research_cats", {}).get("options", []))
    for domain in domains:  # une catégorie arXiv par domaine, si pertinente
        if domain in DOMAIN_CATEGORIES and DOMAIN_CATEGORIES[domain] not in categories:
            categories.append(DOMAIN_CATEGORIES[domain])
    suggested = [
        SuggestedSource(name=f"arXiv {category}", url=ARXIV_FEED.format(category=category),
                        reason="catégorie liée à tes domaines, absente de sources.toml")
        for category in categories
        if ARXIV_FEED.format(category=category) not in context.configured_feeds
    ]
    if "events" in domains:
        suggested.append(SuggestedSource(
            name="Pages officielles des événements choisis", url="",
            reason="aucune source d'événements configurée : à ajouter après vérification (skill ajout-source)",
        ))

    summary = ""
    if context.llm is not None:
        # Libellés (et non identifiants) : sinon le LLM recopie « quant », « models »… comme mots-clés.
        lines = []
        for a in answers:
            if a["skipped"]:
                continue
            question = dynamic_question(a["id"], answers)
            labels = {o.id: o.label for o in question.options}
            chosen = ", ".join(labels.get(o, o) for o in a["options"]) or "—"
            lines.append(f"- {a['question']} → {chosen}{' ; ' + a['text'] if a['text'] else ''}")
        try:
            synthesis = context.llm.generate(render_prompt("grill", answers="\n".join(lines)), GrillSynthesis)
            summary = synthesis.summary
            add(keywords, [k for k in synthesis.extra_keywords[:8]
                           if len(k.strip()) > 3 and k.strip().lower() not in OPTION_IDS])
        except (LLMOutputError, BudgetExceeded):
            summary = ""
    if not summary:
        labels = {o.id: o.label for o in DOMAINS.options}
        summary = ("Priorités : " + ", ".join(labels.get(p, p) for p in priorities) + ". "
                   + (f"À écarter : {', '.join(exclusions[:5])}." if exclusions else ""))

    return GrillProfile(
        domains=domains, priorities=priorities, keywords=keywords[:25], exclusions=exclusions,
        arxiv_categories=categories, suggested_sources=suggested,
        depth=(by_id.get("depth", {}).get("options") or ["summary"])[0],
        frequency=(by_id.get("frequency", {}).get("options") or ["daily"])[0],
        summary=summary.strip(),
    )
