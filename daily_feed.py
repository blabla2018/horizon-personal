"""A daily Telegram group using Horizon's scraper and authenticated AI client."""
import asyncio
import hashlib
import html
import json
import math
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'groups/tech-news.json'
STATE=ROOT/'data/groups/tech-news.json'
ORIGIN='https://blabla2018.github.io/horizon-personal'


def validate_decisions(result, posts, existing):
    if not isinstance(result,dict) or not isinstance(result.get('items'),list) or not isinstance(result.get('ads'),list):
        raise ValueError('AI response must contain items and ads arrays')
    covered=[]
    for item in result['items']:
        ids=item.get('post_ids')
        if not isinstance(ids,list) or not ids or not all(isinstance(x,str) for x in ids):
            raise ValueError('Invalid post_ids')
        covered.extend(ids)
        for key in ['title','text','reason']:
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
            article={'id':hashlib.sha256(identity.encode()).hexdigest(),'title':decision['title'].strip(),
                     'text':decision['text'].strip(),'score':float(decision['score']),
                     'reason':decision['reason'].strip(),'published_at':now,'sources':sources}
            state['articles'].append(article)
            existing[article['id']]=article
    state['processed_ids']=sorted(set(state['processed_ids'])|set(posts))
    return state


def render_article(article):
    paragraphs=''.join('<p>'+html.escape(p).replace('\n','<br>')+'</p>' for p in article['text'].split('\n\n') if p.strip())
    links=' · '.join('<a href="'+html.escape(s['url'],quote=True)+'">@'+html.escape(s['channel'])+'</a>' for s in article['sources'])
    return ('<p><strong>Оценка интереса: '+str(article['score']).removesuffix('.0')+'/10</strong> — '+html.escape(article['reason'])+'</p>'
            +paragraphs+'<p>Источники: '+links+'</p>')


def export(state, config, out):
    threshold=config['min_score']
    if threshold is not None and (isinstance(threshold,bool) or not isinstance(threshold,(int,float)) or not 0<=threshold<=10):
        raise ValueError('min_score must be null or a number from 0 to 10')
    articles=[a for a in state['articles'] if threshold is None or a['score']>=threshold]
    articles.sort(key=lambda a:(a['published_at'],a['score']),reverse=True)
    ET.register_namespace('content','http://purl.org/rss/1.0/modules/content/')
    ET.register_namespace('atom','http://www.w3.org/2005/Atom')
    rss=ET.Element('rss',version='2.0'); channel=ET.SubElement(rss,'channel')
    for key,value in [('title',config['title']),('link',ORIGIN+'/tech-news.html'),('description','Новости bugnotfeature и media1337: реклама удалена, повторы объединены, оценка 0–10.'),('language','ru')]:
        ET.SubElement(channel,key).text=value
    ET.SubElement(channel,'{http://www.w3.org/2005/Atom}link',href=ORIGIN+'/tech-news.xml',rel='self',type='application/rss+xml')
    body=[]
    for a in articles:
        content=render_article(a)
        item=ET.SubElement(channel,'item')
        for key,value in [('title',f"[{a['score']:g}/10] {a['title']}"),('link',a['sources'][0]['url']),('pubDate',format_datetime(datetime.fromisoformat(a['published_at']))),('{http://purl.org/rss/1.0/modules/content/}encoded',content)]:
            ET.SubElement(item,key).text=value
        ET.SubElement(item,'guid',isPermaLink='false').text='horizon:tech-news:'+a['id']
        body.append('<article><h2>'+html.escape(a['title'])+'</h2>'+content+'</article>')
    out.mkdir(parents=True,exist_ok=True)
    ET.indent(rss); ET.ElementTree(rss).write(out/'tech-news.xml',encoding='utf-8',xml_declaration=True)
    page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Техноновости — Horizon</title><style>body{max-width:820px;margin:40px auto;padding:0 24px;background:#faf8f3;color:#26342e;font:18px/1.7 system-ui}a{color:#17664e;overflow-wrap:anywhere}article{border-top:1px solid #ddd;padding:24px 0}h1{font-size:36px}h2{font-size:24px;line-height:1.4}</style><header><a href="./">Все ленты</a> · <a href="tech-news.xml">RSS</a></header><h1>Техноновости</h1><p>bugnotfeature + media1337 · Обновление ежедневно в 12:00 по Мадриду.<br>Оценка отражает личный интерес, а не достоверность. '''
    page+=('Порог выключен: сохраняются все нерекламные новости.' if threshold is None else 'Порог интереса: '+str(threshold)+'/10.')+'</p>'+''.join(body)+'</html>'
    (out/'tech-news.html').write_text(page)
    return len(articles)


async def collect(config):
    import httpx
    from src.models import TelegramConfig
    from src.scrapers.telegram import TelegramScraper
    cfg=TelegramConfig.model_validate({'enabled':True,'channels':[{'channel':c,'fetch_limit':100} for c in config['channels']]})
    since=datetime.now(timezone.utc)-timedelta(days=config['lookback_days'])
    async with httpx.AsyncClient(timeout=45,follow_redirects=True) as client:
        scraper=TelegramScraper(cfg,client); result=await scraper.fetch(since)
        if result.failures:
            raise RuntimeError('Channel fetch failed: '+str(result.failures))
        for channel in cfg.channels:
            for _ in range(config['max_pages_per_channel']-1):
                posts=[p for p in result.items if p.author==channel.channel]
                if not posts or min(p.published_at for p in posts)<=since: break
                before=min(int(p.id.rsplit(':',1)[1]) for p in posts)
                response=await client.get('https://t.me/s/'+channel.channel,params={'before':before}); response.raise_for_status()
                older=scraper._parse_channel_html(response.text,channel,since)
                known={p.id for p in result.items}; older=[p for p in older if p.id not in known]
                if not older: break
                result.items.extend(older)
    return {p.id:{'id':p.id,'channel':p.author,'url':'https://t.me/'+p.author+'/'+p.id.rsplit(':',1)[1],
                  'published_at':p.published_at.isoformat(),'text':p.content} for p in result.items}


async def main():
    config=json.loads(CONFIG.read_text())
    state=json.loads(STATE.read_text()) if STATE.exists() else {'articles':[],'processed_ids':[]}
    if '--export-only' not in sys.argv:
        from dotenv import load_dotenv
        from src.ai.client import create_ai_client
        from src.ai.utils import parse_json_response
        from src.storage.manager import StorageManager
        load_dotenv(ROOT/'.env')
        posts=await collect(config)
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
            payload=json.dumps({'new_posts':list(batch.values()),'existing_articles':context},ensure_ascii=False)
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
        STATE.parent.mkdir(parents=True,exist_ok=True)
        # Commit state only after every batch passes validation.
        temp=STATE.with_suffix('.tmp'); temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n'); temp.replace(STATE)
        audit_path.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
        pending.unlink(missing_ok=True)
    count=export(state,config,ROOT/'docs')
    print(f'Exported {count} articles. Score threshold: {config["min_score"]}.',flush=True)


if __name__=='__main__':
    asyncio.run(main())
