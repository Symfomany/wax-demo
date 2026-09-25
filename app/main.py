import json
import re
import shutil
import subprocess
import sys
from uuid import uuid4

import httpx
import typer
from langgraph.types import Command
from rich import print
from rich.markdown import Markdown
from rich.table import Table

from app import observability, storage
from app.collectors import load_sources
from app.collectors.github_mcp import discover, stdio_connection
from app.config import PROJECT_ROOT, settings
from app.harness.skills import load_skill
from app.memory import WatchMemory
from app.runtime import graph_config, make_context, open_graph, open_store
from app.workflow.graph import initial_state
from app.workflow.tasks import COLLECTORS, default_plan

cli = typer.Typer(no_args_is_help=True)


def show_result(result: dict, run_id: str, context) -> None:
    if result.get("collected"):
        print(f"[green]Nouveaux documents : {result['collected']}[/green]")
    for line in result.get("trace", []):
        print(f"[cyan]supervisor[/cyan] {line}")
    for error in result.get("errors", []):
        print(f"[yellow]⚠ {error}[/yellow]")
    print(
        f"[dim]LLM : {context.llm.calls} appel(s), "
        f"{context.llm.cache_hits} depuis le cache SQLite[/dim]"
    )
    if links := observability.langfuse_links(run_id, run_id):
        print(f"[dim]Langfuse : {links['trace']}[/dim]")

    if "__interrupt__" in result:
        print(Markdown(result["__interrupt__"][0].value["markdown"]))
        print("[yellow]Digest prêt pour validation humaine.[/yellow]")
        print(f"Publier : python -m app.main resume {run_id} --approved --note \"...\"")
        print(f"Rejeter : python -m app.main resume {run_id} --rejected --note \"pourquoi\"")
    elif result.get("violations"):
        print("[red]Publication bloquée par les guards :[/red]")
        for violation in result["violations"]:
            print(f"  - {violation}")
    elif result.get("outputs"):
        print(f"[green]Markdown : {result['outputs']['markdown']}[/green]")
        print(f"[green]JSON : {result['outputs']['json']}[/green]")
    elif result.get("approval") is False:
        print("[yellow]Digest rejeté ; leçon enregistrée en mémoire.[/yellow]")
    else:
        print("[red]Run en échec (voir les erreurs ci-dessus).[/red]")


@cli.command()
def run(
    approve: bool = typer.Option(False, help="Publier sans validation humaine."),
    collect: bool = typer.Option(True, help="--no-collect : réutiliser la mémoire SQLite."),
    mcp: bool = typer.Option(True, help="--no-mcp : ne pas interroger le serveur MCP GitHub."),
    keyword: list[str] = typer.Option([], "--keyword", "-k", help="Mot-clé de focus (répétable)."),
    match_all: bool = typer.Option(False, help="Exiger tous les mots-clés (défaut : au moins un)."),
    source: list[str] = typer.Option([], "--source", "-s", help="rss, arxiv, github_releases, github_mcp (répétable)."),
    max_age: int = typer.Option(0, help="Âge maximal des documents en jours (0 : réglage par défaut)."),
    max_docs: int = typer.Option(0, help="Nombre maximal de candidats (0 : réglage par défaut)."),
    run_id_file: str = typer.Option("", help="Écrit l'identifiant du run dans ce fichier (scripts)."),
):
    """Supervisor → collecte parallèle → research → review → editorial → validation."""
    connection = storage.connect(settings.database_path)
    context = make_context(connection, human_approval=settings.human_approval and not approve)
    names = tuple(
        n for n in COLLECTORS
        if n in context.collectors and (mcp or n != "github_mcp") and (not source or n in source)
    )
    plan = default_plan(collect=collect, collectors=names, evidence=settings.evidence_enabled)
    options = {"keywords": keyword, "match_all": match_all, "max_age_days": max_age, "max_documents": max_docs}
    options = {key: value for key, value in options.items() if value}

    run_id = str(uuid4())
    storage.set_run_status(connection, run_id, "running")
    if run_id_file:
        from pathlib import Path

        Path(run_id_file).write_text(run_id, encoding="utf-8")

    with open_graph(context) as graph:
        try:
            result = graph.invoke(
                initial_state(run_id, plan, settings.min_relevance, options), config=graph_config(run_id)
            )
        except Exception:
            storage.set_run_status(connection, run_id, "failed")
            raise
        finally:
            observability.flush()

    if "__interrupt__" in result:
        storage.set_run_status(connection, run_id, "awaiting_approval")
    show_result(result, run_id, context)


@cli.command()
def resume(
    thread_id: str,
    approved: bool = typer.Option(..., "--approved/--rejected", help="Publier ou rejeter."),
    note: str = typer.Option("", help="Leçon mémorisée pour les prochains runs."),
):
    """Reprend un run en attente de validation humaine."""
    connection = storage.connect(settings.database_path)
    context = make_context(connection, human_approval=True, collectors={})

    with open_graph(context) as graph:
        config = graph_config(thread_id)
        if not graph.get_state(config).next:
            print(f"[red]Aucun run en attente pour {thread_id}.[/red]")
            raise typer.Exit(1)
        result = graph.invoke(Command(resume={"approved": approved, "note": note}), config=config)
        observability.flush()

    # Trace, erreurs et collecte appartiennent au run initial : déjà affichés.
    show_result({**result, "trace": [], "errors": [], "collected": {}}, thread_id, context)


