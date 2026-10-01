import base64
import hashlib
import struct
import zlib

import pytest

from app.services import x_tweet_capture as capture_module
from app.services.x_tweet_capture import (
    TweetBrowserError,
    TweetCaptureError,
    canonical_tweet_url,
    capture_tweet,
    choose_tweet_article,
    normalize_tweet_text,
)


def candidate(index, *, tweet_id="123", username="user", text="hello"):
    return {
        "index": index,
        "username": username,
        "time_urls": [f"https://x.com/{username}/status/{tweet_id}"],
        "text": text,
    }


def png_bytes(width=600, height=800):
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(
            ">I", zlib.crc32(name + data)
        )
    return b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ) + chunk(b"IDAT", zlib.compress(b"\x00")) + chunk(b"IEND", b"")


class FakeExpand:
    def __init__(self, page):
        self.page = page

    def count(self):
        return int(self.page.expand_available)

    @property
    def first(self):
        return self

    def click(self, **_kwargs):
        self.page.candidates[0]["text"] = self.page.expanded_text


class FakeArticle:
    def __init__(self, page, index):
        self.page = page
        self.index = index

    def scroll_into_view_if_needed(self, **_kwargs):
        self.page.scrolled = True

    def locator(self, _selector):
        return FakeExpand(self.page)

    def evaluate(self, _script):
        self.page.media_checks += 1
        return {
            "media_count": self.page.media_count,
            "fonts_ready": self.page.fonts_ready,
            "ready": self.page.media_checks >= self.page.media_ready_after,
        }

    def bounding_box(self):
        return {"width": 600, "height": self.page.height, "x": 0, "y": 0}

    def screenshot(self, **kwargs):
        self.page.screenshots.append((self.index, kwargs))
        return self.page.png


class FakeArticles:
    def __init__(self, page):
        self.page = page

    def nth(self, index):
        return FakeArticle(self.page, index)


class FakePage:
    def __init__(self, candidates=None):
        self.candidates = [candidate(0)] if candidates is None else candidates
        self.url = "https://x.com/user/status/123"
        self.expand_available = False
        self.expanded_text = "hello"
        self.media_count = 1
        self.media_checks = 0
        self.media_ready_after = 1
        self.fonts_ready = True
        self.height = 800
        self.png = png_bytes()
        self.clock = 0.0
        self.closed = False
        self.scrolled = False
        self.screenshots = []
        self.obstructed = False

    def set_default_timeout(self, _timeout):
        pass

    def set_viewport_size(self, _viewport):
        pass

    def goto(self, _url, **_kwargs):
        pass

    def evaluate(self, script, *_args):
        if '[role="dialog"]' in script:
            return self.obstructed
        if "scrollY" in script:
            return 0
        if "scrollTo" in script:
            return None
        return self.candidates

    def locator(self, _selector):
        return FakeArticles(self)

    def wait_for_timeout(self, milliseconds):
        self.clock += milliseconds / 1000

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, page):
        self.page = page

    def new_page(self):
        return self.page


def capture(page, monkeypatch, *, expected_text="hello", media_count=0):
    monkeypatch.setattr(capture_module.time, "monotonic", lambda: page.clock)
    return capture_tweet(
        FakeContext(page), tweet_id="123", username="user",
        expected_text=expected_text, expected_media_count=media_count,
    )


def test_selects_exact_author_and_post_among_parent_replies_and_quotes():
    parent = candidate(0, tweet_id="111")
    quote = candidate(1, tweet_id="222", username="quoting_author")
    # Quoted body/status links do not establish the quoting article's own time.
    quote["other_links"] = ["https://x.com/user/status/123"]
    reply = candidate(2, tweet_id="333")
    target = candidate(3)
    assert choose_tweet_article([parent, quote, reply, target], "123", "USER") == 3
    target["username"] = "other_author"
    assert choose_tweet_article([parent, quote, reply, target], "123", "user") is None


@pytest.mark.parametrize("url", [
    "https://x.com.evil.example/user/status/123", "https://user:secret@x.com/user/status/123",
    "https://x.com:invalid/user/status/123", "https://x.com/user/status/123/photo/1",
    "http://x.com/user/status/123", "https://x.com/user/status/1234",
])
def test_rejects_untrusted_or_different_status_urls(url):
    value = candidate(0)
    value["time_urls"] = [url]
    assert choose_tweet_article([value], "123", "user") is None


