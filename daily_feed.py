"""A daily Telegram group using Horizon's scraper and authenticated AI client."""
import asyncio
import hashlib
import html
import json
import math
import re
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'groups/tech-news.json'
STATE=ROOT/'data/groups/tech-news.json'
ORIGIN='https://blabla2018.github.io/horizon-personal'


def safe_url(value):
    try:
        return isinstance(value,str) and urlsplit(value).scheme in {'http','https'} and bool(urlsplit(value).netloc)
    except ValueError:
        return False


def sanitize_original(fragment):
    from bs4 import BeautifulSoup, Comment
    soup=BeautifulSoup(fragment,'html.parser')
    for node in soup.find_all(string=lambda s:isinstance(s,Comment)): node.extract()
    for node in list(soup.find_all(['script','style','iframe','object','embed','form'])): node.decompose()
    allowed={'a','b','strong','i','em','u','s','del','blockquote','code','pre','br','p','div','span','img','video','source'}
    for tag in list(soup.find_all(True)):
        if tag.name is None: continue
        if tag.name not in allowed:
            tag.unwrap(); continue
        original=dict(tag.attrs); tag.attrs={}
        if tag.name=='a':
            if not safe_url(original.get('href')): tag.unwrap(); continue
            tag['href']=original['href']
        if tag.name in {'img','video','source'}:
            if not safe_url(original.get('src')): tag.decompose(); continue
            tag['src']=original['src']
            if tag.name=='img': tag['alt']=original.get('alt','Фото из оригинального поста')
            if tag.name=='video':
                tag['controls']=''; tag['preload']='none'
                if safe_url(original.get('poster')): tag['poster']=original['poster']
            if tag.name!='source': tag['style']='max-width:100%;height:auto'
    return str(soup)


def parse_original_posts(page, channel, since):
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(page,'html.parser'); posts={}
    for node in soup.select('div.tgme_widget_message[data-post]'):
        path=node.get('data-post',''); post_id=path.rsplit('/',1)[-1]
        stamp=node.select_one('time[datetime]')
        if not post_id.isdigit() or stamp is None: continue
        published=datetime.fromisoformat(stamp['datetime'].replace('Z','+00:00'))
        if published<since: continue
        url='https://t.me/'+channel+'/'+post_id
        text_node=node.select_one('.tgme_widget_message_text')
        fragment=text_node.decode_contents() if text_node else ''
        copy=BeautifulSoup(fragment,'html.parser')
        for br in copy.find_all('br'): br.replace_with('\n')
        text=copy.get_text().strip()
        forwarded=node.select_one('.tgme_widget_message_forwarded_from')
        if forwarded: fragment='<p>'+forwarded.decode_contents()+'</p>'+fragment
        photos=[]
        for photo in node.select('.tgme_widget_message_photo_wrap'):
            match=re.search(r'background-image:\s*url\([\'\"]?(.*?)[\'\"]?\)',photo.get('style',''))
            if match and safe_url(match[1]):
                photos.append('<a href="'+url+'"><img src="'+html.escape(match[1],quote=True)+'" alt="Фото из оригинального поста"></a>')
        media=''.join(photos)
        for video in node.select('video[src]'):
            if safe_url(video.get('src')):
                media+='<video controls src="'+html.escape(video['src'],quote=True)+'"'
                if safe_url(video.get('poster')): media+=' poster="'+html.escape(video['poster'],quote=True)+'"'
                media+='></video><p><a href="'+url+'">Открыть видео в Telegram</a></p>'
        if not text and not media: continue
        title=text.split('\n')[0] if text else 'Медиа из @'+channel
        title=title[:177]+'…' if len(title)>180 else title
        identity='telegram:'+channel+':'+post_id
        posts[identity]={'id':identity,'channel':channel,'url':url,'published_at':published.isoformat(),
                         'title':title,'text':text or '[Медиа без текстовой подписи; содержание не анализировалось]',
                         'original_html':sanitize_original(media+fragment)}
    return posts


