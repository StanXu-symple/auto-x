"""Optional real DOM checks: set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH + NODE_PATH."""
import json
import os
import shutil
import subprocess

import pytest

from app.services.x_tweet_capture import (
    _ARTICLE_METADATA,
    _CAPTURE_OBSTRUCTION,
    _MEDIA_READY,
    choose_tweet_article,
)

_RUN_DOM = r"""
const fs = require('fs');
const {chromium} = require('playwright');
(async () => {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
    headless: true
  });
  try {
    const page = await browser.newPage();
    // Keep public-media fixtures deterministic; no live platform requests.
    await page.route('https://pbs.twimg.com/**', route => route.fulfill({
      contentType: 'image/png',
      body: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE'
        + 'QVR42mP8/x8AAwMCAO+aN2kAAAAASUVORK5CYII=', 'base64')
    }));
    await page.setContent(input.html);
    const candidates = await page.evaluate(eval(input.metadata));
    const target = page.locator('article').nth(input.media_index || 0);
    const media = await target.evaluate(eval(input.media));
    const obstructed = await page.evaluate(eval(input.obstruction));
    process.stdout.write(JSON.stringify({candidates, media, obstructed}));
  } finally { await browser.close(); }
})().catch(error => { process.stderr.write(String(error)); process.exit(1); });
"""


def dom(html, *, media_index=0):
    node = os.environ.get("XSENTINEL_BROWSER_TEST_NODE") or shutil.which("node")
    if not node or not os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"):
        pytest.skip("Real DOM regression requires an installed Node Playwright browser")
    completed = subprocess.run(
        [node, "-e", _RUN_DOM],
        input=json.dumps({
            "html": html, "metadata": _ARTICLE_METADATA, "media": _MEDIA_READY,
            "media_index": media_index,
            "obstruction": _CAPTURE_OBSTRUCTION,
        }),
        text=True, capture_output=True, check=True, timeout=30,
    )
    return json.loads(completed.stdout)


def own_content(username, tweet_id, text="hello"):
    return f"""
      <div data-testid="User-Name"><a href="https://x.com/{username}">@{username}</a></div>
      <a href="https://x.com/{username}/status/{tweet_id}"><time>now</time></a>
      <div data-testid="tweetText">{text}</div>
    """


def test_real_dom_excludes_quote_timestamp_author_and_nested_article():
    html = f"""
      <article>{own_content('quoting_author', '222')}
        <div role="link">{own_content('user', '123')}</div>
        <div data-testid="quoteTweet"><article>{own_content('user', '123')}</article></div>
      </article>
      <article>{own_content('user', '333')}</article>
      <article>{own_content('user', '123')}</article>
    """
    result = dom(html)
    assert result["candidates"][0]["time_urls"] == ["https://x.com/quoting_author/status/222"]
    assert result["candidates"][0]["text"] == "hello"
    assert result["candidates"][1]["time_urls"] == []
    assert choose_tweet_article(result["candidates"], "123", "user") == 3


def test_real_dom_preserves_mentions_emoji_and_excludes_outbound_labels():
    text = (
        'hello <a href="https://x.com/friend">@friend</a> '
        '<img alt="😀" src="data:invalid"> '
        '<a href="https://t.co/example" target="_blank">example.com/expanded-label</a>'
    )
    result = dom(f"<article>{own_content('user', '123', text)}</article>")
    assert result["candidates"][0]["text"].strip() == "hello @friend 😀"


def test_real_dom_quoted_media_does_not_satisfy_target_media_count():
    html = f"""
      <article>{own_content('user', '123')}
        <div role="link"><div data-testid="tweetPhoto"><img src="data:invalid"></div></div>
      </article>
    """
    result = dom(html)
    assert result["media"]["media_count"] == 0
    assert result["media"]["ready"] is False


def test_real_dom_visible_login_dialog_obscures_loaded_post():
    html = f"""
      <article>{own_content('user', '123')}</article>
      <div role="dialog" style="position:fixed;inset:0">Sign in</div>
    """
    assert dom(html)["obstructed"] is True
    assert dom(html.replace('position:fixed;inset:0', 'display:none'))["obstructed"] is False


def public_content(username, tweet_id, text="hello", *, body=True):
    # Structural fixture taken from the public SSR page; no generated class names.
    return f"""
      <div><a href="https://x.com/{username}">Author name</a>
        <a href="https://x.com/{username}"><span>@{username}</span></a></div>
      {f'<div dir="auto">{text}</div>' if body else ''}
      <span><a data-base-ui-tooltip-trigger href="https://x.com/{username}/status/{tweet_id}">
        6:12 AM · Sep 30, 2026</a><script>window.formatTimestamp('not body');</script></span>
      <a data-base-ui-tooltip-trigger data-status="active"
        href="https://x.com/{username}/status/{tweet_id}">785K Views</a>
      <div data-engagement-action="reply"><a
        href="https://x.com/{username}/status/{tweet_id}">Reply</a></div>
    """


