"""Public-source provenance and the shared paid/AV admission boundaries."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from omni_story.discovery import hot_capture, public_capture as public
from omni_story.library.state import LibraryStopped


def board(now):
    return dict(status_code=0, data=dict(active_time=now.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'),
        word_list=[dict(word='one actual trend', sentence_id='123', position=1, hot_value=9, video_count=3)]))


def test_public_board_uses_actual_update_time_and_rejects_error():
    now = datetime.now(timezone.utc)
    result = public.board_data(board(now))['data']
    assert result['last_update_time'] == now.astimezone(timezone(timedelta(hours=8))).strftime('%Y%m%d%H%M%S')
    assert result['objs'][0]['sentence_id'] == '123'
    with pytest.raises(LibraryStopped, match='platform_error'):
        public.board_data(dict(status_code=8))


def test_board_includes_actual_rising_topics_without_duplicate_main_entries():
    payload=board(datetime.now(timezone.utc))
    payload['data']['trending_list']=[dict(word='actual rising',sentence_id='789'),
                                    dict(word='duplicate',sentence_id='123')]
    result=public.board_data(payload)['data']['objs']
    assert [r['sentence_id'] for r in result]==['123','789']
    assert result[1]['public_board_section']=='trending_list'


def test_received_flight_json_requires_matching_topic_and_never_executes_script(tmp_path):
    props = dict(hotInfo=dict(hotId='123'), aweme=dict(detail=dict(awemeId='456')))
    fragment = '7:' + json.dumps(['$', '$L9', None, props])
    document = '<script>self.__pace_f.push(' + json.dumps([1, fragment]) + ')</script>'
    assert list(public.rendered_details(document, '123')) == [dict(awemeId='456')]
    assert list(public.rendered_details(document, '999')) == []
    assert list(public.rendered_details(document, '999', video_id='456')) == [dict(awemeId='456')]
    assert list(public.rendered_details(document, '123', video_id='789')) == []
    assert list(public.rendered_details("self.__pace_f.push(__import__('os').system('false'))", '123')) == []


def test_rendered_record_keeps_real_identity_time_and_https_urls():
    raw = dict(awemeId='456', createTime=1234, authorInfo=dict(uid='9'), desc='actual title',
        awemeType=0, video=dict(duration=5000, playAddr=[dict(src='https://cdn.example/video'), dict(src='http://cdn.example/insecure')]),
        images=[], stats=dict(diggCount=2))
    record = hot_capture.video_record(dict(data=dict(aweme_detail=public.canonical_detail(raw))))
    assert record['author_id'] == '9' and record['published_at'] == 1234
    assert record['urls'] == ['https://cdn.example/video'] and record['duration_s'] == 5
    raw.pop('authorInfo')
    assert hot_capture.video_record(dict(data=dict(aweme_detail=public.canonical_detail(raw))))['author_id'] == ''
    raw['isPrivate'] = True
    with pytest.raises(LibraryStopped, match='not_public'):
        public.canonical_detail(raw)


def test_explicit_topic_members_precede_unrelated_short_recommendations():
    primary=dict(aweme_id='1')
    unrelated=dict(aweme_id='2',hot_list=dict(sentence_id='999'),video=dict(duration=1000))
    same=dict(aweme_id='3',hot_list=dict(ac_sentence_id=123),video=dict(duration=30000))
    missing=dict(aweme_id='4',video=dict(duration=10000))
    records=[primary,unrelated,missing,same]
    assert [r['aweme_id'] for r in public.topic_priority(records,'123')]==['1','3','2','4']
    assert records[1] is unrelated  # Original received order remains intact.


def test_public_cached_response_tamper_is_rejected(tmp_path):
    source = public.PublicSource(tmp_path, tmp_path/'source')
    row = source._save('public_board', json.dumps(board(datetime.now(timezone.utc))).encode(), endpoint=public.BOARD)
    assert source._cached('public_board')['status_code'] == 0
    Path(row['response_path']).write_text('{}')
    with pytest.raises(LibraryStopped, match='response_changed'):
        source._cached('public_board')


def test_same_soundtrack_recall_keeps_topic_identity_and_raw_order():
    primary=dict(aweme_id='1', music=dict(id_str='42'))
    short=dict(aweme_id='2', music=dict(id_str='99'), video=dict(duration=1000))
    music=dict(aweme_id='3', music=dict(id_str='42'), video=dict(duration=30000))
    topic=dict(aweme_id='4', hot_list=dict(sentence_id='123'), video=dict(duration=60000))
    records=[primary,short,music,topic]
    assert [r['aweme_id'] for r in public.topic_priority(records,'123')]==['1','4','3','2']
    assert records==[primary,short,music,topic]
    assert public.topic_priority([], '123') == []


def test_known_missing_video_receipt_is_reused_and_source_tamper_still_blocks(tmp_path):
    source=public.PublicSource(tmp_path,tmp_path/'source')
    original=source._save('public_topic_response',b'known received unavailable page',endpoint='https://www.douyin.com/hot/123')
    source._save('topic_unavailable_123',json.dumps(dict(reason='public_hot_representative_unavailable',sources=[original])).encode(),endpoint='normalization',status='derived')
    with pytest.raises(hot_capture.CandidateUnavailable,match='representative_unavailable'):
        source._topic('123')
    Path(original['response_path']).write_bytes(b'changed')
    with pytest.raises(LibraryStopped,match='source_changed'):
        source._topic('123')


def test_received_live_room_is_skipped_without_network_or_fabricated_video(tmp_path):
    source=public.PublicSource(tmp_path,tmp_path/'source')
    detail=dict(awemeId='456',awemeType=101,createTime='$undefined',
                music='$undefined',desc='$undefined',video=dict(playAddr=[]))
    props=dict(hotInfo=dict(hotId='123'),aweme=dict(detail=detail))
    raw=('<script>self.__pace_f.push('+json.dumps([1,'7:'+json.dumps(['$','$L9',None,props])])+')</script>').encode()
    receipt=source._save('public_topic_response',raw,endpoint='https://www.douyin.com/hot/123',requested_topic_id='123')
    with patch.object(source,'_navigate_topic',side_effect=AssertionError('received live card must not be re-requested')):
        with pytest.raises(hot_capture.CandidateUnavailable,match='is_live_not_video'):
            source._topic('123')
    assert source.calls==[receipt]
    assert hot_capture.video_record(dict(data=dict(aweme_detail=dict(aweme_type=101))))['is_video'] is False


def topic_document(tid='123'):
    props=dict(hotInfo=dict(hotId=tid),aweme=dict(detail=dict(awemeId='456',authorInfo=dict(uid='1'))))
    return ('<script>self.__pace_f.push('+json.dumps([1,'7:'+json.dumps(['$','$L9',None,props])])+')</script>').encode()


def test_complete_raw_topic_evidence_is_reparsed_without_another_navigation(tmp_path):
    source=public.PublicSource(tmp_path,tmp_path/'source')
    source._save('public_topic_response',topic_document(),endpoint='https://www.douyin.com/hot/123',requested_topic_id='123')
    source._save('public_topic_response',json.dumps(dict(status_code=0,aweme_list=[dict(aweme_id='789')])).encode(),
        endpoint='https://www.douyin.com/aweme/v1/web/aweme/related/',requested_topic_id='123',related_parent_id='456')
    with patch.object(source,'_navigate_topic',side_effect=AssertionError('already received')):
        assert [r['aweme_id'] for r in source._topic('123')]==['456','789']
    assert source.calls[-1]['name']=='topic_123'


def test_received_non_json_related_body_stops_without_requesting_it_again(tmp_path):
    source=public.PublicSource(tmp_path,tmp_path/'source')
    source._save('public_topic_response',b'actual invalid upstream body',
        endpoint='https://www.douyin.com/aweme/v1/web/aweme/related/',requested_topic_id='123',related_parent_id='456')
    with patch.object(source,'_navigate_topic',side_effect=AssertionError('must diagnose received bytes')):
        with pytest.raises(json.JSONDecodeError):source._topic('123')
    assert len(source.calls)==1


def test_callback_mapping_error_is_raised_outside_the_event_dispatcher(tmp_path):
    playwright=pytest.importorskip('playwright.sync_api')
    source=public.PublicSource(tmp_path,tmp_path/'source')
    class Page:
        def route(self,*args):pass
        def on(self,event,callback):self.callback=callback
        def remove_listener(self,*args):pass
        def goto(self,*args,**kwargs):
            response=SimpleNamespace(url='https://www.douyin.com/hot/123',status=200,
                request=SimpleNamespace(resource_type='document'),body=topic_document)
            try:self.callback(SimpleNamespace(response=lambda:response))
            except Exception:pass  # The real dispatcher does not propagate this.
        def wait_for_timeout(self,*args):pass
        def locator(self,*args):return SimpleNamespace(inner_text=lambda:'public page')
        def title(self):return 'public page'
    browser=SimpleNamespace(new_page=lambda **kw:Page(),close=lambda:None)
    manager=SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw:browser))
    from contextlib import nullcontext
    with patch.object(playwright,'sync_playwright',return_value=nullcontext(manager)), \
         patch.object(public,'canonical_detail',side_effect=AttributeError('received field is not a mapping')):
        with pytest.raises(AttributeError,match='received field is not a mapping'):
            source._topic('123')
    assert source.calls[0]['status']=='received' and source.calls[0]['requested_topic_id']=='123'


@pytest.mark.parametrize('failure', ['response_body_unavailable', 'response_unavailable'])
def test_lost_response_body_uses_honest_dom_observation_with_exact_topic_binding(tmp_path,failure):
    playwright=pytest.importorskip('playwright.sync_api')
    source=public.PublicSource(tmp_path,tmp_path/'source')
    class Page:
        url='https://www.douyin.com/video/456'
        def route(self,*args):pass
        def on(self,event,callback):
            assert event=='requestfinished'
            self.callback=callback
        def remove_listener(self,event,callback):
            assert event=='requestfinished' and callback is self.callback
        def goto(self,*args,**kwargs):
            def unavailable():raise playwright.Error('body was navigated away from')
            response=SimpleNamespace(url='https://www.douyin.com/hot/123',status=200,
                request=SimpleNamespace(resource_type='document'),body=unavailable)
            self.callback(SimpleNamespace(response=unavailable if failure=='response_unavailable' else lambda:response))
        def wait_for_timeout(self,*args):pass
        def locator(self,*args):return SimpleNamespace(inner_text=lambda:'actual public video')
        def title(self):return 'actual public video'
        def content(self):return topic_document().decode()
    browser=SimpleNamespace(new_page=lambda **kw:Page(),close=lambda:None)
    manager=SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw:browser))
    from contextlib import nullcontext
    with patch.object(playwright,'sync_playwright',return_value=nullcontext(manager)):
        rows=source._topic('123')
    assert [r['aweme_id'] for r in rows]==['456']
    dom=source.calls[0]
    assert dom['observation']=='browser_dom_snapshot' and dom['status']=='derived'
    assert 'http_status' not in dom
    assert json.loads((source.output/'topic_transport_123.json').read_text())[0]['error']==failure


def test_recent_public_root_reused_across_hour_before_any_new_network(tmp_path):
    root=tmp_path/'shared/data/hot_references/public_previous_hour'
    source=public.PublicSource(tmp_path,root)
    source._save('public_board',json.dumps(board(datetime.now(timezone.utc))).encode(),endpoint=public.BOARD)
    with patch.object(public,'PublicSource') as constructor, \
         patch.object(hot_capture,'_capture_from_api',return_value={}) as admission:
        public.capture_reference(tmp_path,tmp_path/'out')
    assert constructor.call_args.args[1]==root
    assert admission.call_args.args[3]==root


def test_original_batch_can_pin_received_board_but_cannot_select_an_outside_directory(tmp_path):
    root=tmp_path/'shared/data/hot_references/public_original'
    source=public.PublicSource(tmp_path,root)
    source._save('public_board',json.dumps(board(datetime.now(timezone.utc))).encode(),endpoint=public.BOARD)
    with patch.object(hot_capture,'_capture_from_api',return_value={}) as admission:
        public.capture_reference(tmp_path,tmp_path/'out',cache_root=root)
        assert admission.call_args.args[3]==root
        outside=tmp_path/'public_outside';outside.mkdir()
        with pytest.raises(ValueError,match='invalid_existing_cache_root'):
            public.capture_reference(tmp_path,tmp_path/'other',cache_root=outside)
        assert admission.call_count==1


def test_independent_public_reads_preserve_unknown_paid_call_and_paid_guard(tmp_path):
    root = tmp_path/'shared/data/hot_references/old'
    root.mkdir(parents=True)
    path = root/'capture_calls.json'
    original = json.dumps([dict(name='hot_challenges', status='uncertain', endpoint='/paid/original')]).encode()
    path.write_bytes(original)
    with patch.object(hot_capture, 'Wellbyte', side_effect=AssertionError('must not call paid provider')), \
         patch.object(public, 'PublicSource') as source, \
         patch.object(hot_capture, '_capture_from_api', return_value={'public':True}) as admission:
        assert public.capture_reference(tmp_path, tmp_path/'out') == {'public':True}
        source.assert_called_once()
        admission.assert_called_once()
        with pytest.raises(LibraryStopped, match='existing_unknown'):
            hot_capture.capture_reference(tmp_path, tmp_path/'paid-out')
    assert path.read_bytes() == original


@pytest.mark.parametrize('status', ['submitted', 'uncertain'])
@pytest.mark.parametrize('ledger', ['model/library_state.json', 'audio_review/state.json'])
def test_model_unknown_in_another_source_or_hour_stops_before_public_network(tmp_path, status, ledger):
    path = tmp_path/'shared/data/hot_references/old_hour/template_other'/ledger
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(calls=[dict(status=status)])))
    with patch.object(public, 'PublicSource') as source:
        with pytest.raises(LibraryStopped, match='existing_unknown'):
            public.capture_reference(tmp_path, tmp_path/'out')
        source.assert_not_called()


@pytest.mark.parametrize('extra_candidate', [False, True])
@pytest.mark.parametrize('invalid', ['duplicate_author', 'stale', 'missing_url', 'photo', 'stale_primary', None])
def test_shared_public_admission_requires_three_recent_video_authors_and_av(tmp_path, invalid, extra_candidate):
    now = datetime.now(timezone.utc)
    records = [dict(aweme_id=str(i), author=dict(uid=str(i)), desc='title', create_time=now.timestamp()-60,
        video=dict(duration=5000, play_addr=dict(url_list=['https://cdn.example/'+str(i)]))) for i in range(1,4)]
    if invalid == 'duplicate_author': records[2]['author']['uid'] = '1'
    if invalid == 'stale': records[2]['create_time'] = now.timestamp()-73*3600
    if invalid == 'missing_url': records[2]['video']['play_addr']['url_list'] = []
    if invalid == 'photo': records[2]['aweme_type'] = 68
    if invalid == 'stale_primary':
        records.insert(0,dict(aweme_id='0',author=dict(uid='0'),create_time=now.timestamp()-80*3600,
            video=dict(duration=5000,play_addr=dict(url_list=['https://cdn.example/old']))))
    class Source:
        def extra_trends(self, *_args, **_kwargs):
            return public.board_data(board(now))['data']['objs'] if extra_candidate else []
        def call(self, name, endpoint, params):
            if name == 'hot_challenges':
                result=public.board_data(board(now))
                if extra_candidate: result['data']['objs']=[]
                return result
            if name.startswith('related_'): return dict(data=dict(item_list=[dict(item_id=r['aweme_id']) for r in records],required_member_id=records[0]['aweme_id']))
            return dict(data=dict(aweme_detail=next(r for r in records if r['aweme_id']==params['aweme_id'])))
    out=tmp_path/'out';out.mkdir()
    with patch.object(hot_capture, '_download', side_effect=lambda r,*a,**kw:dict(id=r['id'],sha256=r['id'],path=str(tmp_path/'video'))) as download, \
         patch.object(hot_capture, '_verify_template', return_value=dict(same_template=False)) as review:
        with pytest.raises(LibraryStopped, match='no_supported_same_template'):
            hot_capture._capture_from_api(tmp_path,out,Source(),tmp_path,now,max_candidates=1,freshness_hours=72,
                visual_library=None,excluded_trends=set(),excluded_references=set(),max_video_bytes=1024,min_free_bytes=0)
        assert review.call_count == (0 if invalid else 1)
        assert download.call_count == (0 if invalid else 3)


@pytest.mark.parametrize('invalid', [None, 'id', 'title', 'score', 'stale', 'time_skew', 'expired'])
def test_received_hot_label_retains_separate_provenance_and_time(tmp_path, invalid):
    now=datetime.now(timezone.utc)
    extra=dict(ac_sentence_id='456',trending_topic='Actual native label',board_name='上升热点',
        hotspot_bar_req_time=str(int(now.timestamp())),hotspot_bar_expire_time='0')
    hot=dict(sentence_id=456,sentence='Actual native label',header='上升热点',type=2,hot_score=100)
    if invalid=='id': extra['ac_sentence_id']='999'
    if invalid=='title': extra['trending_topic']='Different title'
    if invalid=='score': hot['hot_score']=0
    if invalid=='time_skew': extra['hotspot_bar_req_time']=str(int(now.timestamp())-301)
    if invalid=='expired': extra['hotspot_bar_expire_time']=str(int(now.timestamp())-1)
    hot['extra']=json.dumps(extra)
    source=public.PublicSource(tmp_path,tmp_path/'cache')
    row=source._save('public_topic_response',json.dumps(dict(status_code=0,
        aweme_list=[dict(aweme_id='789',hot_list=hot)])).encode(),
        endpoint='https://www.douyin.com/aweme/v1/web/aweme/related/')
    if invalid=='stale': row['finished_at']=(now-timedelta(hours=73)).isoformat()
    # _save's real clock is later than the frozen `now` above.
    current=datetime.now(timezone.utc)
    result=source.extra_trends(current,freshness_hours=72)
    if invalid:
        assert result==[]
    else:
        assert result[0]['sentence_id']=='456' and result[0]['rank']==100000
        proof=result[0]['candidate_discovery']
        assert proof['response_sha256']==row['response_sha256'] and proof['observed_at']==row['finished_at']
        assert 'last_update_time' not in result[0] and 'board_updated_at' not in proof
        Path(row['response_path']).write_text('{}')
        with pytest.raises(LibraryStopped,match='hot_label_source_changed'):
            source.extra_trends(current,freshness_hours=72)


def test_extra_candidate_reached_under_cap_with_full_main_board(tmp_path):
    now=datetime.now(timezone.utc)
    original=public.board_data(board(now))
    stamp=original['data']['last_update_time']
    main=[dict(sentence_id=str(i),sentence='Main topic') for i in range(10,20)]
    additional=dict(sentence_id='456',sentence='Separately observed label',
        candidate_discovery=dict(source_kind='native_label_fixture',observed_at=now.isoformat()))
    records=[dict(aweme_id=str(i),author=dict(uid=str(i)),create_time=now.timestamp()-60,
        video=dict(duration=5000,play_addr=dict(url_list=['https://cdn.example/'+str(i)]))) for i in range(1,4)]
    visited=[]
    class Source:
        calls=[]
        ledger=tmp_path/'public_calls.json'
        def extra_trends(self,*a,**k):return [additional]
        def call(self,name,endpoint,params):
            if name=='hot_challenges':return dict(data=dict(last_update_time=stamp,objs=main))
            if name.startswith('related_'):
                visited.append(params['query_id'])
                if params['query_id']!='456':raise hot_capture.CandidateUnavailable('unavailable')
                return dict(data=dict(item_list=[dict(item_id=r['aweme_id']) for r in records],required_member_id='1'))
            return dict(data=dict(aweme_detail=next(r for r in records if r['aweme_id']==params['aweme_id'])))
    out=tmp_path/'out';out.mkdir()
    with patch.object(hot_capture,'_download',side_effect=lambda r,*a,**k:dict(id=r['id'],sha256=r['id'],path=str(tmp_path/'video'))), \
         patch.object(hot_capture,'_verify_template',return_value=dict(same_template=True,library_adaptable=True,representative_id='1')):
        result=hot_capture._capture_from_api(tmp_path,out,Source(),tmp_path,now,max_candidates=2,
            freshness_hours=72,visual_library=None,excluded_trends=set(),excluded_references=set(),
            max_video_bytes=1024,min_free_bytes=0)
    assert visited==['10','456']
    assert result['evidence']['candidate_discovery']==additional['candidate_discovery']
    assert result['evidence']['board_updated_at']==datetime.strptime(stamp,'%Y%m%d%H%M%S').replace(tzinfo=timezone(timedelta(hours=8))).isoformat()


@pytest.mark.parametrize('invalid', [None, 'newer_label', 'label_sha', 'label_id', 'video_author', 'dom_only'])
def test_native_primary_requires_original_topic_label_and_real_matching_document(tmp_path, invalid):
    now=datetime.now(timezone.utc)
    root=tmp_path/'shared/data/hot_references/public_existing'
    source=public.PublicSource(tmp_path,root)
    original=dict(aweme_id='789',author=dict(uid='9'),create_time=int(now.timestamp())-60,
        video=dict(duration=5000,play_addr=dict(url_list=['https://cdn.example/video'])),
        hot_list=dict(sentence_id=456,sentence='Native topic',header='上升热点',hot_score=9,
            extra=json.dumps(dict(ac_sentence_id='456',trending_topic='Native topic',board_name='上升热点',
                hotspot_bar_req_time=str(int(now.timestamp())),hotspot_bar_expire_time='0'))))
    source._save('public_topic_response',json.dumps(dict(status_code=0,aweme_list=[original])).encode(),
        endpoint='https://www.douyin.com/aweme/v1/web/aweme/related/',http_status=200,related_parent_id='555')
    trend=source.extra_trends(datetime.now(timezone.utc),freshness_hours=72)[0]
    if invalid=='label_sha': trend['candidate_discovery']['response_sha256']='bad'
    if invalid=='label_id': trend['sentence_id']='999'
    if invalid=='newer_label':
        newer=dict(original,aweme_id='999')
        source._save('public_topic_response',json.dumps(dict(status_code=0,aweme_list=[newer])).encode(),
            endpoint='https://www.douyin.com/aweme/v1/web/aweme/related/',http_status=200,related_parent_id='666')
    source.prepare_trend(trend)
    def navigate(tid,primary,related,sources,*,video_id=None):
        assert tid=='456' and video_id=='789'
        primary.update(dict(original,author=dict(uid='wrong' if invalid=='video_author' else '9')))
        flight='7:'+json.dumps(['$', '$L9', None, dict(children=dict(awemeId='789',statusCode=0,aweme=None))])
        document='self.__pace_f.push('+json.dumps([1,flight])+')'
        row=source._save('public_topic_response',document.encode(),
            endpoint='https://www.douyin.com/video/789',http_status=200,
            status='derived' if invalid=='dom_only' else 'received')
        sources.append(row)
        related.extend([('789',dict(aweme_id='790')),('wrong-parent',dict(aweme_id='791'))])
    with patch.object(source,'_navigate_topic',side_effect=navigate) as browser:
        if invalid and invalid!='newer_label':
            with pytest.raises(LibraryStopped,match='native_'):
                source._topic(trend['sentence_id'])
            if invalid.startswith('label_'): browser.assert_not_called()
        else:
            rows=source._topic('456')
            assert [r['aweme_id'] for r in rows]==['789','790']
            assert source._topic('456')==rows and browser.call_count==1


def test_native_video_route_and_detail_are_independently_bound(tmp_path):
    fragment='7:'+json.dumps([dict(children=dict(awemeId='789',statusCode=0,aweme=None))])
    document='self.__pace_f.push('+json.dumps([1,fragment])+')'
    assert public.rendered_video_identity(document,'789')
    assert not public.rendered_video_identity(document,'999')
    assert not public.rendered_video_identity("self.__pace_f.push(__import__('os'))",'789')
    source=public.PublicSource(tmp_path,tmp_path/'cache')
    row=source._save('public_topic_response',json.dumps(dict(status_code=0,
        aweme_detail=dict(aweme_id='789',author=dict(uid='9')))).encode(),
        endpoint='https://www.douyin.com/aweme/v1/web/aweme/detail/',requested_video_id='789')
    primary={}
    source._read_topic_source(row,'456',primary,[],video_id='789')
    assert primary['aweme_id']=='789'
    row['requested_video_id']='999'
    with pytest.raises(LibraryStopped,match='native_detail_identity_mismatch'):
        source._read_topic_source(row,'456',{},[],video_id='789')


@pytest.mark.parametrize('error',[hot_capture.CandidateUnavailable('public_hot_representative_unavailable'),
                                  LibraryStopped('public_capture_requires_human_verification')])
def test_only_explicit_individual_unavailable_candidate_is_skipped(tmp_path,error):
    now=datetime.now(timezone.utc)
    class Source:
        def call(self,name,endpoint,params):
            if name=='hot_challenges':
                data=public.board_data(board(now));data['data']['objs'].append(dict(sentence='second',sentence_id='456'))
                return data
            if name=='related_123':raise error
            assert name=='related_456'
            return dict(data=dict(item_list=[]))
    out=tmp_path/'out';out.mkdir()
    with pytest.raises(LibraryStopped) as raised:
        hot_capture._capture_from_api(tmp_path,out,Source(),tmp_path,now,max_candidates=2,freshness_hours=72,
            visual_library=None,excluded_trends=set(),excluded_references=set(),max_video_bytes=1024,min_free_bytes=0)
    if isinstance(error,hot_capture.CandidateUnavailable):
        rows=json.loads((out/'candidate_reviews.json').read_text())
        assert [r['trend_id'] for r in rows]==['123','456']
        assert rows[0]['reason']=='public_hot_representative_unavailable'
    else:assert str(raised.value)=='public_capture_requires_human_verification'



@pytest.mark.parametrize('offset_seconds', [-73*3600, 60])
def test_nonrecent_native_primary_stops_before_browser_and_preserves_receipts(tmp_path, offset_seconds):
    now=datetime.now(timezone.utc)
    root=tmp_path/'shared/data/hot_references/public_existing'
    source=public.PublicSource(tmp_path,root)
    original=dict(aweme_id='789',author=dict(uid='9'),create_time=int(now.timestamp())+offset_seconds,
        video=dict(duration=5000,play_addr=dict(url_list=['https://cdn.example/video'])),
        hot_list=dict(sentence_id=456,sentence='Native topic',header='上升热点',hot_score=9,
            extra=json.dumps(dict(ac_sentence_id='456',trending_topic='Native topic',board_name='上升热点',
                hotspot_bar_req_time=str(int(now.timestamp())),hotspot_bar_expire_time='0'))))
    row=source._save('public_topic_response',json.dumps(dict(status_code=0,aweme_list=[original])).encode(),
        endpoint='https://www.douyin.com/aweme/v1/web/aweme/related/',http_status=200,related_parent_id='555')
    trend=source.extra_trends(datetime.now(timezone.utc),freshness_hours=72)[0]
    source.prepare_trend(trend)
    before={path:path.read_bytes() for path in (source.ledger,Path(row['response_path']))}
    with patch.object(source,'_navigate_topic',side_effect=AssertionError('known nonrecent primary must not browse')) as browser:
        with pytest.raises(hot_capture.CandidateUnavailable,match='public_native_primary_not_recent_video'):
            source._topic('456')
        browser.assert_not_called()
    assert all(path.read_bytes()==raw for path,raw in before.items())


@pytest.mark.parametrize('unusable', ['stale', 'future', 'photo', 'missing_time', 'missing_author'])
@pytest.mark.parametrize('fresh_received_last', [False, True])
def test_native_label_selects_fresh_video_instead_of_later_unusable_primary(tmp_path, unusable, fresh_received_last):
    now=datetime.now(timezone.utc)
    source=public.PublicSource(tmp_path,tmp_path/'source')
    hot=dict(sentence_id=456,sentence='Native topic',header='上升热点',hot_score=9,
        extra=json.dumps(dict(ac_sentence_id='456',trending_topic='Native topic',board_name='上升热点',
            hotspot_bar_req_time=str(int(now.timestamp())),hotspot_bar_expire_time='0')))
    fresh=dict(aweme_id='789',author=dict(uid='9'),create_time=int(now.timestamp())-60,hot_list=hot)
    other=dict(fresh,aweme_id='790')
    if unusable=='stale':other['create_time']=int(now.timestamp())-73*3600
    elif unusable=='future':other['create_time']=int(now.timestamp())+60
    elif unusable=='photo':other['images']=[dict(uri='actual-photo')]
    elif unusable=='missing_time':other.pop('create_time')
    else:other.pop('author')
    rows=[other,fresh] if fresh_received_last else [fresh,other]
    receipts=[]
    for index,record in enumerate(rows):
        with patch.object(hot_capture,'_now',return_value=(now-timedelta(seconds=2-index)).isoformat()):
            receipts.append(source._save('public_topic_response',json.dumps(dict(status_code=0,aweme_list=[record])).encode(),
                endpoint='https://www.douyin.com/aweme/v1/web/aweme/related/',http_status=200,related_parent_id='555'))
    before={path:path.read_bytes() for path in [source.ledger,*[Path(row['response_path']) for row in receipts]]}
    with patch.object(source,'_navigate_topic',side_effect=AssertionError('selection must use received receipts only')):
        trend,=source.extra_trends(now,freshness_hours=72)
    proof=trend['candidate_discovery'];chosen=receipts[int(fresh_received_last)]
    assert proof['native_aweme_id']=='789'
    assert proof['response_path']==chosen['response_path'] and proof['response_sha256']==chosen['response_sha256']
    assert proof['observed_at']==chosen['finished_at']
    assert all(path.read_bytes()==raw for path,raw in before.items())


def test_native_label_retains_latest_observation_when_both_videos_are_fresh(tmp_path):
    now=datetime.now(timezone.utc)
    source=public.PublicSource(tmp_path,tmp_path/'source')
    for index,video_id in enumerate(['789','790']):
        hot=dict(sentence_id=456,sentence='Native topic',header='上升热点',hot_score=9,
            extra=json.dumps(dict(ac_sentence_id='456',trending_topic='Native topic',board_name='上升热点',
                hotspot_bar_req_time=str(int(now.timestamp())),hotspot_bar_expire_time='0')))
        record=dict(aweme_id=video_id,author=dict(uid='9'),create_time=int(now.timestamp())-60,hot_list=hot)
        with patch.object(hot_capture,'_now',return_value=(now-timedelta(seconds=2-index)).isoformat()):
            last=source._save('public_topic_response',json.dumps(dict(status_code=0,aweme_list=[record])).encode(),
                endpoint='https://www.douyin.com/aweme/v1/web/aweme/related/',http_status=200,related_parent_id='555')
    trend,=source.extra_trends(now,freshness_hours=72)
    assert trend['candidate_discovery']['native_aweme_id']=='790'
    assert trend['candidate_discovery']['response_sha256']==last['response_sha256']