def set_original(article, posts):
    candidates=[posts[s['id']] for s in article['sources'] if s['id'] in posts]
    primary=next((p for p in candidates if p['id']==article.get('primary_source_id')),None)
    if primary is None and article.get('original_html'): return
    if primary is None and candidates: primary=max(candidates,key=lambda p:len(p['text']))
    if primary is None:
        if article.get('original_html'): return
        raise ValueError('Original post unavailable for '+article['id'])
    article.update(title=primary['title'],text=primary['text'],original_html=primary['original_html'],primary_source_id=primary['id'])


def validate_decisions(result, posts, existing):
    if not isinstance(result,dict) or not isinstance(result.get('items'),list) or not isinstance(result.get('ads'),list):
        raise ValueError('AI response must contain items and ads arrays')
    covered=[]
    for item in result['items']:
        ids=item.get('post_ids')
        if not isinstance(ids,list) or not ids or not all(isinstance(x,str) for x in ids):
            raise ValueError('Invalid post_ids')
        covered.extend(ids)
        for key in ['reason']:
            if not isinstance(item.get(key),str) or not item[key].strip():
                raise ValueError('Missing '+key)
        score=item.get('score')
        if isinstance(score,bool) or not isinstance(score,(int,float)) or not math.isfinite(score) or not 0<=score<=10:
            raise ValueError(f'Score must be a JSON number from 0 to 10, got {score!r}')
        if item.get('duplicate_of') is not None and item['duplicate_of'] not in existing:
            raise ValueError('Unknown existing article')
    for ad in result['ads']:
        if not isinstance(ad.get('post_id'),str) or not isinstance(ad.get('reason'),str) or not ad['reason'].strip():
            raise ValueError('Ad without id or reason')
        covered.append(ad['post_id'])
    if len(covered)!=len(set(covered)) or set(covered)!=set(posts):
        raise ValueError('AI omitted, duplicated or invented an input post')


def merge_decisions(state, result, posts, now):
    existing={x['id']:x for x in state['articles']}
    validate_decisions(result,posts,existing)
    for decision in result['items']:
        sources=[{'id':i,'url':posts[i]['url'],'channel':posts[i]['channel']} for i in decision['post_ids']]
        duplicate=decision.get('duplicate_of')
        if duplicate:
            article=existing[duplicate]
            known={s['id'] for s in article['sources']}
            article['sources'].extend(s for s in sources if s['id'] not in known)
        else:
            identity=min(s['id'] for s in sources)
            article={'id':hashlib.sha256(identity.encode()).hexdigest(),'score':float(decision['score']),
                     'reason':decision['reason'].strip(),'published_at':now,'sources':sources}
            set_original(article,posts)
            state['articles'].append(article)
            existing[article['id']]=article
    state['processed_ids']=sorted(set(state['processed_ids'])|set(posts))
    return state


def render_article(article):
    links=' · '.join('<a href="'+html.escape(s['url'],quote=True)+'">@'+html.escape(s['channel'])+'</a>' for s in article['sources'])
    return ('<p><strong>Оценка интереса: '+str(article['score']).removesuffix('.0')+'/10</strong></p>'
            +sanitize_original(article['original_html'])+'<p>Источники: '+links+'</p>')


