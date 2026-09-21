import os

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

_API = "https://api.github.com"


def _headers():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def publish_dashboard(week_start, html_path):
    """
    Upload the HTML dashboard as a secret GitHub Gist.
    Returns an htmlpreview.github.io URL that renders it fully.
    """
    user = os.getenv("GITHUB_USER")
    if not user:
        raise RuntimeError("GITHUB_USER not set")

    from pathlib import Path
    html = Path(html_path).read_text()
    filename = f"fintwit-{week_start}.html"

    resp = requests.post(
        f"{_API}/gists",
        headers=_headers(),
        json={
            "description": f"Fintwit Weekly — {week_start}",
            "public": False,
            "files": {filename: {"content": html}},
        },
        timeout=30,
    )
    resp.raise_for_status()
    gist_id = resp.json()["id"]

    raw_url = f"https://gist.githubusercontent.com/{user}/{gist_id}/raw/{filename}"
    return f"https://htmlpreview.github.io/?{raw_url}"
