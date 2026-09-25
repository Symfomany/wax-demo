def render_markdown(digest: dict, critiques: list[dict] | None = None) -> str:
    lines = [
        f"# Veille LLM / GenAI — {digest['period_label']}",
        "",
        f"*Généré le {digest['generated_at']}*",
        "",
        "## Synthèse",
        "",
        digest["executive_summary"],
        "",
        "## Signaux retenus",
        "",
    ]

    for item in digest["items"]:
        lines.extend(
            [
                f"### {item['title']}",
                "",
                f"- Source : {item['source']}",
                f"- Date : {item.get('date') or 'Non précisée'}",
                f"- Lien : {item['url']}",
                f"- Tags : {', '.join(item.get('tags', []))}",
                "",
                item["summary"],
                "",
                f"**Pourquoi c’est important :** {item['why_it_matters']}",
                "",
            ]
        )
        if item.get("score") is not None:
            lines.extend([f"*Classement : {item['score']:.0f}/100 — {' · '.join(item.get('rank_reasons', []))}*", ""])

    rejected = [c for c in critiques or [] if c["verdict"] != "keep"]
    if rejected:
        lines.extend(["## Écartés par Critic", ""])
        for critique in rejected:
            risks = "; ".join(critique.get("factual_risks", []))
            lines.append(
                f"- `{critique['verdict']}` {critique['signal_url']} — {critique['rationale']}"
                + (f" *(risques : {risks})*" if risks else "")
            )
        lines.append("")

    return "\n".join(lines)