@cli.command()
def plan(langgraph: bool = typer.Option(False, help="Afficher aussi le graphe LangGraph.")):
    """Affiche le Task Graph (Mermaid) et, en option, le graphe LangGraph compilé."""
    task_graph = default_plan()
    print("[bold]Ordre topologique :[/bold] " + " → ".join(task_graph.topological_order()))
    print(task_graph.to_mermaid())
    if langgraph:
        connection = storage.connect(settings.database_path)
        context = make_context(connection, human_approval=True, collectors={})
        with open_graph(context) as graph:
            print(graph.get_graph().draw_mermaid())


@cli.command()
def github(query: str, limit: int = typer.Option(5, help="Nombre de dépôts.")):
    """Recherche de dépôts via le serveur MCP github-scout (démo de l'outil MCP)."""
    import asyncio

    audit: list[str] = []
    documents = asyncio.run(
        discover(
            [query],
            stdio_connection(settings.github_token, settings.github_api_url),
            max_repos=limit,
            per_query=limit,
            audit_log=audit,
        )
    )
    for call in audit:
        print(f"[dim]MCP {call}[/dim]")
    for document in documents:
        print(f"[bold]{document.title}[/bold]\n  {document.url}\n  {document.summary[:200]}")


@cli.command()
def memory(export: bool = typer.Option(False, help="Exporter vers la mémoire Claude Code.")):
    """Affiche les trois niveaux de mémoire : SQLite, Store LangGraph, export Claude."""
    connection = storage.connect(settings.database_path)
    table = Table("Table SQLite", "Lignes")
    for name, count in storage.memory_stats(connection).items():
        table.add_row(name, str(count))
    print(table)
    print(f"Schéma SQLite : v{storage.schema_version(connection)}")

    with open_store() as store:
        watch_memory = WatchMemory(store)
        print("[bold]Store LangGraph — contexte injecté dans les prompts :[/bold]")
        print(watch_memory.prompt_context())
        health = watch_memory.source_health()
        if health:
            print("[bold]Santé des sources :[/bold] " + json.dumps(
                {name: f"{v['ok']} OK / {v['failures']} KO" for name, v in health.items()},
                ensure_ascii=False,
            ))
        if export:
            path = watch_memory.export_markdown(settings.claude_memory_path)
            print(f"[green]Mémoire Claude Code exportée : {path}[/green]")


@cli.command()
def why(as_json: bool = typer.Option(False, "--json", help="Sortie JSON brute.")):
    """Pourquoi cette veille ? Profil d'impact, mémoire typée, règles, ranking (aussi sur /why)."""
    from app.why import why_payload

    connection = storage.connect(settings.database_path)
    with open_store() as store:
        payload = why_payload(connection, store)
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return
    profile = payload["profile"]
    print(f"[bold]Profil d'impact :[/bold] {profile['label'] if profile else 'aucun'}")
    if profile:
        print("  Priorités : " + ", ".join(profile["priorities"]["topics"]))
        print("  Matériel : " + ", ".join([*profile["hardware"]["gpus"], *profile["hardware"]["platforms"]]))
    table = Table("Type", "Statut", "Contenu", "Provenance", "Confiance", "Expire")
    for record in payload["records"]:
        table.add_row(record["kind"], record["status"], record["content"][:70], record["provenance"],
                      f"{record['confidence']:.2f}", (record["expires_at"] or "—")[:10])
    print(table)
    for item in (payload["last_digest"] or {}).get("items", []):
        print(f"• {item['title'][:80]} — score {item['score']}, confiance {item['confidence']}")
        for reason in item.get("impact_reasons") or []:
            print(f"    🎯 {reason}")


@cli.command()
def rules(
    accept: str = typer.Option(None, help="Accepter une règle suggérée (clé, ex. exclude:robotics)."),
    dismiss: str = typer.Option(None, help="Écarter une règle suggérée."),
):
    """Règles suggérées après des rejets récurrents : lister, accepter ou écarter."""
    with open_store() as store:
        memory = WatchMemory(store)
        if accept or dismiss:
            try:
                record = memory.decide_rule(accept or dismiss, accept is not None)
            except KeyError:
                print(f"[red]Règle inconnue : {accept or dismiss}[/red]")
                raise typer.Exit(1)
            print(f"[green]{record.key} → {record.status}[/green]")
            if settings.claude_memory_path:
                memory.export_markdown(settings.claude_memory_path)
            return
        table = Table("Clé", "Statut", "Règle", "Provenance", "Confiance")
        for record in memory.rules():
            table.add_row(record.key, record.status, record.content, record.provenance, f"{record.confidence:.2f}")
        print(table)