def test_rejects_duplicate_identity_instead_of_selecting_arbitrarily():
    with pytest.raises(TweetCaptureError, match="Multiple articles"):
        choose_tweet_article([candidate(0), candidate(1)], "123", "user")


@pytest.mark.parametrize("tweet_id,username", [
    ("123/../456", "user"), ("123", "user?redirect=evil"), ("123", "../../evil"),
])
def test_canonical_url_has_validated_identity(tweet_id, username):
    with pytest.raises(TweetCaptureError):
        canonical_tweet_url(tweet_id, username)


def test_normalizes_api_whitespace_html_and_shortened_urls():
    assert normalize_tweet_text("hello\n &amp; world https://t.co/abcdef") == "hello & world"
    assert normalize_tweet_text("hello\u200b & world") == "hello & world"
    assert normalize_tweet_text("hello https://t.co/abcdef.") == "hello ."


def test_capture_waits_for_media_and_saves_only_verified_article(monkeypatch):
    page = FakePage([candidate(0, tweet_id="111"), candidate(1), candidate(2, tweet_id="333")])
    page.media_ready_after = 3
    result = capture(page, monkeypatch, media_count=1)
    assert page.screenshots[0][0] == 1
    assert page.screenshots[0][1]["scale"] == "css"
    assert page.media_checks >= 4
    assert page.scrolled
    assert page.closed
    assert base64.b64decode(result["png_base64"]) == page.png
    assert result["sha256"] == hashlib.sha256(page.png).hexdigest()
    assert result["width"] == 600
    assert result["height"] == 800
    assert result["canonical_url"] == "https://x.com/user/status/123"


def test_expands_long_text_before_comparing_and_capturing(monkeypatch):
    page = FakePage([candidate(0, text="hello…")])
    page.expand_available = True
    page.expanded_text = "hello long post content"
    capture(page, monkeypatch, expected_text=page.expanded_text)
    assert len(page.screenshots) == 1


@pytest.mark.parametrize("problem", ["login", "text", "image", "media_count", "redirect"])
def test_does_not_save_login_unavailable_wrong_text_or_incomplete_media(problem, monkeypatch):
    page = FakePage()
    if problem == "login":
        page.candidates = []
    elif problem == "text":
        page.candidates[0]["text"] = "a different post"
    elif problem == "image":
        page.media_ready_after = 1000
    elif problem == "media_count":
        page.media_count = 0
    elif problem == "redirect":
        page.url = "https://x.com/i/flow/login"
    with pytest.raises(TweetCaptureError):
        capture(page, monkeypatch, media_count=1)
    assert page.closed
    assert not page.screenshots


def test_rejects_oversize_article_before_screenshot_allocation(monkeypatch):
    page = FakePage()
    page.height = 16001
    with pytest.raises(TweetCaptureError, match="too large"):
        capture(page, monkeypatch)
    assert not page.screenshots
    assert page.closed


def test_rejects_loaded_post_under_login_dialog(monkeypatch):
    page = FakePage()
    page.obstructed = True
    with pytest.raises(TweetCaptureError, match="dialog obscures"):
        capture(page, monkeypatch)
    assert not page.screenshots
    assert page.closed


def test_browser_exception_is_safe_and_page_is_closed(monkeypatch):
    page = FakePage()

    def fail(*_args, **_kwargs):
        raise RuntimeError("bad browser url?cookie=secret")

    page.goto = fail
    with pytest.raises(TweetBrowserError) as exc:
        capture(page, monkeypatch)
    assert str(exc.value) == "X browser capture failed"
    assert "secret" not in str(exc.value)
    assert page.closed


def test_rejects_png_beyond_byte_or_dimension_limits():
    with pytest.raises(TweetCaptureError):
        capture_module._png_dimensions(png_bytes(width=2001))
    with pytest.raises(TweetCaptureError):
        capture_module._png_dimensions(b"x" * (capture_module.MAX_PNG_BYTES + 1))
