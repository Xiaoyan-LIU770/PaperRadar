#!/usr/bin/env python3
"""由Codex原生临时任务调用；触发真实性还须由App运行记录交叉验证。"""
import fcntl
import hashlib
import json
import os
import sqlite3
import sys
import traceback
from pathlib import Path

from paperradar.core import ROOT,dump,now
from paperradar.tracking import DEFAULT_TOPIC,run_tracking


def main():
    target=ROOT/'tests/stage3/automation/native-test'
    authorization=json.loads((target/'authorization.json').read_text())
    if not authorization['test_only'] or Path(authorization['allowed_root']).resolve()!=target.resolve():raise ValueError('隔离授权配置不符')
    registration=target/'native_registration.json'
    if not registration.exists():raise ValueError('缺少原生调度登记证据，不能冒充定时触发')
    task=json.loads(registration.read_text())
    result_file=target/'execution_result.json'
    lock=(target/'execution.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:print('已有同一验收正在运行');return 3
    if result_file.exists():
        result=json.loads(result_file.read_text());print(json.dumps({'already_executed':True,'result':result},ensure_ascii=False));return result['exit_code']
    def event(kind,**values):
        with (target/'events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps({'event':kind,'time':now(),**values},ensure_ascii=False)+'\n')
    event('started',automation_id=task.get('automation_id'),pid=os.getpid(),cwd=os.getcwd())
    result={'started_at':now(),'test_only':True,'automation_id':task.get('automation_id'),'trigger_claim':'须与Codex计划任务运行记录交叉核实'}
    exit_code=1
    try:
        db=target/'papers.sqlite'
        before=hashlib.sha256(db.read_bytes()).hexdigest()
        m=run_tracking(db,target,DEFAULT_TOPIC,'scheduled-test',strategy='blue-peled-v2')
        report=target/'reports/weekly'/(m['run_id']+'.md')
        c=sqlite3.connect(db);persisted=json.loads(c.execute('SELECT payload FROM runs WHERE run_id=?',(m['run_id'],)).fetchone()[0]);total=c.execute('SELECT count(*) FROM papers').fetchone()[0];integrity=c.execute('PRAGMA integrity_check').fetchone()[0];c.close()
        refs=json.loads(report.with_suffix('.references.json').read_text())
        formal_same=hashlib.sha256((ROOT/'database/papers.sqlite').read_bytes()).hexdigest()==authorization['formal_sha256_before']
        valid=m['status']=='success' and persisted['run_id']==m['run_id'] and before!=hashlib.sha256(db.read_bytes()).hexdigest() and integrity=='ok' and report.exists() and refs['all_reference_ids_in_database'] and formal_same
        exit_code=0 if valid else 2
        result.update(run_id=m['run_id'],pipeline_status=m['status'],new_papers=m['new_papers'],existing_papers_seen=m['existing_papers_seen'],library_total=total,baseline_papers=authorization['baseline_papers'],database_changed=before!=hashlib.sha256(db.read_bytes()).hexdigest(),database_integrity=integrity,report=str(report),references_valid=refs['all_reference_ids_in_database'],formal_database_unchanged=formal_same,credential_source=m['credential_source'],file_and_database_checks_passed=valid)
        event('pipeline_completed',run_id=m['run_id'],status=m['status'],new_papers=m['new_papers'])
    except Exception as exc:
        # 仅记录异常类型；不把未知异常正文或环境变量写入日志。
        result.update(error_type=type(exc).__name__,pipeline_status='program_failed')
        event('program_failed',error_type=type(exc).__name__)
    finally:
        result.update(finished_at=now(),exit_code=exit_code)
        dump(result_file,result);event('finished',exit_code=exit_code)
        fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
    print(json.dumps(result,ensure_ascii=False,indent=2));return exit_code


if __name__=='__main__':raise SystemExit(main())
