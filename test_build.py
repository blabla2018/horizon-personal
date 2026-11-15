"""Check archive retention and RSS identity across two weekly publications."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET


def issue(*articles):
    return '# Horizon Daily\n\n'+''.join(
        f'<a id="item-test-{i}"></a>\n### [{title}]({url}) ⭐️ 8.0/10\n\n{summary}\n\n---\n\n'
        for i,(title,url,summary) in enumerate(articles,1))


class FeedArchiveTest(unittest.TestCase):
    def test_second_week_preserves_old_entries_and_deduplicates_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            shutil.copy(Path(__file__).with_name('build.py'),root/'build.py')
            summaries=root/'data/summaries'; summaries.mkdir(parents=True)
            (summaries/'horizon-2026-11-15-ru.md').write_text(issue(
                ('First title','https://example.com/story?id=7&utm_source=rss#top','First annotation'),
                ('Telegram original','https://t.me/it_secur/5292','Telegram annotation')))

            def build():
                subprocess.run([sys.executable,str(root/'build.py')],check=True,capture_output=True)
                return ET.parse(root/'docs/news.xml').findall('./channel/item')

            first=build()
            previous={x.findtext('guid'):x.findtext('pubDate') for x in first}
            (summaries/'horizon-2026-11-22-ru.md').write_text(issue(
                ('Changed headline','http://www.example.com/story?utm_campaign=weekly&id=7','Changed annotation'),
                ('Telegram preview','https://t.me/s/it_secur/5292','Repeated post'),
                ('New article','https://example.com/new','New annotation')))
            second=build()
            self.assertEqual(len(second),3)
            current={x.findtext('guid'):x.findtext('pubDate') for x in second}
            self.assertTrue(previous.items() <= current.items())
            self.assertEqual(sum(x.findtext('title')=='First title' for x in second),1)
            self.assertFalse(any(x.findtext('title')=='Changed headline' for x in second))
            self.assertEqual(len(ET.parse(root/'docs/feed.xml').findall('./channel/item')),2)
            before=(root/'docs/news.xml').read_bytes()
            build()
            self.assertEqual(before,(root/'docs/news.xml').read_bytes())


if __name__=='__main__':
    unittest.main()
