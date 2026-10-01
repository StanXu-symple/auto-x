"""Verify and capture one public X post on a temporary Camoufox page."""
from __future__ import annotations

import base64
import hashlib
import html
import re
import struct
import time
import unicodedata
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

MAX_PNG_BYTES = 8 * 1024 * 1024
MAX_CAPTURE_WIDTH = 2000
MAX_CAPTURE_HEIGHT = 16000
CAPTURE_TIMEOUT_SECONDS = 65
_USERNAME = re.compile(r"[A-Za-z0-9_]{1,15}\Z")
_TWEET_ID = re.compile(r"[0-9]{1,32}\Z")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_STATUS = re.compile(r"/([A-Za-z0-9_]{1,15})/status/([0-9]{1,32})/?\Z")
_X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}


class TweetCaptureError(RuntimeError):
    """A post could not be verified/rendered; never include cookies or page HTML."""


class TweetBrowserError(TweetCaptureError):
    """The browser itself failed and the pool should retire its context."""


def canonical_tweet_url(tweet_id: str, username: str) -> str:
    if not _TWEET_ID.fullmatch(tweet_id) or not _USERNAME.fullmatch(username):
        raise TweetCaptureError("Invalid X post identity")
    return f"https://x.com/{username}/status/{tweet_id}"


def _matches_post_url(url: str, tweet_id: str, username: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    match = _STATUS.fullmatch(parsed.path)
    return bool(
        parsed.scheme == "https"
        and parsed.hostname in _X_HOSTS
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and match
        and match.group(1).casefold() == username.casefold()
        and match.group(2) == tweet_id
    )


def normalize_tweet_text(text: str) -> str:
    # API URLs are normally t.co while the DOM displays their expanded labels.
    # The DOM extractor removes only outbound URL anchors; mentions remain text.
    text = unicodedata.normalize("NFC", html.unescape(text))
    def remove_url(match: re.Match[str]) -> str:
        value = match.group(0)
        trimmed = value.rstrip(".,!?;:)]}，。！？；：）】")
        return value[len(trimmed):]

    text = _URL.sub(remove_url, text)
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]", "", text)
    return " ".join(text.split())


def choose_tweet_article(
    candidates: list[dict[str, Any]], tweet_id: str, username: str
) -> int | None:
    """An own timestamp and own author must agree; quote links cannot qualify."""
    matched = [
        int(item["index"])
        for item in candidates
        if str(item.get("username", "")).casefold() == username.casefold()
        and any(
            _matches_post_url(str(url), tweet_id, username)
            for url in item.get("time_urls", [])
        )
    ]
    if len(matched) > 1:
        raise TweetCaptureError("Multiple articles claim the requested X post identity")
    return matched[0] if matched else None


_ARTICLE_METADATA = r"""() => {
  const outbound = a => {
    try {
      const u = new URL(a.href);
      return !['x.com','www.x.com','twitter.com','www.twitter.com'].includes(u.hostname)
        || a.target === '_blank' || !!a.dataset.expandedUrl || u.pathname.startsWith('/i/redirect');
    } catch (_) { return false; }
  };
  return [...document.querySelectorAll('article')].map((article, index) => {
    if (article.parentElement?.closest('article'))
      return {index, username: '', time_urls: [], text: ''};
    const own = el => {
      if (el.closest('article') !== article) return false;
      for (let parent = el.parentElement; parent && parent !== article;
           parent = parent.parentElement) {
        if (parent.dataset.testid === 'quoteTweet'
            || (parent.getAttribute('role') === 'link' && parent.tagName !== 'A')) return false;
      }
      return true;
    };
    const elements = selector => [...article.querySelectorAll(selector)].filter(own);
    const name = elements('[data-testid="User-Name"]')[0];
    let username = '';
    if (name) {
      for (const a of [...name.querySelectorAll('a[href]')].filter(own)) {
        const match = new URL(a.href).pathname.match(/^\/([A-Za-z0-9_]{1,15})\/?$/);
        if (match) { username = match[1]; break; }
      }
    }
    const text = elements('[data-testid="tweetText"]').map(el => {
      const clone = el.cloneNode(true);
      for (const a of clone.querySelectorAll('a[href]')) if (outbound(a)) a.remove();
      for (const img of clone.querySelectorAll('img[alt]')) img.replaceWith(img.alt);
      return clone.textContent || '';
    }).join('\n');
    const time_urls = elements('a[href]').filter(a => a.querySelector('time')).map(a => a.href);
    return {index, username, time_urls, text};
  });
}"""