@cli.command()
def doctor():
    """Vérifie Ollama, le modèle, le GPU, les sources, le MCP, la mémoire et les secrets."""
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        mark = "[green]OK[/green]" if passed else "[red]KO[/red]"
        print(f"{mark}  {label}" + (f" — {detail}" if detail else ""))

    if settings.llm_provider != "ollama":
        target = "API Claude native" if settings.llm_provider == "anthropic" else settings.llm_base_url
        check(f"Fournisseur LLM {settings.llm_provider}", True, f"{settings.llm_model} · {target}")
    else:
        try:
            tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=5).json()
            models = [model["name"] for model in tags.get("models", [])]
            check("Ollama joignable", True, settings.ollama_url)
            present = settings.llm_model in models or f"{settings.llm_model}:latest" in models
            check(
                f"Modèle {settings.llm_model}",
                present,
                "" if present else f"absent ; disponibles : {', '.join(models)} "
                "→ ajuster LLM_MODEL dans .env",
            )
        except httpx.HTTPError as error:
            check("Ollama joignable", False, f"{error} → lancer `ollama serve`")

    if shutil.which("nvidia-smi"):
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        check("GPU", bool(gpu), gpu)

    sources = load_sources(settings.sources_path)
    check(
        "Sources déclarées",
        True,
        f"{len(sources.get('rss', []))} RSS, {len(sources.get('arxiv', {}).get('feeds', []))} arXiv, "
        f"{len(sources.get('github', {}).get('repositories', []))} dépôts GitHub, "
        f"{len(sources.get('github_mcp', {}).get('queries', []))} requêtes MCP",
    )

    try:
        import asyncio

        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(
            {"github": stdio_connection(settings.github_token, settings.github_api_url)}
        )
        tools = asyncio.run(client.get_tools())
        check("Serveur MCP github-scout", bool(tools), ", ".join(tool.name for tool in tools))
    except Exception as error:  # noqa: BLE001 — diagnostic
        check("Serveur MCP github-scout", False, str(error))

    skill = load_skill(settings.skill_path)
    check("Skill chargé", bool(skill.section("Critères de priorité")), skill.name)

    connection = storage.connect(settings.database_path)
    version = storage.schema_version(connection)
    check("Migrations SQLite", version == len(storage.MIGRATIONS), f"v{version}")
    with open_store():
        check("Store LangGraph", True, str(settings.memory_db_path))

    gitignore = PROJECT_ROOT / ".gitignore"
    check(
        ".env ignoré par Git",
        gitignore.exists() and ".env" in gitignore.read_text().splitlines(),
    )
    if settings.web_login_enabled:
        from app.web.auth import AuthConfigError
        from app.web.server import build_authenticator

        try:
            check("Connexion web (login)", True, f"utilisateur {build_authenticator().username}")
        except AuthConfigError as error:
            check("Connexion web (login)", False, str(error))
    elif settings.web_host not in ("127.0.0.1", "localhost", "::1"):
        check("Connexion web (login)", False, f"interface exposée sur {settings.web_host} sans WEB_USERNAME / WEB_PASSWORD")
    if settings.notion_enabled:
        from app.notion import check_access

        check("Notion", *check_access(settings.notion_token, settings.notion_parent_page_id, settings.notion_api_url))
    else:
        check("Notion", True, "non configuré (NOTION_TOKEN, NOTION_PARENT_PAGE_ID)")
    check("Actus : recherche web Claude", True,
          f"{settings.news_model} + {settings.news_search_tool}"
          + (f" · workspace {settings.claude_workspace_id}" if settings.claude_workspace_id else "")
          if settings.claude_search_key
          else "clé absente (CLAUDE_API dans .env) — le crawl des blogs fonctionne sans")
    check(
        "Langfuse",
        True,
        "activé" if settings.langfuse_enabled else "désactivé (LANGFUSE_ENABLED=false)",
    )

    raise typer.Exit(0 if ok else 1)


@cli.command()
def pending():
    """Liste les veilles en attente de validation humaine."""
    connection = storage.connect(settings.database_path)
    rows = connection.execute(
        "SELECT run_id, started_at FROM runs WHERE status = 'awaiting_approval' ORDER BY started_at DESC"
    ).fetchall()
    if not rows:
        print("[green]Aucune veille en attente.[/green]")
    for run_id, started_at in rows:
        typer.echo(f"{run_id}\t{started_at}")


@cli.command()
def show(run_id: str):
    """Affiche le digest d'une veille en attente de validation."""
    connection = storage.connect(settings.database_path)
    context = make_context(connection, human_approval=True, collectors={})
    with open_graph(context) as graph:
        snapshot = graph.get_state({"configurable": {"thread_id": run_id}})
    if not snapshot.next or not snapshot.tasks or not snapshot.tasks[0].interrupts:
        print(f"[red]Aucune veille en attente pour {run_id}.[/red]")
        raise typer.Exit(1)
    print(Markdown(snapshot.tasks[0].interrupts[0].value["markdown"]))


