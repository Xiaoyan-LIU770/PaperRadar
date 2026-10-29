"""Stage 3隔离测试；模拟网络与钥匙串，不使用任何真实凭证。"""
import copy
import json
import os
import contextlib
import io
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from paperradar.core import ROOT,collect_source
from paperradar.credentials import load_openalex_key,store_openalex_key
from paperradar.search_strategy import GROUPS,collect_optimized,windows
from paperradar.tracking import DEFAULT_TOPIC,register_topic,run_tracking,save_tracking_run
from paperradar.weekly import generate_weekly


class Stage3Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'tests',prefix='stage3-test-');self.out=Path(self.temp.name)
        self.cfg=json.loads((ROOT/'config/examples/blue-peled.json').read_text());self.cfg.update(date_from='2026-10-01',date_to='2026-10-14')
        self.record=json.loads((ROOT/'tests/fixtures/records.json').read_text())[0]
    def tearDown(self):self.temp.cleanup()
    def success(self,total=1):return {'source':'openalex','status':'success','raw_count':1,'eligible_date_count':1,'missing_abstract_count':0,'reported_total':total,'stopped_at_limit':total>50,'meta':{'cost_usd':.001}}

    def test_windows_no_overlap_and_no_day_missing(self):
        self.assertEqual(windows('2026-10-01','2026-10-15',7),[('2026-10-01','2026-10-07'),('2026-10-08','2026-10-14'),('2026-10-15','2026-10-15')])
    def test_invalid_window(self):
        with self.assertRaises(ValueError):windows('2026-10-02','2026-10-01')
        with self.assertRaises(ValueError):windows('2026-10-01','2026-10-01',0)
    def test_multi_groups_scoped_search_relevance_and_limits(self):
        calls=[]
        def fake(source,cfg,directory):calls.append(cfg);return [copy.deepcopy(self.record)],self.success()
        with patch('paperradar.search_strategy.collect_source',side_effect=fake):records,s=collect_optimized(self.cfg,self.out)
        self.assertEqual(len(calls),4);self.assertEqual(len(records),4)
        self.assertTrue(all(c['openalex_search_field']=='search.title_and_abstract' and c['openalex_sort']=='relevance_score:desc' and c['max_per_source']==50 for c in calls))
        self.assertEqual(s['status'],'success');self.assertAlmostEqual(s['cost_usd_reported'],.004)
    def test_adaptive_split_retains_parent_and_covers_children(self):
        calls=[]
        def fake(source,cfg,directory):
            calls.append((cfg['date_from'],cfg['date_to']))
            return [copy.deepcopy(self.record)],self.success(60 if len(calls)==1 else 1)
        cfg={**self.cfg,'date_to':'2026-10-07'}
        with patch('paperradar.search_strategy.collect_source',side_effect=fake):records,s=collect_optimized(cfg,self.out,groups=GROUPS[:1])
        self.assertEqual(calls,[('2026-10-01','2026-10-07'),('2026-10-01','2026-10-04'),('2026-10-05','2026-10-07')])
        self.assertEqual(len(records),3);self.assertEqual(s['coverage_gaps'],[])
    def test_budget_gap_is_explicit(self):
        with patch('paperradar.search_strategy.collect_source',return_value=([self.record],self.success())):_,s=collect_optimized(self.cfg,self.out,max_requests=1)
        self.assertEqual(s['query_count'],1);self.assertTrue(s['coverage_gaps']);self.assertTrue(s['stopped_at_limit'])
    def test_429_stops_remaining_queries_without_claim_zero(self):
        failed={'source':'openalex','status':'failed','raw_count':None,'error':'TEST HTTP 429'}
        with patch('paperradar.search_strategy.collect_source',return_value=([],failed)) as mock:_,s=collect_optimized(self.cfg,self.out)
        self.assertEqual(mock.call_count,1);self.assertEqual(s['status'],'failed');self.assertIsNone(s['raw_count']);self.assertTrue(s['coverage_gaps'])
    def test_partial_requests_preserve_successful_records(self):
        failed={'source':'openalex','status':'failed','raw_count':None,'error':'TEST HTTP 503'}
        with patch('paperradar.search_strategy.collect_source',side_effect=[([self.record],self.success()),([],failed)]):rr,s=collect_optimized({**self.cfg,'date_to':'2026-10-07'},self.out)
        self.assertEqual(len(rr),1);self.assertEqual(s['status'],'partial_failure');self.assertEqual(s['successful_request_count'],1)
    def test_partial_aggregate_with_other_source_failed_not_all_failed(self):
        db=self.out/'test.sqlite';register_topic(db,DEFAULT_TOPIC,self.cfg)
        sources=[{'source':'openalex','status':'partial_failure','successful_request_count':1},{'source':'arxiv','status':'failed','raw_count':None}]
        m={'run_id':'TEST-PARTIAL-AGG','topic_id':DEFAULT_TOPIC,'topic':self.cfg,'mode':'manual','test_data':True,'created_at':'2026-10-22T09:00:00+08:00','sources':sources}
        save_tracking_run(db,[copy.deepcopy(self.record)],m)
        self.assertEqual(m['status'],'partial_failure');self.assertEqual(m['new_papers'],1)
    def test_scheduled_test_never_writes_formal_db(self):
        with self.assertRaises(ValueError):run_tracking(ROOT/'database/papers.sqlite',self.out,DEFAULT_TOPIC,mode='scheduled-test')
    def test_keychain_not_read_without_consent_policy(self):
        with patch.dict(os.environ,{},clear=True),patch('paperradar.credentials.POLICY',self.out/'missing.json'),patch('paperradar.credentials.subprocess.run') as mock:
            key,source=load_openalex_key()
        self.assertIsNone(key);self.assertEqual(source,'not_configured');mock.assert_not_called()
    def test_environment_preferred_to_keychain(self):
        with patch.dict(os.environ,{'OPENALEX_API_KEY':'TEST_DUMMY_KEY_NOT_REAL'}),patch('paperradar.credentials.subprocess.run') as mock:
            key,source=load_openalex_key()
        self.assertEqual(source,'environment');mock.assert_not_called()
    def test_keychain_save_no_secret_in_argv_or_policy(self):
        key='TEST_DUMMY_KEY_NOT_REAL';policy=self.out/'policy.json';calls=[]
        def fake(args,**kwargs):
            calls.append((args,kwargs));return SimpleNamespace(returncode=0,stdout=key if 'find-generic-password' in args else '',stderr='')
        with patch('paperradar.credentials.POLICY',policy),patch('paperradar.credentials.subprocess.run',side_effect=fake):store_openalex_key(key)
        self.assertNotIn(key,str([c[0] for c in calls]));self.assertNotIn(key,policy.read_text());self.assertTrue(json.loads(policy.read_text())['keychain_enabled'])
    def test_save_not_trusted_until_readback_matches(self):
        policy=self.out/'policy.json'
        with patch('paperradar.credentials.POLICY',policy),patch('paperradar.credentials.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='',stderr='')):
            with self.assertRaises(RuntimeError):store_openalex_key('TEST_DUMMY_KEY_NOT_REAL')
        self.assertFalse(policy.exists())
    def test_scheduled_report_does_not_claim_historical_replay(self):
        db=self.out/'test.sqlite';register_topic(db,DEFAULT_TOPIC,self.cfg)
        m={'run_id':'TEST-SCHEDULED','topic_id':DEFAULT_TOPIC,'topic':self.cfg,'mode':'scheduled-test','test_data':True,'created_at':'2026-10-22T09:00:00+08:00','sources':[self.success()]}
        save_tracking_run(db,[],m);path=self.out/'report.md';generate_weekly(db,m['run_id'],path)
        self.assertIn('真实接口',path.read_text());self.assertNotIn('测试回放：',path.read_text())
    def test_native_scoped_parameter_is_sent_to_api(self):
        cfg={**self.cfg,'openalex_search_field':'search.title_and_abstract'}
        with patch('paperradar.core.http_get',side_effect=RuntimeError('TEST STOP')) as mock:collect_source('openalex',cfg,self.out)
        self.assertIn('search.title_and_abstract=',mock.call_args.args[0]);self.assertNotIn('?search=',mock.call_args.args[0])

    def test_baseline_and_full_range_keep_legacy_recovery(self):
        db=self.out/'fallback.sqlite';register_topic(db,DEFAULT_TOPIC,self.cfg)
        empty={'source':'TEST-MOCK','status':'success','raw_count':0,'eligible_date_count':0}
        with patch('paperradar.search_strategy.active_strategy',return_value='blue-peled-v2'),patch('paperradar.credentials.load_openalex_key',return_value=(None,'TEST-MOCK')),patch('paperradar.tracking.collect_source',return_value=([],empty)),patch('paperradar.search_strategy.collect_optimized') as optimized:
            baseline=run_tracking(db,self.out,DEFAULT_TOPIC,mode='baseline')
            backfill=run_tracking(db,self.out,DEFAULT_TOPIC,full_range=True)
        self.assertEqual(baseline['search_strategy'],'legacy-v1');self.assertEqual(backfill['search_strategy'],'legacy-v1');optimized.assert_not_called()

    def test_isolated_analysis_cards_do_not_change_official_cards(self):
        from paperradar.weekly import analysis_cards
        extra=self.out/'isolated-cards.json';extra.write_text(json.dumps([{'paper_id':'TEST-DATA-ONLY','method':'TEST DATA ONLY'}]))
        self.assertIn('TEST-DATA-ONLY',analysis_cards(extra));self.assertNotIn('TEST-DATA-ONLY',analysis_cards())

    def test_network_timeout_is_failure_not_zero(self):
        with patch('paperradar.core.http_get',side_effect=subprocess.TimeoutExpired(['TEST-CURL-NO-CREDENTIALS'],50)):
            records,summary=collect_source('openalex',self.cfg,self.out)
        self.assertEqual(records,[]);self.assertEqual(summary['status'],'failed');self.assertIsNone(summary['raw_count']);self.assertIn('timed out',summary['error'])

    def test_keychain_timeout_requires_attention(self):
        policy=self.out/'policy.json';policy.write_text('{"keychain_enabled":true}')
        with patch.dict(os.environ,{},clear=True),patch('paperradar.credentials.POLICY',policy),patch('paperradar.credentials.subprocess.run',side_effect=subprocess.TimeoutExpired(['TEST-SECURITY'],12)):
            key,source=load_openalex_key()
        self.assertIsNone(key);self.assertIn('interaction_required',source)

    def test_program_failure_still_records_start_and_end(self):
        namespace=runpy.run_path(str(ROOT/'tests/helpers/native_scheduled_test.py'));main=namespace['main']
        target=self.out/'tests/stage3/automation/native-test';target.mkdir(parents=True)
        (target/'authorization.json').write_text(json.dumps({'test_only':True,'allowed_root':str(target)}))
        (target/'native_registration.json').write_text('{"automation_id":"TEST-DATA-ONLY-NOT-A-REAL-AUTOMATION"}')
        (target/'papers.sqlite').write_bytes(b'TEST DATA ONLY: no actual database used in failure simulation')
        with patch.dict(main.__globals__,{'ROOT':self.out,'run_tracking':lambda *a,**k:(_ for _ in ()).throw(RuntimeError('TEST DATA: injected program failure'))}),contextlib.redirect_stdout(io.StringIO()):
            code=main()
        result=json.loads((target/'execution_result.json').read_text());events=[json.loads(x)['event'] for x in (target/'events.jsonl').read_text().splitlines()]
        self.assertEqual(code,1);self.assertEqual(result['pipeline_status'],'program_failed');self.assertEqual(events,['started','program_failed','finished'])


if __name__=='__main__':unittest.main(verbosity=2)
