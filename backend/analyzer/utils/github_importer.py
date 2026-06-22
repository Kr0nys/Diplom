"""
Скачивание публичных репозиториев GitHub как zip для анализа.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import requests

GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
USER_AGENT = "python-test-gen/1.0"

# owner/repo, optional .git, optional /tree/ref/...
_GITHUB_PATH_RE = re.compile(
    r"^(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?(?:/)?(?:tree/(?P<ref>[^/?#]+))?(?:/.*)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GitHubRepoRef:
    owner: str
    repo: str
    ref: Optional[str]
    original_url: str


class GitHubImportError(ValueError):
    pass


def parse_github_url(url: str) -> GitHubRepoRef:
    raw = (url or "").strip()
    if not raw:
        raise GitHubImportError("Укажите ссылку на репозиторий GitHub.")

    if raw.startswith("github.com/") or raw.startswith("www.github.com/"):
        raw = "https://" + raw

    if not raw.startswith(("http://", "https://")):
        raise GitHubImportError("Ссылка должна начинаться с https://github.com/…")

    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if host not in GITHUB_HOSTS:
        raise GitHubImportError("Поддерживаются только ссылки на github.com.")

    path = (parsed.path or "").strip("/")
    m = _GITHUB_PATH_RE.match(path)
    if not m:
        raise GitHubImportError(
            "Не удалось разобрать ссылку. Пример: https://github.com/owner/repo или …/tree/main"
        )

    owner = m.group("owner")
    repo = m.group("repo")
    if repo.endswith(".git"):
        repo = repo[:-4]

    ref = (m.group("ref") or "").strip() or None
    return GitHubRepoRef(owner=owner, repo=repo, ref=ref, original_url=raw)


def _default_branch(owner: str, repo: str, timeout: int) -> str:
    api = f"https://api.github.com/repos/{owner}/{repo}"
    r = requests.get(
        api,
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
        timeout=timeout,
    )
    if r.status_code == 404:
        raise GitHubImportError("Репозиторий не найден или недоступен (private?).")
    if r.status_code != 200:
        raise GitHubImportError(f"GitHub API вернул {r.status_code}: не удалось получить ветку по умолчанию.")
    data = r.json()
    branch = (data.get("default_branch") or "main").strip()
    return branch or "main"


def _archive_candidate_urls(owner: str, repo: str, ref: str) -> List[str]:
    ref = ref.strip()
    if re.fullmatch(r"[0-9a-f]{7,40}", ref, re.IGNORECASE):
        return [f"https://github.com/{owner}/{repo}/archive/{ref}.zip"]
    return [
        f"https://github.com/{owner}/{repo}/archive/refs/heads/{ref}.zip",
        f"https://github.com/{owner}/{repo}/archive/refs/tags/{ref}.zip",
    ]


def download_github_repo_zip(
    url: str,
    *,
    ref_override: Optional[str] = None,
    timeout: Optional[int] = None,
    max_bytes: Optional[int] = None,
) -> Tuple[bytes, GitHubRepoRef, str]:
    """
    Возвращает (zip_bytes, parsed, resolved_ref).
    """
    parsed = parse_github_url(url)
    timeout = int(timeout or os.environ.get("GITHUB_DOWNLOAD_TIMEOUT") or "120")
    max_bytes = int(max_bytes or os.environ.get("GITHUB_DOWNLOAD_MAX_MB") or "80") * 1024 * 1024

    ref = (ref_override or parsed.ref or "").strip() or _default_branch(parsed.owner, parsed.repo, timeout)

    last_err = "не удалось скачать архив"
    for zip_url in _archive_candidate_urls(parsed.owner, parsed.repo, ref):
        try:
            with requests.get(
                zip_url,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
                stream=True,
                allow_redirects=True,
            ) as r:
                if r.status_code == 404:
                    last_err = f"ветка/тег «{ref}» не найден"
                    continue
                if r.status_code != 200:
                    last_err = f"GitHub вернул HTTP {r.status_code}"
                    continue

                cl = r.headers.get("Content-Length")
                if cl and cl.isdigit() and int(cl) > max_bytes:
                    raise GitHubImportError(
                        f"Архив слишком большой (>{max_bytes // (1024 * 1024)} МБ). "
                        "Загрузите zip вручную или укажите меньший репозиторий."
                    )

                chunks: List[bytes] = []
                total = 0
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise GitHubImportError(
                            f"Архив превышает лимит {max_bytes // (1024 * 1024)} МБ."
                        )
                    chunks.append(chunk)

                if total < 32:
                    last_err = "пустой или повреждённый архив"
                    continue

                return b"".join(chunks), parsed, ref
        except GitHubImportError:
            raise
        except requests.RequestException as e:
            last_err = str(e)
            continue

    raise GitHubImportError(
        f"Не удалось скачать репозиторий {parsed.owner}/{parsed.repo} ({last_err}). "
        "Проверьте, что репозиторий публичный и ссылка верна."
    )