@cli.command()
def grill():
    """Grill-me : entretien dans le terminal pour cerner ce que tu cherches en actu IA."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    from app.collectors import load_sources
    from app.grill import GrillContext, build_grill_graph, resume_payload
    from app.llm import get_llm

    connection = storage.connect(settings.database_path)
    sources = load_sources(settings.sources_path)
    feeds = {s["url"] for s in sources.get("rss", [])} | set(sources.get("arxiv", {}).get("feeds", []))
    with open_store() as store:
        graph = build_grill_graph(
            GrillContext(llm=get_llm(connection), configured_feeds=feeds), InMemorySaver(), store
        )
        config = {"configurable": {"thread_id": "grill-cli"}}
        result = graph.invoke({"queue": ["domains"]}, config)
        while "__interrupt__" in result:
            question = result["__interrupt__"][0].value
            print(f"\n[bold cyan]{question['branch']}[/bold cyan] · [bold]{question['text']}[/bold]")
            print(f"[dim]{question['why']}[/dim]")
            ids = [o["id"] for o in question["options"]]
            for index, option in enumerate(question["options"], start=1):
                mark = " [green](recommandé)[/green]" if option["id"] in question["recommended"] else ""
                print(f"  {index}. {option['label']}{mark}")
            raw = typer.prompt(
                "Numéros séparés par des virgules, texte libre après « / », Entrée = recommandation, « - » = passer",
                default="", show_default=False,
            )
            reply = parse_grill_reply(raw, ids)
            result = graph.invoke(Command(resume=resume_payload(reply)), config)
        profile = result["profile"]
        WatchMemory(store).export_markdown(settings.claude_memory_path)
    print_grill_profile(profile)


def parse_grill_reply(raw: str, ids: list[str]) -> dict:
    raw = raw.strip()
    if not raw:
        return {"recommended": True}
    if raw == "-":
        return {"options": [], "text": ""}
    numbers, _, text = raw.partition("/")
    options = [ids[int(n) - 1] for n in re.findall(r"\d+", numbers) if 0 < int(n) <= len(ids)]
    if not options and not text and not re.search(r"\d", numbers):
        text = numbers  # texte libre sans numéro
    return {"options": options, "text": text.strip()}


def print_grill_profile(profile: dict) -> None:
    print("\n[bold green]Profil enregistré dans la mémoire de veille[/bold green]")
    print(profile["summary"])
    print(f"[bold]Priorités :[/bold] {', '.join(profile['priorities'])}")
    print(f"[bold]Mots-clés :[/bold] {', '.join(profile['keywords'])}")
    print(f"[bold]À écarter :[/bold] {', '.join(profile['exclusions'])}")
    for source in profile["suggested_sources"]:
        print(f"[dim]Source suggérée : {source['name']} {source['url']} — {source['reason']}[/dim]")
    if profile["keywords"]:
        focus = " ".join(f"-k {json.dumps(k, ensure_ascii=False)}" for k in profile["keywords"][:5])
        print(f"[dim]Veille ciblée : python -m app.main run {focus}[/dim]")


@cli.command("grill-save")
def grill_save(profile_json: str = typer.Argument(..., help="Profil JSON (ou @fichier.json).")):
    """Enregistre un profil Grill-me produit ailleurs (ex. par le skill Claude Code grill-me)."""
    from pathlib import Path

    from app.grill import GrillProfile

    raw = Path(profile_json[1:]).read_text(encoding="utf-8") if profile_json.startswith("@") else profile_json
    profile = GrillProfile.model_validate_json(raw).model_dump()
    with open_store() as store:
        WatchMemory(store).set_interests(profile)
        WatchMemory(store).export_markdown(settings.claude_memory_path)
    print_grill_profile(profile)


@cli.command("notion-sync")
def notion_sync_command():
    """Publie la page Notion des dernières veilles via le serveur MCP notion-veille."""
    from app.runtime import notion_sync

    sync = notion_sync()
    if sync is None:
        print("[red]Notion non configuré : NOTION_TOKEN et NOTION_PARENT_PAGE_ID requis (.env).[/red]")
        raise typer.Exit(1)
    audit: list[str] = []
    result = sync(storage.connect(settings.database_path), audit_log=audit)
    for call in audit:
        print(f"[dim]MCP {call}[/dim]")
    print(f"[green]Page Notion ({result['digests']} veille(s), {result['blocks']} blocs) : {result['url']}[/green]")
    if result["archived"]:
        print(f"[dim]Ancienne page archivée : {result['archived']}[/dim]")


@cli.command()
def report(run_id: str = typer.Option("", help="Run à rendre (défaut : dernière veille publiée).")):
    """(Re)génère le rapport Markdown daté d'une veille publiée."""
    from app.publishers import Publication, publish_report

    connection = storage.connect(settings.database_path)
    records = [r for r in storage.recent_digests(connection, limit=50) if not run_id or r["run_id"] == run_id]
    if not records:
        print("[red]Aucune veille publiée correspondante.[/red]")
        raise typer.Exit(1)
    record = records[0]
    row = connection.execute("SELECT stats_json FROM runs WHERE run_id = ?", (record["run_id"],)).fetchone()
    stats = json.loads(row[0]) if row else {}
    outputs = publish_report(Publication(
        run_id=record["run_id"], digest=record["digest"], critiques=[], connection=connection,
        output_dir=settings.output_dir, reports_dir=settings.reports_dir,
        templates_dir=settings.templates_dir, model=settings.llm_model,
        collected=stats.get("collected", {}), trace=stats.get("trace", []),
    ))
    print(f"[green]Rapport : {outputs['report']}[/green]")
    print(f"[green]HTML : {outputs['report_html']}[/green]")


@cli.command()
def instructions(
    target: str = typer.Argument("claude-md", help="claude-md | demande | chat"),
    profile: str = typer.Option("templates/claude/profil.toml", help="Profil de veille (TOML)."),
    output: str = typer.Option("", help="Fichier de sortie (défaut : affichage)."),
    period: str = typer.Option("7 derniers jours", help="Période (cible « demande »)."),
):
    """Génère des instructions Claude à partir des templates et d'un profil de veille."""
    from pathlib import Path

    from app.instructions import render_instructions

    text = render_instructions(target, PROJECT_ROOT / profile, period=period)
    if output:
        Path(output).write_text(text, encoding="utf-8")
        print(f"[green]Instructions écrites : {output}[/green]")
    else:
        typer.echo(text)


source_cli = typer.Typer(help="Sources de la veille : lister, ajouter par URL (vérifiée), retirer.")
cli.add_typer(source_cli, name="source")


@source_cli.command("list")
def source_list():
    """Sources déclarées dans sources.toml."""
    from app import sources_admin

    current = sources_admin.list_sources()
    table = Table("Type", "Source")
    for item in current["rss"]:
        table.add_row("rss", f"{item['name']} — {item['url']}")
    for kind in ("arxiv", "github", "github_mcp"):
        for value in current[kind]:
            table.add_row(kind, value)
    for item in current["blog"]:
        table.add_row("blog (HTML)", f"{item['name']} — {item['url']}")
    print(table)


