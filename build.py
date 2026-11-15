"""Export local Horizon digests as a static reading site and a full-content RSS feed."""
import html
import re
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from html.parser import HTMLParser
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
import markdown
HORIZON = ROOT
def _prepare_email_markdown(text):
    text = text.replace('<', '&lt;').replace('\\|', '|')
    list_item = r'[ \t]*(?:\d+[.)]|[-*+])[ \t]'
    return re.sub(rf'(?m)^((?!{list_item}).*\S.*)\n(?={list_item})', r'\1\n\n', text)
def _render_email_markdown(text):
    return markdown.markdown(text)

ORIGIN = 'https://blabla2018.github.io/horizon-personal'
CSS = '''
:root{color-scheme:light dark;--bg:#faf8f3;--paper:#fffefa;--ink:#242c2a;--muted:#68716d;--line:#dedfd5;--accent:#17664e}
@media(prefers-color-scheme:dark){:root{--bg:#17201d;--paper:#1d2823;--ink:#e5e8df;--muted:#a2afa5;--line:#3d4d43;--accent:#9bd8b2}}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}body{margin:0;background:var(--bg);color:var(--ink);font:18px/1.75 Georgia,serif}
header,main,footer{max-width:820px;margin:auto;padding:24px}header{padding-top:50px;font-family:system-ui,sans-serif}nav{display:flex;gap:24px;flex-wrap:wrap;font-size:15px}
a{color:var(--accent);text-underline-offset:3px;overflow-wrap:anywhere}a:hover{text-decoration-thickness:2px}h1{font-size:clamp(30px,6vw,46px);line-height:1.16;letter-spacing:-1px;font-weight:600}h2{font:600 24px/1.4 system-ui,sans-serif;margin-top:36px}h3{font:600 23px/1.4 system-ui,sans-serif;margin:36px 0 14px}h3 a{text-decoration:none}
p{margin:12px 0 20px}blockquote{border-left:3px solid var(--accent);padding-left:18px;margin:24px 0;color:var(--muted)}hr{border:0;border-top:1px solid var(--line);margin:32px 0}li{margin:8px 0}code{font-size:.88em;overflow-wrap:anywhere}pre{overflow:auto;padding:16px;background:var(--paper)}details{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:18px 22px;font:15px/1.7 system-ui,sans-serif}summary{cursor:pointer;font-weight:600}footer{font:14px/1.7 system-ui,sans-serif;color:var(--muted);padding-bottom:60px}.eyebrow{font:600 12px/1.5 system-ui,sans-serif;letter-spacing:2px;color:var(--muted);text-transform:uppercase}.issue{border-top:1px solid var(--line);padding:24px 0}.issue a{font-size:24px;text-decoration:none}.meta{color:var(--muted);font:14px/1.6 system-ui,sans-serif}.top{position:fixed;right:18px;bottom:18px;background:var(--paper);border:1px solid var(--line);padding:5px 14px;border-radius:24px;font:14px/1.8 system-ui,sans-serif;text-decoration:none}@media(max-width:600px){body{font-size:17px}header,main,footer{padding:20px}header{padding-top:32px}details ol{padding-left:22px}}
'''