def test_public_dom_matches_author_body_and_timestamp_without_legacy_testids():
    result = dom(f"<article>{public_content('thsottiaux', '123', 'What’s up dot')}</article>")
    candidate = result["candidates"][0]
    assert candidate["username"] == "thsottiaux"
    assert candidate["text"] == "What’s up dot"
    assert candidate["time_urls"] == ["https://x.com/thsottiaux/status/123"]
    assert choose_tweet_article(result["candidates"], "123", "thsottiaux") == 0


def test_public_dom_does_not_match_quoted_post_or_adjacent_reply():
    html = f"""
      <article>{public_content('user', '222', 'my own post')}
        <div role="link" data-timeline-entry data-href="/user/status/123">
          <article>{public_content('user', '123', 'quote')}</article>
        </div>
      </article>
      <article>{public_content('user', '333', 'reply')}</article>
    """
    result = dom(html)
    assert result["candidates"][0]["text"] == "my own post"
    assert result["candidates"][0]["time_urls"] == ["https://x.com/user/status/222"]
    assert result["candidates"][1]["username"] == ""
    assert choose_tweet_article(result["candidates"], "123", "user") is None


def test_public_dom_linked_quote_cannot_supply_identity_or_body():
    html = f"""
      <article>{public_content('other', '222', 'mine')}
        <div role="link"><div dir="auto">quote</div>
          <a data-base-ui-tooltip-trigger href="https://x.com/user/status/123">now</a>
        </div>
      </article>
    """
    result = dom(html)
    assert result["candidates"][0]["text"] == "mine"
    assert choose_tweet_article(result["candidates"], "123", "user") is None


def test_public_dom_anchor_wrapped_quote_body_is_excluded():
    html = f"""
      <article>{public_content('user', '123', 'mine')}
        <a href="https://x.com/other/status/222"><div dir="auto">quoted body</div></a>
      </article>
    """
    result = dom(html)
    assert result["candidates"][0]["text"] == "mine"


def test_public_dom_preserves_mentions_emoji_linebreaks_and_removes_outbound_labels():
    text = (
        'hello <a href="https://x.com/friend">@friend</a><br>'
        '<span dir="auto">world</span><img alt="😀" src="data:invalid"> '
        '<a href="https://t.co/abc" target="_blank">example.com</a>'
        '<a data-base-ui-tooltip-trigger href="https://x.com/friend/status/999">quoted link</a>'
    )
    result = dom(f"<article>{public_content('user', '123', text)}</article>")
    assert result["candidates"][0]["username"] == "user"
    assert result["candidates"][0]["text"].strip() == "hello @friend\nworld😀 quoted link"
    assert result["candidates"][0]["time_urls"] == ["https://x.com/user/status/123"]


def test_public_dom_requires_an_own_timestamp_not_engagement_links():
    html = public_content('user', '123').replace('data-base-ui-tooltip-trigger href=', 'href=')
    result = dom(f"<article>{html}</article>")
    assert choose_tweet_article(result["candidates"], "123", "user") is None


def test_public_dom_media_counts_photos_not_avatar_preview_or_nested_quote():
    html = f"""
      <article>{public_content('user', '123')}
        <a href="https://x.com/user"><img src="https://pbs.twimg.com/profile_images/avatar.png"></a>
        <a href="https://x.com/user/status/123/photo/1"><img src="https://pbs.twimg.com/media/own.png"></a>
        <a href="https://example.com"><img src="https://pbs.twimg.com/media/card.png"></a>
        <div role="link"><article>{public_content('user', '222')}
          <img src="https://pbs.twimg.com/media/quote.png"></article></div>
      </article>
    """
    result = dom(html)
    assert result["media"]["media_count"] == 1
    assert result["media"]["ready"] is True


def test_public_dom_waits_for_nested_quote_media_without_counting_it():
    html = f"""
      <article>{public_content('user', '123')}
        <div role="link"><article>{public_content('user', '222')}
          <img src="data:invalid"></article></div>
      </article>
    """
    result = dom(html)
    assert result["media"]["media_count"] == 0
    assert result["media"]["ready"] is False


def test_public_dom_empty_media_post_does_not_use_caption_or_script_as_body():
    result = dom(f"<article>{public_content('user', '123', body=False)}</article>")
    assert result["candidates"][0]["text"] == ""
    assert choose_tweet_article(result["candidates"], "123", "user") == 0
