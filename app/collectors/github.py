from datetime import datetime, timezone

import httpx
from pydantic import ValidationError
from rich import print

from app.schemas import Document


def collect_github_releases(
    repositories: list[str],
    token: str | None = None,
    per_repo: int = 3,
) -> list[Document]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    documents: list[Document] = []

    with httpx.Client(headers=headers, timeout=20) as client:
        for repository in repositories:
            try:
                response = client.get(
                    f"https://api.github.com/repos/{repository}/releases",
                    params={"per_page": per_repo},
                )
                response.raise_for_status()
            except httpx.HTTPError as error:
                # 403/429 = quota anonyme (60 req/h) atteint : définir GITHUB_TOKEN
                print(f"[yellow]GitHub ignoré ({repository}) : {error}[/yellow]")
                continue

            for release in response.json()[:per_repo]:
                if release.get("draft"):
                    continue
                published_at = release.get("published_at")
                body = (release.get("body") or "")[:4000]

                try:
                    documents.append(
                        Document(
                            source="github",
                            title=f"{repository}: {release['name'] or release['tag_name']}",
                            url=release["html_url"],
                            published_at=(
                                datetime.fromisoformat(
                                    published_at.replace("Z", "+00:00")
                                ).astimezone(timezone.utc)
                                if published_at
                                else None
                            ),
                            summary=body,
                            content=body,
                            tags=[
                                "github",
                                repository,
                                *(["prerelease"] if release.get("prerelease") else []),
                            ],
                        )
                    )
                except ValidationError:
                    continue

    return documents
