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