@source_cli.command("add")
def source_add(
    url: str = typer.Argument(..., help="Blog, flux RSS/Atom, dépôt GitHub ou catégorie arXiv."),
    name: str = typer.Option("", help="Nom affiché (flux RSS)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Ajouter sans demander confirmation."),
):
    """Vérifie une source (skill ajout-source) puis l'ajoute à sources.toml après confirmation."""
    from app import sources_admin

    try:
        candidate = sources_admin.inspect_source(url)
    except sources_admin.SourceError as error:
        print(f"[red]✗ {error}[/red]")
        raise typer.Exit(1)
    print(f"[green]✓ {candidate.label}[/green] : [bold]{candidate.name}[/bold] → {candidate.value}")
    print(f"  {candidate.entries} entrée(s) · dernière : {candidate.sample_date or '?'} — {candidate.sample_title}")
    for warning in candidate.warnings:
        print(f"  [yellow]⚠ {warning}[/yellow]")
    if candidate.already_present:
        print("[yellow]Déjà présente dans sources.toml : rien à faire.[/yellow]")
        return
    if not yes and not typer.confirm("Ajouter cette source à sources.toml ?"):
        raise typer.Exit(1)
    try:
        sources_admin.add_source(candidate, name or None)
    except sources_admin.SourceError as error:
        print(f"[red]{error}[/red]")
        raise typer.Exit(1)
    print("[green]Source ajoutée : elle sera collectée à la prochaine veille.[/green]")


