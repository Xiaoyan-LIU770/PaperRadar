"""有界多组检索与不重叠日期窗口；复用真实接口与既有初筛标准。"""
import copy
import datetime as dt
import json
import re
from pathlib import Path

from .core import ROOT, collect_source, dump, now

GROUPS = [
    {"id":"device", "query":'blue AND perovskite AND ("light emitting diode" OR "light-emitting diode" OR PeLED OR electroluminescence)'},
    {"id":"stability", "query":'blue AND perovskite AND (stability OR lifetime OR degradation OR "ion migration" OR segregation)'},
]


def windows(start, end, days=7):
    if days < 1: raise ValueError("窗口天数须为正数")
    first,last=dt.date.fromisoformat(start),dt.date.fromisoformat(end)
    if first>last:raise ValueError("起始日期晚于截止日期")
    result=[]
    while first<=last:
        stop=min(last,first+dt.timedelta(days=days-1))
        result.append((first.isoformat(),stop.isoformat()));first=stop+dt.timedelta(days=1)
    return result


def collect_optimized(config,directory,groups=None,window_days=7,max_requests=24,adaptive=True):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    groups=groups or config.get('openalex_groups') or GROUPS
    if not all(re.fullmatch(r'[a-z0-9_-]{1,40}', g['id']) and isinstance(g['query'], str) and g['query'].strip() for g in groups):
        raise ValueError('检索组须含安全的id与非空query')
    queue=[(g,a,b) for a,b in reversed(windows(config['date_from'],config['date_to'],window_days)) for g in groups]
    records,requests,gaps=[],[],[];started=now();count=0
    while queue and count<max_requests:
        group,a,b=queue.pop(0);count+=1
        sub=directory/f'{count:02d}-{group["id"]}-{a}-{b}';sub.mkdir()
        cfg={**config,'date_from':a,'date_to':b,'openalex_search':group['query'],'openalex_search_field':'search.title_and_abstract','openalex_sort':'relevance_score:desc'}
        batch,details=collect_source('openalex',cfg,sub)
        details.update(query_group=group['id'],query_id=sub.name)
        request_log=sub/'openalex.request.json'
        details['http_attempt_count']=len(json.loads(request_log.read_text())['attempts']) if request_log.exists() else 0
        records.extend(batch);requests.append(details)
        if details['status']!='success':
            gaps.append({'group':group['id'],'date_from':a,'date_to':b,'reason':'request_failed'})
            # 日额度不足时停止余下请求，避免无意义重试每个窗口。
            if '429' in details.get('error',''):
                gaps.extend({'group':g['id'],'date_from':x,'date_to':y,'reason':'stopped_after_429'} for g,x,y in queue);queue=[]
            continue
        if details.get('stopped_at_limit'):
            first,last=dt.date.fromisoformat(a),dt.date.fromisoformat(b)
            if first<last and adaptive:
                mid=first+(last-first)//2
                queue[0:0]=[(group,first.isoformat(),mid.isoformat()),(group,(mid+dt.timedelta(days=1)).isoformat(),last.isoformat())]
            else:gaps.append({'group':group['id'],'date_from':a,'date_to':b,'reason':'single_day_exceeds_50' if first==last else 'window_exceeds_50_no_split'})
    gaps.extend({'group':g['id'],'date_from':a,'date_to':b,'reason':'request_budget_exhausted'} for g,a,b in queue)
    successes=sum(d['status']=='success' for d in requests)
    status='success' if successes==len(requests) and requests else 'partial_failure' if successes else 'failed'
    costs=[d.get('meta',{}).get('cost_usd') for d in requests]
    summary={'source':'openalex','strategy':'blue-peled-v2','status':status,'started_at':started,'finished_at':now(),'parameters':{'search_field':'search.title_and_abstract','groups':groups,'window_days':window_days,'max_per_request':config['max_per_source'],'max_requests':max_requests,'sort':'relevance_score:desc'},'raw_count':sum(d.get('raw_count') or 0 for d in requests) if successes else None,'eligible_date_count':len(records),'missing_abstract_count':sum(not p.get('abstract') for p in records),'query_count':len(requests),'successful_request_count':successes,'requests':requests,'coverage_gaps':gaps,'stopped_at_limit':bool(gaps),'reported_total':None,'cost_usd_reported':sum(c for c in costs if isinstance(c,(float,int))),'cost_reported_for_all_requests':all(isinstance(c,(float,int)) for c in costs),'coverage_note':'数量为多组及窗口返回记录之和，含跨请求重复；未声明穷尽领域。'}
    if status!='success':summary['error']='至少一组OpenAlex请求失败，详见requests与coverage_gaps'
    dump(directory/'openalex.aggregate.json',summary);dump(directory/'openalex.all.normalized.json',records)
    return records,summary


def active_strategy(topic_id):
    path=ROOT/'config/active_search_strategy.json'
    return json.loads(path.read_text()).get(topic_id,'legacy-v1') if path.exists() else 'legacy-v1'