_MEDIA_READY = r"""article => {
  const ownMedia = el => el.closest('article') === article;
  const photos = [...article.querySelectorAll('[data-testid="tweetPhoto"] img')].filter(ownMedia);
  const images = [...article.querySelectorAll('img[src]')].filter(ownMedia);
  const videos = [...article.querySelectorAll('video')].filter(ownMedia);
  const mainMedia = el => {
    for (let parent = el.parentElement; parent && parent !== article;
         parent = parent.parentElement) {
      if (parent.dataset.testid === 'quoteTweet'
          || (parent.getAttribute('role') === 'link' && parent.tagName !== 'A')) return false;
    }
    return true;
  };
  const videoReady = video => {
    video.pause();
    if (video.readyState >= 2) return true;
    if (!video.poster) return false;
    if (!video._xsentinelPoster) {
      video._xsentinelPoster = new Image();
      video._xsentinelPoster.src = video.poster;
    }
    return video._xsentinelPoster.complete && video._xsentinelPoster.naturalWidth > 0;
  };
  // Wait for quoted images too, but they cannot satisfy the target's media count.
  return {
    media_count: photos.filter(mainMedia).length + videos.filter(mainMedia).length,
    ready: images.every(img => img.complete && img.naturalWidth > 0)
      && videos.every(videoReady),
    fonts_ready: !document.fonts || document.fonts.status === 'loaded'
  };
}"""