@source_cli.command("remove")
def source_remove(
    kind: str = typer.Argument(..., help="rss | arxiv | github"),
    value: str = typer.Argument(..., help="URL du flux ou owner/repo."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Retirer sans demander confirmation."),
):
    """Retire une source de sources.toml (après confirmation)."""
    from app import sources_admin

    if kind not in {"rss", "arxiv", "github"}:
        print("[red]Type attendu : rss, arxiv ou github[/red]")
        raise typer.Exit(2)
    if not yes and not typer.confirm(f"Retirer {value} de sources.toml ?"):
        raise typer.Exit(1)
    try:
        sources_admin.remove_source(kind, value)
    except sources_admin.SourceError as error:
        print(f"[red]{error}[/red]")
        raise typer.Exit(1)
    print(f"[green]Source retirée : {value}[/green]")


news_cli = typer.Typer(help="Actus en cartes : crawl des blogs (claude.com/blog, OpenAI News…) et recherche web Claude.")
cli.add_typer(news_cli, name="news")


def _news_table(items: list[dict]) -> Table:
    table = Table("Date", "Source", "Titre", "URL")
    for item in items:
        table.add_row(item.get("published_at") or "?", item["source"], item["title"][:70], item["url"])
    return table


@news_cli.command("crawl")
def news_crawl():
    """Crawle les pages de blog et les flux d'actus déclarés dans sources.toml, puis enregistre les actus."""
    from app.news import crawl_all

    report = crawl_all(load_sources(settings.sources_path))
    connection = storage.connect(settings.database_path)
    storage.save_news(connection, [item.record() for item in report.items])
    for name, count in report.counts.items():
        print(f"[green]✓ {name}[/green] : {count} actu(s)")
    for name, error in report.errors.items():
        print(f"[red]✗ {name}[/red] : {error}")
    if not report.items:
        raise typer.Exit(1)


@news_cli.command("search")
def news_search(
    topics: str = typer.Argument("", help="Sujets séparés par des virgules (vide : profil Grill-me)."),
    days: int = typer.Option(settings.news_search_days, help="Fenêtre de recherche en jours."),
):
    """Recherche web par l'API Claude (outil web_search, clé CLAUDE_API) ; seules les URL trouvées sont gardées."""
    from app.news import NewsError, interest_topics, search_news

    with open_store() as store:
        memory = WatchMemory(store)
        wanted, exclusions = interest_topics(memory.interests())
        context = memory.prompt_context()
    if topics.strip():
        wanted = "\n".join(f"- {t.strip()}" for t in topics.split(",") if t.strip())
    print(f"[cyan]🌐 {settings.news_model} + web_search ({days} j)…[/cyan]")
    try:
        report = search_news(wanted, exclusions, context, days=days)
    except NewsError as error:
        print(f"[red]✗ {error}[/red]")
        raise typer.Exit(1)
    except Exception as error:  # noqa: BLE001 — erreur de l'API Claude, message actionnable
        from app.news import explain_api_error

        print(f"[red]✗ {explain_api_error(error)}[/red]")
        raise typer.Exit(1)
    connection = storage.connect(settings.database_path)
    storage.save_news(connection, [item.record() for item in report.items])
    print(_news_table([item.record() for item in report.items]))
    print(f"{len(report.items)} actu(s) · {report.searches} recherche(s) · {report.results_seen} résultats lus"
          + (f" · {len(report.rejected)} écartée(s)" if report.rejected else ""))


@news_cli.command("list")
def news_list(
    source: str = typer.Option("", "--source", "-s", help="Claude Blog, OpenAI News, Web (Claude)…"),
    limit: int = typer.Option(20, help="Nombre d'actus."),
):
    """Dernières actus enregistrées."""
    connection = storage.connect(settings.database_path)
    print(_news_table(storage.list_news(connection, source=source or None, limit=limit)))


@news_cli.command("screenshots")
def news_screenshots(limit: int = typer.Option(settings.screenshot_max_per_run, help="Pages capturées au plus.")):
    """Aperçus des actus sans image : captures par le serveur MCP Playwright (@playwright/mcp)."""
    from app.screenshots import ScreenshotError, capture_news

    connection = storage.connect(settings.database_path)
    candidates = [n for n in storage.list_news(connection, limit=200) if not n.get("image") and not n.get("screenshot")]
    try:
        report = capture_news(candidates, limit=limit)
    except ScreenshotError as error:
        print(f"[red]✗ {error}[/red]")
        raise typer.Exit(1)
    by_id = {n["id"]: n for n in candidates}
    storage.save_news(connection, [{k: v for k, v in by_id[i].items() if k != "fetched_at"}
                                   | {"screenshot": f"/api/news/{i}/screenshot"} for i in report.saved])
    print(f"[green]✓ {len(report.saved)} aperçu(s)[/green] · {report.skipped} déjà illustrée(s)")
    for url, error in report.errors.items():
        print(f"[red]✗ {url}[/red] : {error}")


def _radar_context(topics: str) -> tuple[str, str]:
    from app.news import interest_topics

    with open_store() as store:
        memory = WatchMemory(store)
        wanted, _ = interest_topics(memory.interests())
        context = memory.prompt_context()
    if topics.strip():
        wanted = "\n".join(f"- {t.strip()}" for t in topics.split(",") if t.strip())
    return wanted, context


def _claude(function, **kwargs):
    from app.news import NewsError, explain_api_error

    try:
        return function(**kwargs)
    except NewsError as error:
        print(f"[red]✗ {error}[/red]")
    except Exception as error:  # noqa: BLE001 — erreur de l'API Claude, message actionnable
        print(f"[red]✗ {explain_api_error(error)}[/red]")
    raise typer.Exit(1)


events_cli = typer.Typer(help="Événements IA : conférences, meetups, webinaires (flux .ics + recherche web Claude).")
cli.add_typer(events_cli, name="events")


def _events_table(items: list[dict]) -> Table:
    table = Table("Date", "Type", "Événement", "Lieu", "URL")
    for item in items:
        when = item.get("starts_on") or "à confirmer"
        if item.get("ends_on") and item["ends_on"] != item.get("starts_on"):
            when += f" → {item['ends_on']}"
        table.add_row(when, item["kind"], item["title"], "en ligne" if item.get("online") else item.get("location", ""),
                      item["url"])
    return table


@events_cli.command("search")
def events_search(
    topics: str = typer.Argument("", help="Sujets séparés par des virgules (vide : profil Grill-me)."),
    horizon: int = typer.Option(settings.events_horizon_days, help="Jours à venir couverts."),
):
    """Événements à venir par l'API Claude (web_search) ; dates gardées seulement si présentes dans la page."""
    from app.events import search_events

    wanted, context = _radar_context(topics)
    print(f"[cyan]📅 {settings.news_model} + web_search ({horizon} j à venir)…[/cyan]")
    report = _claude(search_events, topics=wanted, memory=context, horizon=horizon)
    storage.save_events(storage.connect(settings.database_path), [item.record() for item in report.items])
    print(_events_table([item.record() for item in report.items]))
    print(f"{len(report.items)} événement(s) · {report.unverified_dates} date(s) non retrouvée(s) dans la page"
          + (f" · {len(report.rejected)} écarté(s)" if report.rejected else ""))


@events_cli.command("crawl")
def events_crawl():
    """Lit les calendriers .ics déclarés dans sources.toml (sections events)."""
    from app.events import crawl_calendars

    report = crawl_calendars(load_sources(settings.sources_path))
    storage.save_events(storage.connect(settings.database_path), [item.record() for item in report.items])
    print(f"[green]✓ {len(report.items)} événement(s)[/green]")
    for name, error in report.errors.items():
        print(f"[red]✗ {name}[/red] : {error}")


@events_cli.command("list")
def events_list(when: str = typer.Option("upcoming", help="upcoming | past | all"),
                limit: int = typer.Option(30, help="Nombre d'événements.")):
    """Événements enregistrés (à venir d'abord)."""
    print(_events_table(storage.list_events(storage.connect(settings.database_path), when, limit=limit)))


media_cli = typer.Typer(help="Vidéos, podcasts et émissions IA (flux media de sources.toml + recherche web Claude).")
cli.add_typer(media_cli, name="media")


def _media_table(items: list[dict]) -> Table:
    table = Table("Date", "Type", "Titre", "Chaîne / émission", "URL")
    for item in items:
        table.add_row(item.get("published_at") or "—", "🎬" if item["kind"] == "video" else "🎙️", item["title"],
                      item["source"], item["url"])
    return table


@media_cli.command("search")
def media_search(
    topics: str = typer.Argument("", help="Sujets séparés par des virgules (vide : profil Grill-me)."),
    days: int = typer.Option(settings.media_search_days, help="Fenêtre de recherche en jours."),
):
    """Meilleures vidéos et podcasts récents par l'API Claude (web_search)."""
    from app.media import search_media

    wanted, context = _radar_context(topics)
    print(f"[cyan]🎬 {settings.news_model} + web_search ({days} j)…[/cyan]")
    report = _claude(search_media, topics=wanted, memory=context, days=days)
    storage.save_media(storage.connect(settings.database_path), [item.record() for item in report.items])
    print(_media_table([item.record() for item in report.items]))


@media_cli.command("crawl")
def media_crawl():
    """Lit les chaînes et podcasts déclarés dans sources.toml (sections media)."""
    from app.media import crawl_media

    report = crawl_media(load_sources(settings.sources_path))
    storage.save_media(storage.connect(settings.database_path), [item.record() for item in report.items])
    for name, count in report.counts.items():
        print(f"[green]✓ {name}[/green] : {count}")
    for name, error in report.errors.items():
        print(f"[red]✗ {name}[/red] : {error}")


@media_cli.command("list")
def media_list(kind: str = typer.Option("", help="video | podcast"), limit: int = typer.Option(20)):
    """Derniers médias enregistrés."""
    print(_media_table(storage.list_media(storage.connect(settings.database_path), kind or None, limit=limit)))


bench_cli = typer.Typer(help="Benchmarks LLM : catalogue et classements de BenchLM.ai, analysés.")
cli.add_typer(bench_cli, name="benchmarks")


@bench_cli.command("crawl")
def benchmarks_crawl():
    """Actualise le catalogue des benchmarks (une page BenchLM)."""
    from app.benchmarks import BenchmarkError, crawl_catalog
    from app.review import FetchError

    try:
        report = crawl_catalog()
    except (BenchmarkError, FetchError) as error:
        print(f"[red]✗ {error}[/red]")
        raise typer.Exit(1)
    storage.save_benchmarks(storage.connect(settings.database_path), [item.record() for item in report.items])
    print(f"[green]✓ {len(report.items)} benchmark(s)[/green] ({report.total_announced} annoncés par BenchLM, "
          f"{report.period}) — source : {settings.benchmarks_url}/benchmarks")


@bench_cli.command("list")
def benchmarks_list(category: str = typer.Option("", help="agentic, coding, reasoning, knowledge, math…"),
                    query: str = typer.Argument("", help="Filtre texte.")):
    """Catalogue des benchmarks enregistrés."""
    table = Table("Benchmark", "Catégorie", "Année", "Description")
    for item in storage.list_benchmarks(storage.connect(settings.database_path), category or None, query or None, limit=60):
        table.add_row(item["name"], item.get("category_name") or item["category"], item.get("year", ""),
                      item.get("description", "")[:120])
    print(table)


@bench_cli.command("show")
def benchmarks_show(key: str = typer.Argument(..., help="Clé BenchLM (ex. draco, aahle).")):
    """Détail d'un benchmark : classement, fiabilité des scores, analyse."""
    from app.benchmarks import BenchmarkError, analyze, fetch_detail
    from app.review import FetchError

    connection = storage.connect(settings.database_path)
    record = storage.get_benchmark(connection, key)
    if record is None:
        print("[red]✗ Benchmark inconnu : lancer d'abord « benchmarks crawl ».[/red]")
        raise typer.Exit(1)
    key = record["key"]
    if storage.benchmark_detail_stale(connection, key, settings.benchmarks_detail_ttl_hours):
        try:
            storage.save_benchmark_detail(connection, key, fetch_detail(key, record.get("slug")).model_dump())
            record = storage.get_benchmark(connection, key)
        except (BenchmarkError, FetchError) as error:
            print(f"[yellow]⚠ {error}[/yellow]")
    print(f"[bold]{record['name']}[/bold] — {record.get('full_name', '')}\n{record.get('description', '')}\n{record['url']}")
    for point in analyze(record, record["detail"])["points"]:
        print(f"• {point}")
    if record["detail"]:
        table = Table("#", "Modèle", "Éditeur", "Type", "Score")
        rows = sorted((r for r in record["detail"]["leaderboard"] if r.get("score") is not None), key=lambda r: -r["score"])
        for rank, row in enumerate(rows[:10], start=1):
            table.add_row(str(rank), row["model"], row["creator"], row["source_type"], f"{row['score']:g}")
        print(table)


@cli.command()
def knowledge(
    query: str = typer.Argument("", help="Recherche dans la base (terme, domaine…)."),
    index: bool = typer.Option(False, "--index", help="Afficher l'index des mots-clés."),
    add: str = typer.Option("", "--add", help="Téléverser un fichier Markdown (validé) dans data/knowledge/."),
    replace: bool = typer.Option(False, help="Avec --add : remplacer un fichier existant."),
):
    """Base de connaissances : glossaire, règles métiers, prompts, notes téléversées."""
    from pathlib import Path

    from app import knowledge as kb

    if add:
        try:
            info = kb.save_upload(Path(add).name, Path(add).read_text(encoding="utf-8"), replace=replace)
        except kb.KnowledgeError as error:
            print(f"[red]{error}[/red]")
            raise typer.Exit(1)
        print(f"[green]{info.name} : {info.entries} entrée(s) ({info.kind}) → {settings.knowledge_uploads_dir}[/green]")
        return
    base = kb.load_knowledge()
    for error in base.errors:
        print(f"[yellow]⚠ {error}[/yellow]")
    if index:
        for item in base.keyword_index():
            print(f"[bold]{item['term']}[/bold] → " + ", ".join(ref["title"] for ref in item["entries"]))
        return
    if query:
        for entry in base.search(query, limit=5):
            print(f"[bold]{entry.title}[/bold] [dim]({entry.kind}{', ' + entry.domain if entry.domain else ''})[/dim]")
            print("  " + ("\n  ".join(f"- {r}" for r in entry.rules) if entry.rules else entry.body[:400]))
            if entry.sources:
                print(f"  [dim]{entry.sources[0]}[/dim]")
        return
    table = Table("Fichier", "Origine", "Type", "Entrées")
    for info in base.files:
        table.add_row(info.name, info.origin, info.kind, str(info.entries))
    print(table)


@cli.command()
def review(
    url: str = typer.Argument(..., help="URL de l'actualité à analyser."),
    as_json: bool = typer.Option(False, "--json", help="Sortie JSON brute (record validé)."),
):
    """Review d'une actualité : scraping, synthèse selon les règles métiers, affirmations citées."""
    from app.llm import get_llm
    from app.review import FetchError, ReviewContext, build_review_graph, dumps, stream_review

    connection = storage.connect(settings.database_path)
    with open_store() as store:
        context = ReviewContext(llm=get_llm(connection), connection=connection,
                                memory=lambda: WatchMemory(store).prompt_context())
        record = None
        try:
            for event in stream_review(build_review_graph(context), {"url": url},
                                       observability.trace_config(url, "review")):
                if event["type"] == "step":
                    print(f"[cyan]reviewer[/cyan] {event['node']} : {event['detail']}")
                else:
                    record = event["review"]
        except FetchError as error:
            print(f"[red]{error}[/red]")
            raise typer.Exit(1)
        finally:
            observability.flush()
    if as_json:
        typer.echo(dumps(record))
        return
    analysis, page = record["analysis"], record["page"]
    labels = {"ok": "✓", "ko": "✗", "na": "–"}
    print(f"\n[bold]{record['title']}[/bold]\n{page['final_url']} · {page['site']} · "
          f"{page.get('published_at') or 'date non précisée'} · domaines : {', '.join(page['domains']) or '—'}")
    print(f"Source {analysis['source_type']} · pertinence {analysis['relevance']}/10 · "
          f"nouveauté {analysis['novelty']}/10 · confiance {analysis['confidence']}/10\n")
    print(Markdown(analysis["summary"] + "\n\n**Pourquoi c'est important** : " + analysis["why_it_matters"]))
    for claim in analysis["claims"]:
        mark = "[green]étayée[/green]" if claim["status"] == "etaye" else "[yellow]non étayée[/yellow]"
        print(f"- {claim['claim']} ({mark})" + (f"\n    [dim]« {claim['quote']} »[/dim]" if claim["quote"] else ""))
    for check in analysis["rule_checks"]:
        print(f"  {labels[check['verdict']]} R{check['rule_id']} {check['rule']} [dim]{check['note']}[/dim]")
    for warning in record["warnings"]:
        print(f"[yellow]🛡 {warning}[/yellow]")
    print(f"[dim]Review {record['id']} — challenge dans l'onglet 🔬 Review de `python -m app.main web`.[/dim]")


@cli.command()
def web(
    host: str = typer.Option(settings.web_host, help="Interface d'écoute."),
    port: int = typer.Option(settings.web_port, help="Port HTTP."),
):
    """Lance l'interface web de chat (http://127.0.0.1:8000)."""
    import uvicorn

    from app.web.auth import AuthConfigError
    from app.web.server import build_authenticator

    try:
        auth = build_authenticator()  # mot de passe faible : refus de démarrer, avant uvicorn
    except AuthConfigError as error:
        print(f"[red]✗ {error}[/red]\n[dim]Générer un mot de passe conforme : python -m app.main web-password[/dim]")
        raise typer.Exit(2)
    if auth is None and host not in ("127.0.0.1", "localhost", "::1") and not settings.web_api_token:
        print(f"[yellow]⚠ Interface exposée sur {host} sans login : définir WEB_USERNAME et WEB_PASSWORD.[/yellow]")
    print(f"[green]Interface de veille : http://{host}:{port}[/green]" + (f" [dim](login : {auth.username})[/dim]" if auth else ""))
    uvicorn.run("app.web.server:app", host=host, port=port, log_level="warning")


@cli.command("web-password")
def web_password(
    length: int = typer.Option(20, min=12, max=64, help="Longueur du mot de passe."),
    write_env: bool = typer.Option(False, "--write-env", help="Ajoute WEB_USERNAME et WEB_PASSWORD à .env."),
    username: str = typer.Option("veille", help="Identifiant (avec --write-env)."),
):
    """Génère un mot de passe conforme pour WEB_PASSWORD ; --write-env l'ajoute à .env (sans rien écraser)."""
    from app.web.auth import generate_password

    password = generate_password(length)
    if not write_env:
        print(password)
        print("[dim]À placer dans .env : WEB_USERNAME=… et WEB_PASSWORD=…, puis bin/veille restart.[/dim]",
              file=sys.stderr)
        return
    env = PROJECT_ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    if any(re.match(r"\s*(WEB_USERNAME|WEB_PASSWORD)\s*=\s*\S", line) for line in lines):
        print("[red]✗ WEB_USERNAME ou WEB_PASSWORD déjà renseigné dans .env : rien n'est modifié.[/red]")
        raise typer.Exit(1)
    with env.open("a", encoding="utf-8") as handle:
        handle.write(("\n" if lines and lines[-1].strip() else "")
                     + f"# Connexion à l'interface web (python -m app.main web-password)\n"
                     f"WEB_USERNAME={username}\nWEB_PASSWORD={password}\n")
    env.chmod(0o600)
    print(f"[green]✓ .env : WEB_USERNAME={username}, WEB_PASSWORD={password}[/green]")
    print("[dim]Notez-le ; modifiable dans .env. Appliquer : bin/veille restart.[/dim]")


if __name__ == "__main__":
    cli()
