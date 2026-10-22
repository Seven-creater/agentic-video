"""Technical capture admission: a fresh real topic and its actual primary video.

Template similarity is a later quality judgment. This path makes only normal,
idempotent public reads and preserves their raw responses; no paid review is
replayed or represented as completed.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

from .public_capture import PublicSource
from .hot_capture import _download, video_record
from ..library.state import LibraryStopped, write_json
from ..library.media import sha256_file
from ..library.reliable import read, now


def capture_reference(home, output, *, exclude_trend_ids=(), exclude_reference_sha256s=(),
                      freshness_hours=72, max_candidates=10, visual_library=None):
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    api = PublicSource(home,output/'public_source')
    payload = None
    failures=[]
    for attempt in range(3):
        try:
            payload=api._board()
            break
        except (OSError,ValueError) as error:
            failures.append(dict(stage='board',error=type(error).__name__,attempt=attempt))
            write_json(output/'capture_attempts.json',failures)
            if attempt<2: time.sleep(2**attempt)
    if payload is None:
        raise LibraryStopped('reliable_live_board_unavailable')
    stamp=datetime.strptime(str(payload['data']['last_update_time']),'%Y%m%d%H%M%S').replace(
        tzinfo=timezone(timedelta(hours=8)))
    instant=datetime.now(timezone.utc)
    if not 0 <= (instant-stamp).total_seconds() <= freshness_hours*3600:
        raise LibraryStopped('reliable_live_board_stale')
    used={str(x) for x in exclude_trend_ids}
    trends=[t for t in payload['data']['objs'] if str(t['sentence_id']) not in used]
    if visual_library:
        from ..library.visual_library import _projection,_terms
        index=[set(facts)|set(uses) for p in (Path(visual_library)/'observations').glob('*.json')
               for _,facts,uses in [_projection(read(p))]]
        def rank(t):
            terms={w for w in _terms(t['sentence']) if len(w)>1}
            return max((len(terms.intersection(tokens)) for tokens in index),default=0)
        trends.sort(key=lambda t:(-rank(t),t.get('rank',100000)))
    for trend in trends[:max_candidates]:
        tid=str(trend['sentence_id'])
        try:
            api.prepare_trend(trend,freshness_hours=freshness_hours)
            details=api._topic(tid)
            if not details: continue
            # Recommendations do not prove membership in this specific topic.
            record=video_record(dict(data=dict(aweme_detail=details[0])))
            if not record['is_video'] or not 0 <= instant.timestamp()-record['published_at'] <= freshness_hours*3600:
                continue
            downloaded=_download(record,output/'videos',max_video_bytes=32*1024**2,min_free_bytes=2*1024**3)
            if downloaded['sha256'] in exclude_reference_sha256s: continue
            result=dict(path=downloaded['path'],sha256=downloaded['sha256'],
                source_url='https://www.douyin.com/video/'+record['id'],
                evidence=dict(trend=trend,captured_at=now(),board_updated_at=stamp.isoformat(),
                    public_ledger_path=str(api.ledger),public_ledger_sha256=sha256_file(api.ledger),
                    quality_status='unassessed',template_similarity='unassessed',
                    admission='fresh_public_topic_bound_primary_video'))
            write_json(output/'capture_receipt.json',result)
            return result
        except (LibraryStopped,OSError,ValueError,KeyError) as error:
            failures.append(dict(stage='topic',trend_id=tid,error=str(error)[:300]))
            write_json(output/'capture_attempts.json',failures)
    raise LibraryStopped('reliable_no_recent_topic_bound_video')
