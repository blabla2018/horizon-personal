"""Restore explicit Telegram permalinks in the latest digest, without an AI call."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

import httpx
from dotenv import load_dotenv
from src.scrapers.telegram import TelegramScraper
from src.storage.manager import StorageManager


def add_links(text, posts):
    by_source = {}
    for post in posts:
        stamp = post.published_at.strftime('%b ') + str(post.published_at.day) + post.published_at.strftime(', %H:%M')
        by_source.setdefault((post.author.casefold(), stamp), []).append(post)
    linked = 0

    def update(match):
        nonlocal linked
        block = match[0]
        source = re.search(r'^telegram · ([A-Za-z0-9_\\]+) · ([A-Za-z]{3} \d{1,2}, \d{2}:\d{2})', block, re.M)
        if not source:
            return block
        candidates = by_source.get((source[1].replace('\\', '').casefold(), source[2]), [])
        if len(candidates) > 1:
            heading = re.search(r'^### \[.*?\]\(([^\n]*?)\) ⭐', block, re.M)
            exact = [post for post in candidates if heading and str(post.url) == heading[1]]
            if len(exact) == 1:
                candidates = exact
        if len(candidates) != 1:
            print(f'Unmatched Telegram source: {source[1]} {source[2]} ({len(candidates)} candidates)')
            return block  # Do not guess a post ID from an ambiguous timestamp.
        post = candidates[0]
        channel = post.metadata['channel']
        post_id = post.id.rsplit(':', 1)[-1]
        assert re.fullmatch(r'[A-Za-z0-9_]+', channel) and post_id.isdigit()
        url = f'https://t.me/{channel}/{post_id}'
        web = f'https://t.me/s/{channel}/{post_id}'
        block = re.sub(r'^(### \[.*?\]\()([^\n]*?)(\) ⭐)', lambda m: m[1]+url+m[3], block, count=1, flags=re.M)
        if '[Открыть пост в Telegram]' not in block:
            block = block.replace(source[0], source[0]+f' · [Открыть пост в Telegram]({url}) · [Читать в браузере]({web})', 1)
        linked += 1
        return block

    result = re.sub(r'<a id="item-[^"]+"></a>\n.*?(?=<a id="item-|\Z)', update, text, flags=re.S)
    return result, linked


async def main():
    load_dotenv()
    config = StorageManager(data_dir='data').load_config()
    path = max(Path('data/summaries').glob('horizon-????-??-??-ru.md'))
    original = path.read_text()
    since = datetime.now(timezone.utc)-timedelta(hours=config.collection.time_window_hours)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        scraper = TelegramScraper(config.sources.telegram, client)
        result = await scraper.fetch(since)
        # A newly published post can push an earlier digest item off the preview page.
        wanted = {}
        for author, stamp in re.findall(r'^telegram · ([A-Za-z0-9_\\]+) · ([A-Za-z]{3} \d{1,2}, \d{2}:\d{2})', original, re.M):
            wanted.setdefault(author.replace('\\', '').casefold(), set()).add(stamp)
        for cfg in config.sources.telegram.channels:
            for _ in range(5):
                channel_posts = [p for p in result.items if p.author.casefold() == cfg.channel.casefold()]
                stamps = {p.published_at.strftime('%b ')+str(p.published_at.day)+p.published_at.strftime(', %H:%M') for p in channel_posts}
                if wanted.get(cfg.channel.casefold(), set()) <= stamps or not channel_posts:
                    break
                before = min(int(p.id.rsplit(':', 1)[-1]) for p in channel_posts)
                response = await client.get(f'https://t.me/s/{cfg.channel}', params={'before': before}, timeout=30)
                response.raise_for_status()
                older = scraper._parse_channel_html(response.text, cfg, since)
                known = {p.id for p in result.items}
                older = [p for p in older if p.id not in known]
                if not older:
                    break
                result.items.extend(older)
    if result.failures:
        raise RuntimeError(f'Telegram link lookup failed: {list(result.failures)}')
    updated, linked = add_links(original, result.items)
    expected = len(re.findall(r'^telegram · ', original, re.M))
    if linked != expected:
        raise RuntimeError(f'Matched {linked}/{expected} Telegram posts; refusing incomplete link repair')
    path.write_text(updated)
    print(f'Added exact post and web-preview links for {linked} Telegram items.')


if __name__ == '__main__':
    asyncio.run(main())
