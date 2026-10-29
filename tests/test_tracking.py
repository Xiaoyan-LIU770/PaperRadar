"""第二阶段隔离验收：合成记录回放；变异内容和HTTP响应均为明确测试情景。"""
import copy
import json
import sqlite3
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from paperradar.core import ROOT, http_get, norm_doi
from paperradar.tracking import DEFAULT_TOPIC, apply_reviews, open_db, papers, query_config, register_topic, run_tracking, save_tracking_run
from paperradar.weekly import generate_weekly


def successful_sources():
    return [{"source": s, "status": "success", "raw_count": 0, "eligible_date_count": 0} for s in ("arxiv", "openalex")]


class TrackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records=json.loads((ROOT/'tests/fixtures/records.json').read_text())
        cls.a=next(p for p in cls.records if '10.0000/test-data-a' in p['identifiers']['doi'])
        cls.b=next(p for p in cls.records if '10.0000/test-data-b' in p['identifiers']['doi'])
        cls.arxiv=next(p for p in cls.records if p['identifiers']['arxiv'])
        cls.cfg=json.loads((ROOT/'config/examples/blue-peled.json').read_text())

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'tests',prefix='phase2-test-')
        self.out=Path(self.temp.name);self.db=self.out/'test.sqlite';self.n=0
        register_topic(self.db,DEFAULT_TOPIC,self.cfg)

    def tearDown(self): self.temp.cleanup()

    def ingest(self, records, mode='manual', sources=None):
        self.n+=1
        m={'run_id':f'TEST-REPLAY-{self.n}','created_at':f'2026-10-{min(10+self.n,28):02d}T09:00:00+08:00','topic_id':DEFAULT_TOPIC,'topic':self.cfg,'mode':mode,'test_data':True,'sources':sources or successful_sources()}
        return save_tracking_run(self.db,copy.deepcopy(records),m)

    def library(self):
        c=open_db(self.db)
        try: return papers(c)
        finally: c.close()

    def test_baseline_repeat_and_real_new_record(self):
        first=self.ingest([self.a],mode='baseline');pid=first['new_paper_ids'][0]
        second=self.ingest([self.a]);third=self.ingest([self.a,self.b])
        self.assertEqual((first['new_papers'],second['new_papers'],third['new_papers']),(1,0,1))
        self.assertEqual((second['existing_papers_seen'],third['existing_papers_seen']),(1,1))
        self.assertEqual(self.library()[pid]['first_seen'],first['created_at'])
        self.assertEqual(third['new_paper_ids'],third['new_library_ids'])

    def test_doi_case_prefix_duplicate(self):
        self.ingest([self.a]);r=copy.deepcopy(self.a);r['identifiers']['doi']=['HTTPS://DOI.ORG/'+self.a['doi'].upper()]
        r['identifiers']['openalex']=[];r['source_records'][0]['source_id']='TEST-DATA:other-source'
        m=self.ingest([r]);self.assertEqual((m['new_papers'],m['library_total']),(0,1))

    def test_arxiv_version_duplicate(self):
        self.ingest([self.arxiv]);r=copy.deepcopy(self.arxiv)
        r['identifiers']['arxiv']=[r['arxiv_id']+'v2'];r['source_records'][0]['source_id']=r['arxiv_id']+'v2'
        r['source_records'][0]['version']=2
        m=self.ingest([r]);self.assertEqual(m['new_papers'],0);self.assertEqual(m['metadata_changed_papers'],1)

    def test_real_title_different_doi_not_merged(self):
        pair=[p for p in self.records if 'TEST DATA same-title blue' in p['title']]
        self.assertEqual(len(pair),2)
        m=self.ingest(pair);self.assertEqual(m['library_total'],2)
        c=open_db(self.db);self.assertEqual(c.execute('SELECT count(*) FROM review_pairs').fetchone()[0],1);c.close()

    def test_duplicate_relation_manual_review_not_overwritten(self):
        pair=[p for p in self.records if 'TEST DATA same-title blue' in p['title']]
        self.ingest(pair)
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE review_pairs SET status='TEST DATA: 人工确认保留独立记录'")
        m=self.ingest(pair);self.assertEqual(m['new_papers'],0)
        c=open_db(self.db)
        self.assertEqual(c.execute('SELECT status,first_run,last_run FROM review_pairs').fetchone(),('TEST DATA: 人工确认保留独立记录','TEST-REPLAY-1','TEST-REPLAY-2'));c.close()

    def test_title_only_insufficient_identity_is_review(self):
        self.ingest([self.a]);r=copy.deepcopy(self.a)
        r['identifiers']={'doi':[],'arxiv':[],'openalex':['TEST-DATA:unconfirmed-version']};r['source_records'][0]['source_id']='TEST-DATA:unconfirmed-version'
        m=self.ingest([r]);self.assertEqual(m['library_total'],2)
        c=open_db(self.db);self.assertEqual(c.execute('SELECT count(*) FROM review_pairs').fetchone()[0],1);c.close()

    def test_bridge_does_not_retire_existing_records(self):
        self.ingest([self.a,self.arxiv]);r=copy.deepcopy(self.a);r['identifiers']['arxiv']=self.arxiv['identifiers']['arxiv']
        m=self.ingest([r]);self.assertEqual(m['library_total'],2);self.assertEqual(m['unresolved_count'],1);self.assertEqual(m['new_papers'],0)

    def test_openalex_doi_conflict_is_quarantined(self):
        self.ingest([self.a]);r=copy.deepcopy(self.a);r['identifiers']['doi']=['10.0000/test-data-only']
        m=self.ingest([r]);self.assertEqual(m['unresolved_count'],1);self.assertEqual(m['library_total'],1)

    def test_metadata_change_once_and_preserve_manual_evidence(self):
        m=self.ingest([self.a]);pid=m['new_paper_ids'][0];lib=self.library();p=lib[pid]
        p['relevance']['status']='Codex已逐条复核';p['manual_note']='TEST DATA: retain review';before=copy.deepcopy(p)
        with sqlite3.connect(self.db) as c:c.execute('UPDATE papers SET payload=? WHERE paper_id=?',(json.dumps(p),pid))
        r=copy.deepcopy(self.a);r['abstract']+=' TEST DATA: metadata update only.';r['source_records'][0]['abstract']=r['abstract']
        changed=self.ingest([r]);again=self.ingest([r]);after=self.library()[pid]
        self.assertEqual((changed['metadata_changed_papers'],again['metadata_changed_papers']),(1,0))
        for k in ('first_seen','relevance','manual_note','abstract'):self.assertEqual(after[k],before[k])
        c=open_db(self.db);self.assertEqual(c.execute('SELECT count(*) FROM metadata_changes').fetchone()[0],1);c.close()

    def test_existing_unseen_preserved(self):
        self.ingest([self.a]);self.ingest([self.b]);self.assertEqual(len(self.library()),2)

    def test_api_failure_not_zero_and_report_incomplete(self):
        sources=[{'source':s,'status':'failed','raw_count':None,'error':'TEST HTTP 503'} for s in ('arxiv','openalex')]
        m=self.ingest([],sources=sources);self.assertEqual(m['status'],'failed');self.assertIsNone(m['new_papers'])
        path=self.out/'failed.md';proof=generate_weekly(self.db,m['run_id'],path)
        self.assertIn('新增总数无法判断',path.read_text());self.assertFalse(proof['new_papers']==0)

    def test_partial_failure_and_retry(self):
        s=[{'source':'arxiv','status':'failed','raw_count':None,'error':'TEST HTTP 429'}, {'source':'openalex','status':'success','raw_count':1}]
        a=self.ingest([self.a],sources=s);b=self.ingest([self.a,self.b])
        self.assertEqual((a['status'],b['status']),('partial_failure','success'))
        self.assertEqual((a['new_papers'],b['new_papers']),(1,1))
        path=self.out/'partial.md';generate_weekly(self.db,a['run_id'],path);self.assertIn('本报告不完整',path.read_text())

    def test_no_new_report_and_reference_integrity(self):
        self.ingest([self.a]);m=self.ingest([self.a]);path=self.out/'none.md'
        proof=generate_weekly(self.db,m['run_id'],path);self.assertIn('本期无新增',path.read_text());self.assertTrue(proof['all_reference_ids_in_database'])

    def test_real_new_report_references_exist_and_abstract_labels(self):
        self.ingest([self.arxiv]);m=self.ingest([self.a,self.b]);path=self.out/'new.md'
        card={'paper_id':m['new_paper_ids'][0], 'evidence_quote':'TEST DATA ONLY', 'question':'测试问题','method':'测试方法','finding':'测试发现','author_proposed_feature':'测试内容'}
        with patch('paperradar.weekly.analysis_cards',return_value={card['paper_id']:card}):
            proof=generate_weekly(self.db,m['run_id'],path)
        self.assertEqual(len(proof['references']),2);self.assertIn('基于摘要分析',path.read_text())
        for r in proof['references']:
            self.assertIn(r['paper_id'],self.library());self.assertIn(r['identifiers']['doi'][0],path.read_text())

    def test_same_run_cannot_overwrite(self):
        m=self.ingest([self.a])
        with self.assertRaises(ValueError):save_tracking_run(self.db,[self.a],m)
        self.assertEqual(len(self.library()),1)

    def test_baseline_not_allowed_after_existing_topic(self):
        self.ingest([self.a],mode='baseline')
        with self.assertRaises(ValueError):self.ingest([self.b],mode='baseline')
        self.assertEqual(len(self.library()),1)

    def test_unknown_topic_not_using_blue_rules(self):
        cfg={**self.cfg,'topic_zh':'TEST DATA: new unrelated topic','screening_version':'generic'}
        register_topic(self.db,'test-other-topic',cfg)
        m={'run_id':'TEST-OTHER','created_at':'2026-10-21T09:00:00+08:00','topic_id':'test-other-topic','topic':cfg,'mode':'baseline','test_data':True,'sources':successful_sources()}
        save_tracking_run(self.db,[self.a],m);self.assertEqual(m['new_relevance_counts']['待筛选'],1)
        old=self.ingest([self.a]);self.assertEqual(old['new_library_records'],0);self.assertEqual(old['new_papers'],1)
        self.assertEqual(old['new_relevance_counts']['高'],1)

    def test_cli_history_and_report_execute_from_scripts(self):
        m=self.ingest([self.a]);base=[sys.executable,'-B',str(ROOT/'scripts/radar.py'),'--db',str(self.db),'--output-root',str(self.out)]
        history=subprocess.run(base+['history'],capture_output=True,text=True,cwd=ROOT)
        self.assertEqual(history.returncode,0,history.stderr)
        self.assertEqual(json.loads(history.stdout)[0]['run_id'],m['run_id'])
        report=subprocess.run(base+['report'],capture_output=True,text=True,cwd=ROOT)
        self.assertEqual(report.returncode,0,report.stderr)
        self.assertTrue(json.loads(report.stdout)['all_reference_ids_in_database'])

    def test_missing_abstract_count_in_no_new_report(self):
        self.ingest([self.a]);sources=successful_sources();sources[1]['missing_abstract_count']=2
        m=self.ingest([self.a],sources=sources);path=self.out/'missing.md'
        generate_weekly(self.db,m['run_id'],path)
        self.assertIn('缺摘要：2',path.read_text())

    def test_review_protection_and_bad_evidence_rejected(self):
        m=self.ingest([self.a]);pid=m['new_paper_ids'][0];f=self.out/'review.json'
        decision={'level':'高','reason':'TEST DATA: reviewer','evidence':[{'field':'title','quote':self.a['title']}],'recommend_close_reading':True}
        f.write_text(json.dumps({pid:decision}));apply_reviews(self.db,DEFAULT_TOPIC,f)
        with self.assertRaises(ValueError):apply_reviews(self.db,DEFAULT_TOPIC,f)
        n=self.ingest([self.b]);other=n['new_paper_ids'][0];decision['evidence'][0]['quote']='TEST DATA: nonexistent quote';f.write_text(json.dumps({other:decision}))
        with self.assertRaises(ValueError):apply_reviews(self.db,DEFAULT_TOPIC,f)

    def test_failure_does_not_advance_window(self):
        c=open_db(self.db);q1=query_config(c,DEFAULT_TOPIC,'manual');c.close()
        self.ingest([],sources=[{'source':s,'status':'failed','raw_count':None} for s in ('arxiv','openalex')])
        c=open_db(self.db);q2=query_config(c,DEFAULT_TOPIC,'manual');c.close();self.assertEqual(q1,q2)

    def test_replay_prohibited_in_formal_db(self):
        with self.assertRaises(ValueError):run_tracking(ROOT/'database/papers.sqlite',self.out,DEFAULT_TOPIC,snapshot='TEST-DATA')

    def test_isolated_db_cannot_overwrite_formal_export(self):
        with self.assertRaises(ValueError):run_tracking(self.db,ROOT,DEFAULT_TOPIC)

    def fake_http(self, codes):
        count={'n':0}
        def fake(command,**kwargs):
            idx=count['n'];count['n']+=1;status=codes[min(idx,len(codes)-1)]
            Path(command[command.index('--output')+1]).write_text('TEST DATA ONLY: simulated HTTP response')
            Path(command[command.index('--dump-header')+1]).write_text('HTTP/2 '+str(status)+'\r\nRetry-After: 1\r\n')
            return SimpleNamespace(stdout=str(status),stderr='',returncode=0)
        return fake,count

    def test_http429_retries_once_then_failure_logged(self):
        fake,count=self.fake_http([429,429])
        with patch('paperradar.core.subprocess.run',side_effect=fake),patch('paperradar.core.time.sleep'):
            with self.assertRaises(RuntimeError):http_get('https://api.openalex.org/works',self.out,'openalex')
        self.assertEqual(count['n'],2);log=json.loads((self.out/'openalex.request.json').read_text());self.assertEqual([x['http_status'] for x in log['attempts']],[429,429])

    def test_http503_recovers_on_retry(self):
        fake,count=self.fake_http([503,200])
        with patch('paperradar.core.subprocess.run',side_effect=fake),patch('paperradar.core.time.sleep'):
            raw,attempts=http_get('https://export.arxiv.org/api/query',self.out,'arxiv')
        self.assertTrue(raw.exists());self.assertEqual([a['http_status'] for a in attempts],[503,200]);self.assertEqual(count['n'],2)

    def test_http503_exhausted(self):
        fake,count=self.fake_http([503,503])
        with patch('paperradar.core.subprocess.run',side_effect=fake),patch('paperradar.core.time.sleep'):
            with self.assertRaises(RuntimeError):http_get('https://export.arxiv.org/api/query',self.out,'arxiv')
        self.assertEqual(count['n'],2)


if __name__=='__main__':unittest.main(verbosity=2)