def page(title, body):
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>{html.escape(title)}</title><link rel="alternate" type="application/rss+xml" title="Мои дайджесты — Horizon" href="{ORIGIN}/feed.xml"><style>{CSS}</style></head><body id="top"><header><div class="eyebrow">Horizon · Security, AI и история технологий</div><nav><a href="{ORIGIN}/">Все выпуски</a><a href="{ORIGIN}/feed.xml">RSS для Inoreader</a></nav></header><main>{body}</main><footer>Аннотации подготовлены ИИ по материалам источников. Проверяйте важные факты по оригиналам.<br>Обновление: пятница, 12:00 · Europe/Madrid.</footer><a class="top" href="#top" aria-label="К началу страницы">↑ Наверх</a></body></html>'''

class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.hrefs=[]; self.ids=set()
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        assert tag not in {'script','iframe','object','embed'}, tag
        assert not any(k.lower().startswith('on') for k in attrs)
        if 'id' in attrs: self.ids.add(attrs['id'])
        if tag=='a' and 'href' in attrs:
            href=attrs.get('href','')
            assert href.startswith(('https://','http://','#')), href
            self.hrefs.append(href)

out=ROOT/'docs'; out.mkdir(exist_ok=True)
ET.register_namespace('content','http://purl.org/rss/1.0/modules/content/')
ET.register_namespace('atom','http://www.w3.org/2005/Atom')
rss=ET.Element('rss',version='2.0'); channel=ET.SubElement(rss,'channel')
for name,value in [('title','Мои дайджесты — Horizon'),('link',ORIGIN+'/'),('description','Security, AI и история компьютеров: до 30 материалов за неделю.'),('language','ru')]:
    ET.SubElement(channel,name).text=value
ET.SubElement(channel,'{http://www.w3.org/2005/Atom}link',href=ORIGIN+'/feed.xml',rel='self',type='application/rss+xml')
issues=[]
for source in sorted((HORIZON/'data/summaries').glob('horizon-????-??-??-ru.md'),reverse=True):
    date=source.stem.removeprefix('horizon-').removesuffix('-ru')
    text=source.read_text()
    count=len(re.findall(r'^<a id="item-',text,re.M))
    title=f'Подборка за неделю · {date}'
    # The Markdown preamble repeats headings and contains a long TOC. Export articles only.
    text=text[text.index('<a id="item-'):]
    text=re.sub(r'^<a id="([a-zA-Z0-9-]+)"></a>$',r'ANCHORPLACEHOLDER-\1-END',text,flags=re.M)
    articles=_render_email_markdown(_prepare_email_markdown(text))
    articles=re.sub(r'<p>ANCHORPLACEHOLDER-([a-zA-Z0-9-]+)-END</p>',r'<a id="\1"></a>',articles)
    body=f'<h1>{html.escape(title)}</h1><p class="meta">{count} публикаций со ссылками на оригиналы</p>'+articles
    check=Links();check.feed(body)
    assert all(link[1:] in check.ids for link in check.hrefs if link.startswith('#'))
    filename=source.stem+'.html'; url=ORIGIN+'/'+filename
    (out/filename).write_text(page(title,body))
    # RSS readers supply the title themselves; include only the actual articles.
    feed_body=articles
    assert '<details>' not in feed_body and 'Личная подборка' not in feed_body
    item=ET.SubElement(channel,'item')
    for name,value in [('title',title),('link',url),('description',f'{count} публикаций: короткие аннотации и ссылки на оригиналы.'),('pubDate',format_datetime(datetime.fromtimestamp(source.stat().st_mtime,timezone.utc))),('{http://purl.org/rss/1.0/modules/content/}encoded',feed_body)]:
        ET.SubElement(item,name).text=value
    # New edition namespace lets readers receive this focused issue alongside the old broad trial.
    ET.SubElement(item,'guid',isPermaLink='false').text='horizon:security-ai-history:'+date
    issues.append(f'<section class="issue"><div class="meta">{date} · {count} публикаций</div><a href="{url}">Подборка за неделю</a><p class="meta">Короткие аннотации и ссылки на оригинальные статьи.</p></section>')
assert issues, 'No digests found'
(out/'index.html').write_text(page('Мои дайджесты', '<h1>Читать то,<br>что интересно.</h1><p>Security, AI и история компьютеров — до 30 материалов за неделю.</p>'+''.join(issues)))
ET.indent(rss); ET.ElementTree(rss).write(out/'feed.xml',encoding='utf-8',xml_declaration=True)
assert len(ET.parse(out/'feed.xml').findall('./channel/item'))==len(issues)
assert not any(x.name in {'config.json','credentials.json','.env'} for x in out.rglob('*'))
print(f'Built {len(issues)} digest(s), HTML and full-content RSS. Anchor, link and XML checks passed.')