def export(state, config, out):
    threshold=config['min_score']
    if threshold is not None and (isinstance(threshold,bool) or not isinstance(threshold,(int,float)) or not 0<=threshold<=10):
        raise ValueError('min_score must be null or a number from 0 to 10')
    articles=[a for a in state['articles'] if threshold is None or a['score']>=threshold]
    articles.sort(key=lambda a:(a['published_at'],a['score']),reverse=True)
    ET.register_namespace('content','http://purl.org/rss/1.0/modules/content/')
    ET.register_namespace('atom','http://www.w3.org/2005/Atom')
    rss=ET.Element('rss',version='2.0'); channel=ET.SubElement(rss,'channel')
    for key,value in [('title',config['title']),('link',ORIGIN+'/tech-news.html'),('description','Оригинальные посты bugnotfeature и media1337: рекламные публикации исключены, дубли объединены, оценка 0–10.'),('language','ru')]:
        ET.SubElement(channel,key).text=value
    ET.SubElement(channel,'{http://www.w3.org/2005/Atom}link',href=ORIGIN+'/tech-news.xml',rel='self',type='application/rss+xml')
    body=[]
    for a in articles:
        content=render_article(a)
        item=ET.SubElement(channel,'item')
        primary=next(s for s in a['sources'] if s['id']==a['primary_source_id'])
        for key,value in [('title',f"[{a['score']:g}/10] {a['title']}"),('link',primary['url']),('pubDate',format_datetime(datetime.fromisoformat(a['published_at']))),('{http://purl.org/rss/1.0/modules/content/}encoded',content)]:
            ET.SubElement(item,key).text=value
        ET.SubElement(item,'guid',isPermaLink='false').text='horizon:tech-news:'+a['id']
        body.append('<article><h2>'+html.escape(a['title'])+'</h2>'+content+'</article>')
    out.mkdir(parents=True,exist_ok=True)
    ET.indent(rss); ET.ElementTree(rss).write(out/'tech-news.xml',encoding='utf-8',xml_declaration=True)
    # A separate feed lets readers replace cached AI-report entries without changing old GUIDs.
    original_rss=ET.fromstring(ET.tostring(rss))
    original_channel=original_rss.find('channel')
    original_channel.find('title').text='Техноновости — оригинальные посты'
    original_channel.find('{http://www.w3.org/2005/Atom}link').set('href',ORIGIN+'/tech-news-original.xml')
    for item in original_channel.findall('item'):
        item.find('guid').text=item.findtext('guid').replace('horizon:tech-news:','horizon:tech-news-original:')
    ET.ElementTree(original_rss).write(out/'tech-news-original.xml',encoding='utf-8',xml_declaration=True)
    page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Техноновости — Horizon</title><style>body{max-width:820px;margin:40px auto;padding:0 24px;background:#faf8f3;color:#26342e;font:18px/1.7 system-ui}a{color:#17664e;overflow-wrap:anywhere}article{border-top:1px solid #ddd;padding:24px 0}h1{font-size:36px}h2{font-size:24px;line-height:1.4}</style><header><a href="./">Все ленты</a> · <a href="tech-news.xml">RSS</a></header><h1>Техноновости</h1><p>bugnotfeature + media1337 · Обновление ежедневно в 12:00 по Мадриду.<br>Оценка отражает личный интерес, а не достоверность. '''
    page=page.replace('href="tech-news.xml"','href="tech-news-original.xml"')
    page+=('Порог выключен: сохраняются все нерекламные новости.' if threshold is None else 'Порог интереса: '+str(threshold)+'/10.')+' Тексты — оригинальные посты авторов; ИИ их не переписывает.</p>'+''.join(body)+'</html>'
    (out/'tech-news.html').write_text(page)
    return len(articles)


async def collect(config):
    import httpx
    from src.models import TelegramConfig
    from src.scrapers.telegram import TELEGRAM_WEB_BASES, USER_AGENT
    cfg=TelegramConfig.model_validate({'enabled':True,'channels':[{'channel':c,'fetch_limit':100} for c in config['channels']]})
    since=datetime.now(timezone.utc)-timedelta(days=config['lookback_days'])
    async with httpx.AsyncClient(timeout=45,follow_redirects=True) as client:
        result={}
        for channel in cfg.channels:
            before=None
            for _ in range(config['max_pages_per_channel']):
                last_error=None
                for base in TELEGRAM_WEB_BASES:
                    try:
                        response=await client.get(base+'/'+channel.channel,params={'before':before} if before else {},headers={'User-Agent':USER_AGENT})
                        response.raise_for_status(); break
                    except httpx.HTTPError as exc: last_error=exc
                else: raise RuntimeError('Channel fetch failed: '+channel.channel) from last_error
                batch=parse_original_posts(response.text,channel.channel,since)
                fresh={i:p for i,p in batch.items() if i not in result}
                if not fresh: break
                result.update(fresh)
                before=min(int(i.rsplit(':',1)[1]) for i in batch)
    return result


async def main():
    config=json.loads(CONFIG.read_text())
    state=json.loads(STATE.read_text()) if STATE.exists() else {'articles':[],'processed_ids':[]}
    if '--restore-originals' in sys.argv:
        posts=await collect(config)
        for article in state['articles']: set_original(article,posts)
        print(f'Restored original posts for {len(state["articles"])} articles; no AI calls.',flush=True)
    elif '--export-only' not in sys.argv:
        from dotenv import load_dotenv
        from src.ai.client import create_ai_client
        from src.ai.utils import parse_json_response
        from src.storage.manager import StorageManager
        load_dotenv(ROOT/'.env')
        posts=await collect(config)
        for article in state['articles']: set_original(article,posts)
        seen=set(state['processed_ids']); new={i:p for i,p in posts.items() if i not in seen}
        print(f'Fetched {len(posts)} text posts; {len(new)} not processed.',flush=True)
        audit_path=STATE.parent/'tech-news-audit.json'
        audit=json.loads(audit_path.read_text()) if audit_path.exists() else []
        audit=[ad for ad in audit if ad['post_id'] not in new]
        prompt=(ROOT/'groups/tech-news.prompt.md').read_text()
        pending=STATE.with_name('tech-news-pending.json')
        fingerprint=hashlib.sha256((prompt+json.dumps(config,sort_keys=True)).encode()).hexdigest()
        if pending.exists():
            checkpoint=json.loads(pending.read_text())
            saved=checkpoint['state']
            if checkpoint['fingerprint']==fingerprint and set(state['processed_ids'])<=set(saved['processed_ids']):
                state=saved; audit=checkpoint['ads']
                new={i:p for i,p in posts.items() if i not in set(state['processed_ids'])}
                print(f'Resuming checkpoint: {len(new)} posts remain.',flush=True)
        ai=create_ai_client(StorageManager(data_dir=str(ROOT/'data')).load_config().ai)
        rows=list(new.values()); now=datetime.now(timezone.utc).isoformat()
        for start in range(0,len(rows),30):
            batch={p['id']:p for p in rows[start:start+30]}
            recent=state['articles'][-200:]
            context=[{'id':a['id'],'title':a['title'],'text':a['text'][:1500]} for a in recent]
            payload=json.dumps({'new_posts':[{k:v for k,v in p.items() if k!='original_html'} for p in batch.values()],'existing_articles':context},ensure_ascii=False)
            last_error=''
            for attempt in range(3):
                response=await ai.complete(system=prompt,user=payload+last_error,max_tokens=24000)
                try:
                    result=parse_json_response(response)
                    validate_decisions(result,batch,{a['id']:a for a in state['articles']})
                    break
                except (ValueError,TypeError,KeyError,AttributeError) as exc:
                    if attempt==2: raise
                    last_error='\nYour previous response was rejected: '+str(exc)+'. Regenerate the entire JSON. All scores must be numeric values, including 0 for unknown/low interest; never use null or a string.'
                    print('Retrying invalid AI response: '+str(exc),flush=True)
            merge_decisions(state,result,batch,now)
            audit.extend(result['ads'])
            pending.parent.mkdir(parents=True,exist_ok=True)
            checkpoint_path=pending.with_suffix('.tmp')
            checkpoint_path.write_text(json.dumps({'fingerprint':fingerprint,'state':state,'ads':audit},ensure_ascii=False))
            checkpoint_path.replace(pending)
            print(f'Batch: {len(batch)} posts → {len(result["items"])} news groups, {len(result["ads"])} ads.',flush=True)
        audit_path.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
        pending.unlink(missing_ok=True)
    if '--export-only' not in sys.argv:
        STATE.parent.mkdir(parents=True,exist_ok=True)
        # Commit state only after every batch and original-content check passes.
        temp=STATE.with_suffix('.tmp'); temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n'); temp.replace(STATE)
    count=export(state,config,ROOT/'docs')
    print(f'Exported {count} articles. Score threshold: {config["min_score"]}.',flush=True)


if __name__=='__main__':
    asyncio.run(main())
