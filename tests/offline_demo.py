"""TEST DATA ONLY：模拟网络的检索→解析→存储→重复运行→报告闭环。"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from paperradar.core import ROOT
from paperradar.tracking import register_topic, run_tracking, open_db

def run_demo():
    with tempfile.TemporaryDirectory(prefix='demo-',dir=ROOT/'tests') as temp:
        out=Path(temp);db=out/'test.sqlite'
        cfg=json.loads((ROOT/'config/examples/molecular-gnn.json').read_text())
        register_topic(db,'test-generic',cfg)
        def fake_http(url,directory,source):
            p=Path(directory)/(source+'.attempt-1.raw')
            if source=='arxiv':
                p.write_text('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/"><o:totalResults>0</o:totalResults></feed>')
            else:
                p.write_text(json.dumps({'meta':{'count':1},'results':[{'id':'https://openalex.org/W0000000','title':'TEST DATA ONLY: synthetic molecular graph','publication_date':'2025-01-01','abstract_inverted_index':{'TEST':[0],'DATA':[1],'ONLY':[2]}}]}))
            return p,[]
        with patch('paperradar.core.http_get',side_effect=fake_http),patch('paperradar.credentials.load_openalex_key',return_value=(None,'test_mock')):
            first=run_tracking(db,out,'test-generic',mode='baseline')
            second=run_tracking(db,out,'test-generic',mode='manual',full_range=True)
        assert first['new_papers']==1 and second['new_papers']==0
        assert first['new_relevance_counts']['待筛选']==1
        conn=open_db(db)
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert conn.execute('SELECT count(*) FROM runs').fetchone()[0]==2
        conn.close()
        assert (out/'reports/weekly'/(second['run_id']+'.md')).exists()
        print('离线闭环通过：解析、首次新增1、重复新增0、通用主题待筛选、SQLite完整性、报告。')

if __name__=='__main__':run_demo()
