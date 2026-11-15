import copy
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET
from daily_feed import merge_decisions, export


class DailyFeedTest(unittest.TestCase):
    def setUp(self):
        self.state={'articles':[],'processed_ids':[]}
        self.posts={i:{'id':i,'url':'https://t.me/test/'+i,'channel':'test','title':'Original headline '+i,
                       'text':'Author exact original words '+i,'original_html':'<b>Author exact original words '+i+'</b><br><a href="https://example.com/article?x=1">Original link</a><script>unsafe()</script>'} for i in ['1','2','3','4']}
        self.result={'items':[
            {'post_ids':['1','2'],'score':8,'reason':'Relevant','duplicate_of':None},
            {'post_ids':['3'],'score':1,'reason':'Low personal interest','duplicate_of':None}],
            'ads':[{'post_id':'4','reason':'Explicit promo code and paid placement'}]}
        self.config={'title':'Test feed','min_score':None}

    def test_low_scores_ads_dedup_and_next_day(self):
        merge_decisions(self.state,self.result,self.posts,'2026-11-15T12:00:00+01:00')
        self.assertEqual(len(self.state['articles']),2)
        self.assertEqual(len(self.state['processed_ids']),4)
        self.assertEqual(len(self.state['articles'][0]['sources']),2)
        old_id=self.state['articles'][0]['id']
        next_post={'5':{'id':'5','url':'https://t.me/test/5','channel':'test'}}
        result={'items':[dict(self.result['items'][0],post_ids=['5'],duplicate_of=old_id)],'ads':[]}
        merge_decisions(self.state,result,next_post,'2026-11-16T12:00:00+01:00')
        self.assertEqual(len(self.state['articles']),2)
        self.assertEqual(self.state['articles'][0]['published_at'],'2026-11-15T12:00:00+01:00')
        self.assertEqual(len(self.state['articles'][0]['sources']),3)
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            self.assertEqual(export(self.state,self.config,out),2)
            items=ET.parse(out/'tech-news.xml').findall('./channel/item')
            content=items[0].findtext('{http://purl.org/rss/1.0/modules/content/}encoded')
            self.assertIn('<b>Author exact original words 1</b>',content)
            self.assertIn('href="https://example.com/article?x=1"',content)
            self.assertNotIn('<script>',content)
            self.assertNotIn('unsafe()',content)
            self.assertNotIn('Relevant',content)
            self.assertEqual(items[0].findtext('title'),'[8/10] Original headline 1')
            ids=[i.findtext('guid') for i in items]
            export(self.state,self.config,out)
            self.assertEqual(ids,[i.findtext('guid') for i in ET.parse(out/'tech-news.xml').findall('./channel/item')])
            self.config['min_score']=6
            self.assertEqual(export(self.state,self.config,out),1)
            self.assertEqual(len(self.state['articles']),2)

    def test_incomplete_or_invalid_ai_output_cannot_change_archive(self):
        for mode in ['missing','duplicate','bad_score','unknown_existing']:
            result=copy.deepcopy(self.result)
            if mode=='missing': result['ads']=[]
            if mode=='duplicate': result['items'][0]['post_ids'].append('3')
            if mode=='bad_score': result['items'][0]['score']=11
            if mode=='unknown_existing': result['items'][0]['duplicate_of']='invented'
            with self.assertRaises(ValueError):
                merge_decisions(self.state,result,self.posts,'2026-11-15T12:00:00+01:00')
            self.assertEqual(self.state,{'articles':[],'processed_ids':[]})


if __name__=='__main__':
    unittest.main()
