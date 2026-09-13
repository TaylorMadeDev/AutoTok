from __future__ import annotations

import html
import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import requests


REDDIT_BASE = "https://www.reddit.com"
USER_AGENT = "windows:autotok-story-maker:1.0 (personal desktop app)"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass(slots=True)
class StoryPost:
    subreddit: str
    title: str
    body: str
    author: str = "anonymous"
    score: int = 0
    comments: int = 0
    permalink: str = ""

    @property
    def word_count(self) -> int:
        return len(self.body.split())


def normalize_subreddit(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^(?:https?://(?:www\.)?reddit\.com/)?r/", "", value, flags=re.I)
    value = value.strip("/ ")
    if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", value):
        raise ValueError("Enter a valid subreddit name, such as AskReddit or TIFU.")
    return value


def clean_reddit_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"(?m)^\s{0,3}(?:#{1,6}|>|[-*+]\s)", "", text)
    text = text.replace("&amp;#x200B;", " ").replace("&#x200B;", " ")
    text = re.sub(r"[*_~`]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _post_from_child(child: dict[str, Any]) -> StoryPost | None:
    data = child.get("data", {})
    body = clean_reddit_text(data.get("selftext", ""))
    if not body or body in {"[removed]", "[deleted]"}:
        return None
    return StoryPost(
        subreddit=f"r/{data.get('subreddit', 'Reddit')}",
        title=clean_reddit_text(data.get("title", "Untitled story")),
        body=body,
        author=data.get("author") or "anonymous",
        score=int(data.get("score") or 0),
        comments=int(data.get("num_comments") or 0),
        permalink=REDDIT_BASE + (data.get("permalink") or ""),
    )


class _RedditBodyParser(HTMLParser):
    """Extract only the post's Markdown body from Reddit's Atom HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = dict(attrs).get("class", "") or ""
        if self.depth:
            if tag == "div":
                self.depth += 1
            if tag in {"p", "br", "li", "blockquote"}:
                self.parts.append("\n")
        elif tag == "div" and "md" in classes.split():
            self.depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            if tag in {"p", "li", "blockquote"}:
                self.parts.append("\n")
            if tag == "div":
                self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)


def _fetch_atom_stories(
    subreddit: str,
    sort: str,
    timeframe: str,
    min_words: int,
    max_words: int,
) -> list[StoryPost]:
    params = {"t": timeframe} if sort == "top" else {}
    response = requests.get(
        f"{REDDIT_BASE}/r/{subreddit}/{sort}/.rss",
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml"},
        timeout=(10, 30),
    )
    if not response.ok:
        raise RuntimeError(f"Reddit returned HTTP {response.status_code} for r/{subreddit}.")
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise RuntimeError("Reddit returned an unreadable story feed.") from exc

    stories: list[StoryPost] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        title = clean_reddit_text(entry.findtext("atom:title", default="", namespaces=ATOM_NS))
        content = entry.findtext("atom:content", default="", namespaces=ATOM_NS)
        parser = _RedditBodyParser()
        parser.feed(content)
        body = clean_reddit_text("".join(parser.parts))
        if not body or body in {"[removed]", "[deleted]"}:
            continue
        author = entry.findtext("atom:author/atom:name", default="anonymous", namespaces=ATOM_NS)
        author = author.removeprefix("/u/").removeprefix("u/")
        link = entry.find("atom:link[@rel='alternate']", ATOM_NS)
        permalink = link.get("href", "") if link is not None else ""
        post = StoryPost(
            subreddit=f"r/{subreddit}", title=title, body=body,
            author=author or "anonymous", permalink=permalink,
        )
        if min_words <= post.word_count <= max_words:
            stories.append(post)
    return stories


def fetch_stories(
    subreddit: str,
    sort: str = "hot",
    timeframe: str = "week",
    min_words: int = 120,
    max_words: int = 900,
    limit: int = 50,
) -> list[StoryPost]:
    subreddit = normalize_subreddit(subreddit)
    sort = sort.lower() if sort.lower() in {"hot", "new", "top", "rising"} else "hot"
    params: dict[str, str | int] = {"limit": max(10, min(limit, 100)), "raw_json": 1}
    if sort == "top":
        params["t"] = timeframe if timeframe in {"hour", "day", "week", "month", "year", "all"} else "week"

    response = requests.get(
        f"{REDDIT_BASE}/r/{subreddit}/{sort}.json",
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=(10, 30),
    )
    if response.status_code in {403, 429}:
        return _fetch_atom_stories(subreddit, sort, timeframe, min_words, max_words)
    if not response.ok:
        raise RuntimeError(f"Reddit returned HTTP {response.status_code} for r/{subreddit}.")

    try:
        children = response.json()["data"]["children"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Reddit returned an unexpected response.") from exc

    stories: list[StoryPost] = []
    for child in children:
        data = child.get("data", {})
        if data.get("stickied") or data.get("over_18"):
            continue
        post = _post_from_child(child)
        if post and min_words <= post.word_count <= max_words:
            stories.append(post)
    return stories


def fetch_random_story(exclude_permalinks: set[str] | None = None, **kwargs: Any) -> StoryPost:
    stories = fetch_stories(**kwargs)
    if exclude_permalinks:
        unseen = [story for story in stories if story.permalink not in exclude_permalinks]
        if unseen:
            stories = unseen
    if not stories:
        raise RuntimeError(
            "No suitable text stories matched that word range. Try a wider range, "
            "another sort, or a story-focused subreddit."
        )
    # Bias toward the top half while keeping results fresh and varied.
    pool = stories[: max(1, (len(stories) + 1) // 2)]
    return random.choice(pool)
