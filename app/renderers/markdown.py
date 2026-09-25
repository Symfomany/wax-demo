from app.workflow.evidence import STATUS_LABELS


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
            ]
        )
        if item.get("confidence") is not None:
            lines.extend([f"**Confiance : {item['confidence']}/100** — {' · '.join(item.get('confidence_reasons', []))}", ""])
        if item.get("facts"):
            lines.append("**Faits (avec preuve) :**")
            for fact in item["facts"]:
                missing = fact.get("missing_protocol", [])
                lines.append(f"- [{STATUS_LABELS[fact['status']]}, {fact['confidence']}/100] {fact['text']} "
                             f"— « {fact['evidence'][0]['quote']} »"
                             + (f" *(protocole incomplet : {', '.join(missing)})*" if missing else ""))
            lines.append("")
        if item.get("analysis"):
            lines.extend([f"**Analyse (interprétation) :** {item['analysis']}", ""])
        if item.get("hypothesis"):
            lines.extend([f"**Hypothèse (non vérifiée) :** {item['hypothesis']}", ""])
        lines.extend([f"**Pourquoi c’est important :** {item['why_it_matters']}", ""])
        if item.get("impact_reasons"):
            lines.extend([f"*Pour toi : {' · '.join(item['impact_reasons'])}*", ""])
        if item.get("score") is not None:
            lines.extend([f"*Classement : {item['score']:.0f}/100 — {' · '.join(item.get('rank_reasons', []))}*", ""])

    if digest.get("contradictions"):
        lines.extend(["## Contradictions entre sources", ""])
        for c in digest["contradictions"]:
            lines.append(f"- {c['claim']} — « {c['quote']} » contre « {c['other_quote']} » ({c['other_url']})")
        lines.append("")

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