_CAPTURE_OBSTRUCTION = r"""() => {
  return [...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')].some(el => {
    const style = getComputedStyle(el), bounds = el.getBoundingClientRect();
    return el.getAttribute('aria-hidden') !== 'true' && style.display !== 'none'
      && style.visibility !== 'hidden' && Number(style.opacity) !== 0
      && bounds.width > 0 && bounds.height > 0;
  });
}"""


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) > MAX_PNG_BYTES or len(data) < 33:
        raise TweetCaptureError("X screenshot PNG size is outside the allowed range")
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise TweetCaptureError("X screenshot is not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    if not (1 <= width <= MAX_CAPTURE_WIDTH and 1 <= height <= MAX_CAPTURE_HEIGHT):
        raise TweetCaptureError("X post is too large to capture safely")
    return width, height


def capture_tweet(
    context: Any,
    *,
    tweet_id: str,
    username: str,
    expected_text: str,
    expected_media_count: int = 0,
) -> dict[str, Any]:
    """Use the context's existing thread; close this page even after failed checks."""
    canonical_url = canonical_tweet_url(tweet_id, username)
    if not isinstance(expected_media_count, int) or not 0 <= expected_media_count <= 20:
        raise TweetCaptureError("Invalid X post media count")
    deadline = time.monotonic() + CAPTURE_TIMEOUT_SECONDS
    page = None
    try:
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 1000})
        page.set_default_timeout(10000)
        page.goto(canonical_url, wait_until="domcontentloaded", timeout=30000)
        if not _matches_post_url(page.url, tweet_id, username):
            raise TweetCaptureError("X navigation did not reach the requested post")
        target_index = None
        candidates: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            candidates = page.evaluate(_ARTICLE_METADATA)
            target_index = choose_tweet_article(candidates, tweet_id, username)
            if target_index is not None:
                break
            page.wait_for_timeout(350)
        if target_index is None:
            raise TweetCaptureError(
                "Requested X post was not found; it may require login or be unavailable"
            )

        target = page.locator("article").nth(target_index)
        target.scroll_into_view_if_needed(timeout=10000)
        # Long posts can render a truncated tweetText even on their detail page.
        expand = target.locator('[data-testid="tweet-text-show-more-link"]')
        if expand.count():
            expand.first.click(timeout=5000)
        # X lazy-loads media as it enters the viewport. Visit the whole bounded
        # article, then return to its top before checking image/font readiness.
        initial_bounds = target.bounding_box()
        if not initial_bounds or not (
            0 < initial_bounds["width"] <= MAX_CAPTURE_WIDTH
            and 0 < initial_bounds["height"] <= MAX_CAPTURE_HEIGHT
        ):
            raise TweetCaptureError("X post is too large or has no visible content")
        top = page.evaluate("() => window.scrollY") + initial_bounds["y"]
        for offset in range(0, int(initial_bounds["height"]) + 1, 800):
            page.evaluate("position => window.scrollTo(0, position)", top + offset)
            page.wait_for_timeout(75)
        page.evaluate("position => window.scrollTo(0, position)", top)
        expected = normalize_tweet_text(expected_text)
        while time.monotonic() < deadline:
            candidates = page.evaluate(_ARTICLE_METADATA)
            target_index = choose_tweet_article(candidates, tweet_id, username)
            if target_index is None:
                raise TweetCaptureError("X post identity changed while rendering")
            target = page.locator("article").nth(target_index)
            actual = normalize_tweet_text(candidates[target_index]["text"])
            media = target.evaluate(_MEDIA_READY)
            if actual == expected and media["ready"] and media["fonts_ready"]:
                if media["media_count"] >= expected_media_count:
                    break
            page.wait_for_timeout(350)
        else:
            raise TweetCaptureError("X post text or media did not match the monitored post")

        # A fonts/media settlement interval, without waiting for X's endless
        # network requests. A second check protects against reactive replacement.
        page.wait_for_timeout(500)
        candidates = page.evaluate(_ARTICLE_METADATA)
        index = choose_tweet_article(candidates, tweet_id, username)
        if (
            index is None
            or not _matches_post_url(page.url, tweet_id, username)
            or normalize_tweet_text(candidates[index]["text"]) != expected
        ):
            raise TweetCaptureError("X post identity/text changed before capture")
        target = page.locator("article").nth(index)
        media = target.evaluate(_MEDIA_READY)
        if (
            not media["ready"] or not media["fonts_ready"]
            or media["media_count"] < expected_media_count
        ):
            raise TweetCaptureError("X post media changed before capture")
        if page.evaluate(_CAPTURE_OBSTRUCTION):
            raise TweetCaptureError("X login or another dialog obscures the requested post")
        bounds = target.bounding_box()
        if not bounds or not (0 < bounds["width"] <= MAX_CAPTURE_WIDTH
                              and 0 < bounds["height"] <= MAX_CAPTURE_HEIGHT):
            raise TweetCaptureError("X post is too large or has no visible content")
        png = target.screenshot(
            type="png", animations="disabled", scale="css", timeout=15000,
            style='header[role="banner"] { visibility: hidden !important; }',
        )
        width, height = _png_dimensions(png)
        return {
            "png_base64": base64.b64encode(png).decode("ascii"),
            "sha256": hashlib.sha256(png).hexdigest(),
            "width": width,
            "height": height,
            "tweet_id": tweet_id,
            "username": username,
            "canonical_url": canonical_url,
            "captured_at": datetime.now(UTC).isoformat(),
        }
    except TweetCaptureError:
        raise
    except Exception as exc:
        # Browser errors may contain full URLs/cookies; expose only a stable code.
        raise TweetBrowserError("X browser capture failed") from exc
    finally:
        if page is not None:
            with suppress(Exception):
                page.close()
