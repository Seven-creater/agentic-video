"""Normal public-page acquisition; never sends credentials or Wellbyte requests.

The shared admission routine still requires three recent independent authors and
the complete audiovisual comparison. Related recommendations are candidates,
not a claim by the platform that they all share a template.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

from ..library.media import sha256_file
from ..library.state import LibraryStopped, file_lock, write_json
from . import hot_capture

BOARD = 'https://www.douyin.com/aweme/v1/web/hot/search/list/?device_platform=webapp&aid=6383&channel=channel_pc_web&detail_list=1&source=6&main_billboard_count=5'


def board_data(payload):
    if payload.get('status_code') != 0:
        raise LibraryStopped('public_board_platform_error')
    data = payload['data']
    stamp = datetime.strptime(data['active_time'], '%Y-%m-%d %H:%M:%S')
    trends, seen = [], set()
    for section in ['word_list', 'trending_list']:
        for row in data.get(section, []):
            tid = str(row.get('sentence_id', ''))
            if not tid.isdecimal() or tid in seen or not isinstance(row.get('word'), str):
                continue
            seen.add(tid)
            trends.append(dict(sentence=row['word'], sentence_id=tid,
                rank=int(row.get('position') or 100000), hot_score=row.get('hot_value'),
                video_count=row.get('video_count'), public_board_section=section))
    return dict(data=dict(last_update_time=stamp.strftime('%Y%m%d%H%M%S'), objs=trends))


def rendered_details(document, trend_id, *, video_id=None):
    """Decode received JSON strings only; never execute page scripts."""
    for match in re.finditer(r'self\.__pace_f\.push\(', document):
        try:
            args, _ = json.JSONDecoder().raw_decode(document[match.end():])
            fragment = args[1]
            node = json.loads(fragment.partition(':')[2])
        except (ValueError, TypeError, IndexError, AttributeError):
            continue
        if not isinstance(node, list) or len(node) < 4 or not isinstance(node[3], dict):
            continue
        props = node[3]
        if video_id is not None:
            detail = (props.get('aweme') or {}).get('detail')
            if isinstance(detail, dict) and str(detail.get('awemeId')) == video_id:
                yield detail
        elif str((props.get('hotInfo') or {}).get('hotId')) == trend_id:
            detail = (props.get('aweme') or {}).get('detail')
            if isinstance(detail, dict):
                yield detail


def canonical_detail(detail):
    if detail.get('isPrivate') is True or detail.get('isFriendLimit') is True:
        raise LibraryStopped('public_video_not_public')
    if detail.get('awemeType') == 101:
        # A received live-room card has React $undefined video metadata. It is
        # a known non-recorded input, never a downloadable reference video.
        raise hot_capture.CandidateUnavailable('public_hot_representative_is_live_not_video')
    author, video = detail.get('authorInfo') or {}, detail.get('video') or {}
    urls = [row['src'] for row in video.get('playAddr', [])
            if isinstance(row, dict) and isinstance(row.get('src'), str)]
    return dict(aweme_id=detail.get('awemeId'), author=dict(uid=author.get('uid'), nickname=author.get('nickname')),
        desc=detail.get('desc'), create_time=detail.get('createTime'), aweme_type=detail.get('awemeType'),
        images=detail.get('images') if isinstance(detail.get('images'), list) else [],
        video=dict(duration=video.get('duration'), play_addr=dict(url_list=urls)),
        statistics=dict(digg_count=(detail.get('stats') or {}).get('diggCount')),
        music=dict(id_str=(detail.get('music') or {}).get('idStr')))


def rendered_video_identity(document, video_id):
    """Confirm the native route identity in received nested Flight JSON only."""
    for match in re.finditer(r'self\.__pace_f\.push\(', document):
        try:
            args, _ = json.JSONDecoder().raw_decode(document[match.end():])
            node = json.loads(args[1].partition(':')[2])
        except (ValueError, TypeError, IndexError, AttributeError):
            continue
        pending = [node]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if str(value.get('awemeId')) == video_id and value.get('statusCode') == 0 and 'aweme' in value:
                    return True
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
    return False


def topic_priority(records, tid):
    """Prefer explicit topic IDs, then the primary's recorded soundtrack.

    Music is a retrieval clue only; independent authors, age and the actual AV
    comparison still determine whether these examples form a usable template.
    """
    primary_music = hot_capture.video_record(dict(data=dict(aweme_detail=records[0])))['music_id'] if records else ''
    def priority(row):
        hot = row.get('hot_list')
        matched = isinstance(hot, dict) and any(str(hot.get(k)) == tid for k in ('sentence_id', 'ac_sentence_id'))
        music = hot_capture.video_record(dict(data=dict(aweme_detail=row)))['music_id']
        return (not matched, not (primary_music and music == primary_music),
                (row.get('video') or {}).get('duration') or 10**12)
    return records[:1] + sorted(records[1:], key=priority)


class PublicSource:
    """Adapter for the admission routine, with raw public responses as receipts."""
    source_evidence = dict(source_kind='douyin_public_browser_v1',
        candidate_source='Public main board or separately recorded native hot labels; primary is bound by the exact hot-page ID or the native label plus its actual video document. Normal related recommendations still require recorded AV verification.',
        paid_discovery_calls=False)

    def __init__(self, home, root):
        self.home, self.output = Path(home), Path(root)
        self.output.mkdir(parents=True, exist_ok=True)
        self.ledger = self.output / 'public_calls.json'
        self.calls = json.loads(self.ledger.read_text()) if self.ledger.exists() else []
        self.records = {}
        self.current_discovery = None

    def prepare_trend(self, trend, *, freshness_hours=72):
        self.current_discovery = trend.get('candidate_discovery')
        self.discovery_freshness_hours = freshness_hours

    def _save(self, name, raw, *, endpoint, status='received', **evidence):
        path = self.output / f'public-{len(self.calls)+1:04d}.response'
        with path.open('xb') as handle:
            handle.write(raw)
        row = dict(name=name, provider='douyin_public', billable=False, status=status,
            endpoint=endpoint, response_path=str(path), response_sha256=sha256_file(path),
            finished_at=hot_capture._now(), **evidence)
        self.calls.append(row)
        write_json(self.ledger, self.calls)
        return row

    def _cached(self, name):
        row = next((r for r in self.calls if r['name'] == name), None)
        if row is None:
            return None
        if row['status'] not in {'received', 'derived'} or sha256_file(row['response_path']) != row['response_sha256']:
            raise LibraryStopped('public_cached_response_changed_or_unavailable')
        return json.loads(Path(row['response_path']).read_bytes())

    def _board(self):
        payload = self._cached('public_board')
        if payload is None:
            request = urllib.request.Request(BOARD, headers={'User-Agent':'Mozilla/5.0', 'Referer':'https://www.douyin.com/'})
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(4*1024**2+1)
                if len(raw) > 4*1024**2:
                    raise LibraryStopped('public_board_response_too_large')
                self._save('public_board', raw, endpoint=BOARD, http_status=response.status)
            payload = json.loads(raw)
        return board_data(payload)

    def extra_trends(self, now, *, freshness_hours):
        """Recall native hot labels separately from the actual main-board data."""
        found, priorities = {}, {}
        for source in self.calls:
            if (source.get('name') != 'public_topic_response' or source.get('status') != 'received' or
                    urllib.parse.urlsplit(source.get('endpoint', '')).path != '/aweme/v1/web/aweme/related/'):
                continue
            path = Path(source['response_path'])
            if sha256_file(path) != source['response_sha256']:
                raise LibraryStopped('public_hot_label_source_changed')
            try:
                received = datetime.fromisoformat(source['finished_at'])
                age = (now - received).total_seconds()
                payload = json.loads(path.read_bytes())
            except (ValueError, TypeError):
                continue
            if not 0 <= age <= freshness_hours * 3600 or payload.get('status_code') != 0:
                continue
            for record in payload.get('aweme_list') or []:
                hot = record.get('hot_list') or {}
                try:
                    extra = json.loads(hot.get('extra', ''))
                    tid, title = str(hot['sentence_id']), hot['sentence']
                    requested, expires = int(extra['hotspot_bar_req_time']), int(extra['hotspot_bar_expire_time'])
                    score = hot['hot_score']
                    valid = (tid.isdecimal() and isinstance(title, str) and title.strip() and
                        str(extra['ac_sentence_id']) == tid and extra['trending_topic'] == title and
                        hot['header'] in {'热点', '热榜', '上升热点', '挑战榜', '娱乐榜', '社会榜'} and
                        extra['board_name'] == hot['header'] and type(score) in {int, float} and score > 0 and
                        0 <= now.timestamp() - requested <= freshness_hours * 3600 and
                        abs(received.timestamp() - requested) <= 300 and
                        (expires == 0 or expires > now.timestamp()))
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
                if not valid:
                    continue
                evidence = dict(source_kind='douyin_received_hot_label_v1',
                    response_path=str(path), response_sha256=source['response_sha256'],
                    observed_at=source['finished_at'], label_requested_at_epoch_s=requested,
                    label_expires_at_epoch_s=expires, header=hot['header'], native_type=hot.get('type'),
                    native_rank=hot.get('rank'), native_aweme_id=str(record.get('aweme_id', '')),
                    evidence_limit='Platform label observed in this response; not main-board membership, board update time, or proof of a reusable template. Zero expiry is unspecified.')
                trend = dict(sentence=title, sentence_id=tid, hot_score=score, rank=100000,
                    video_count=None, candidate_discovery=evidence)
                published = record.get('create_time')
                # A newly observed label can accompany an old video. Prefer a
                # usable native primary, while retaining the exact raw receipt
                # and the latest observation order among equally usable ones.
                eligible = (type(published) in {int, float} and
                    0 <= now.timestamp() - published <= freshness_hours * 3600 and
                    bool(evidence['native_aweme_id']) and bool((record.get('author') or {}).get('uid')) and
                    not record.get('images') and record.get('aweme_type') not in {68, 101, 150})
                priority = (eligible, received)
                if tid not in found or priority > priorities[tid]:
                    found[tid] = trend
                    priorities[tid] = priority
        return list(found.values())

    def _read_topic_source(self, row, tid, primary, related, *, video_id=None):
        path = Path(row['response_path'])
        if sha256_file(path) != row['response_sha256']:
            raise LibraryStopped('public_topic_source_changed')
        raw = path.read_bytes()
        endpoint = urllib.parse.urlsplit(row['endpoint'])
        if endpoint.path.startswith(('/hot/', '/video/')):
            for detail in rendered_details(raw.decode('utf-8'), tid, video_id=video_id):
                primary.update(canonical_detail(detail))
        elif endpoint.path == '/aweme/v1/web/aweme/related/':
            payload = json.loads(raw)
            if payload.get('status_code') != 0:
                raise LibraryStopped('public_related_platform_error')
            # Older raw receipts did not retain the parent ID. They cannot
            # establish a relationship by themselves; never guess that ID.
            parent = row.get('related_parent_id')
            if parent:
                related.extend((parent, detail) for detail in payload.get('aweme_list', []))
        elif endpoint.path == '/aweme/v1/web/aweme/detail/' and video_id is not None:
            payload = json.loads(raw)
            detail = payload.get('aweme_detail') or {}
            if (payload.get('status_code') != 0 or row.get('requested_video_id') != video_id or
                    str(detail.get('aweme_id')) != video_id):
                raise LibraryStopped('public_native_detail_identity_mismatch')
            primary.update(detail)

    def _topic(self, tid):
        if self.current_discovery is not None:
            return self._native_topic(tid)
        unavailable = self._cached('topic_unavailable_' + tid)
        if unavailable is not None:
            for row in unavailable['sources']:
                if sha256_file(row['response_path']) != row['response_sha256']:
                    raise LibraryStopped('public_topic_source_changed')
            raise hot_capture.CandidateUnavailable(unavailable['reason'])
        cached = self._cached('topic_' + tid)
        if cached is not None:
            for row in cached['sources']:
                if sha256_file(row['response_path']) != row['response_sha256']:
                    raise LibraryStopped('public_topic_source_changed')
            return cached['records']
        primary, related, sources = {}, [], []
        for row in self.calls:
            if row.get('name') == 'public_topic_response' and row.get('requested_topic_id') == tid:
                self._read_topic_source(row, tid, primary, related)
                sources.append(row)
        if not (primary and related):
            self._navigate_topic(tid, primary, related, sources)
        if not primary or not str(primary.get('aweme_id', '')).isdecimal():
            raise LibraryStopped('public_hot_representative_missing')
        records = {str(primary['aweme_id']): primary}
        for parent, detail in related:
            if parent == str(primary['aweme_id']) and str(detail.get('aweme_id', '')).isdecimal():
                records.setdefault(str(detail['aweme_id']), detail)
        rest = sorted((d for key, d in records.items() if key != str(primary['aweme_id'])),
                      key=lambda d: (d.get('video') or {}).get('duration') or 10**12)
        rows = [primary, *rest]
        raw = json.dumps(dict(records=rows, sources=sources), ensure_ascii=False).encode()
        self._save('topic_' + tid, raw, endpoint='public_response_normalization', status='derived')
        return rows

    def _native_topic(self, tid):
        """Bind a native hot label to its actual video document and recommendations."""
        proof = self.current_discovery
        path = Path(proof['response_path']).resolve(strict=True)
        cache = (self.home/'shared/data/hot_references').resolve(strict=True)
        if path.parent.parent != cache or not path.parent.name.startswith('public_'):
            raise LibraryStopped('public_native_label_source_outside_cache')
        origin = PublicSource(self.home, path.parent)
        origin.calls = [row for row in origin.calls if row['response_path'] == str(path) and
                        row['response_sha256'] == proof['response_sha256']]
        if len(origin.calls) != 1:
            raise LibraryStopped('public_native_label_original_receipt_missing')
        # Revalidate the full native row, its original ledger, SHA and clocks.
        valid = origin.extra_trends(datetime.now(timezone.utc), freshness_hours=self.discovery_freshness_hours)
        if not any(row['sentence_id'] == tid and row['candidate_discovery'] == proof for row in valid):
            raise LibraryStopped('public_native_label_binding_changed')
        native_id = proof['native_aweme_id']
        original = next((r for r in json.loads(path.read_bytes()).get('aweme_list', [])
                         if str(r.get('aweme_id')) == native_id), None)
        if original is None or not native_id.isdecimal():
            raise LibraryStopped('public_native_label_video_missing')
        expected = hot_capture.video_record(dict(data=dict(aweme_detail=original)))
        # The required primary must itself fit the unchanged freshness window.
        # Its received timestamp already proves a stale/future primary cannot
        # enter the three-author comparison; avoid browsing it first.
        age = datetime.now(timezone.utc).timestamp() - expected['published_at']
        if not 0 <= age <= self.discovery_freshness_hours * 3600:
            raise hot_capture.CandidateUnavailable('public_native_primary_not_recent_video')
        key = 'native_topic_' + tid + '_' + native_id
        cached = self._cached(key)
        if cached is not None:
            for row in cached['sources']:
                if sha256_file(row['response_path']) != row['response_sha256']:
                    raise LibraryStopped('public_topic_source_changed')
            return cached['records']
        primary, related, sources = {}, [], []
        self._navigate_topic(tid, primary, related, sources, video_id=native_id)
        if not any(row.get('status') == 'received' and row.get('http_status') == 200 and
                   urllib.parse.urlsplit(row['endpoint']).path == '/video/' + native_id and
                   rendered_video_identity(Path(row['response_path']).read_text(), native_id) for row in sources):
            raise LibraryStopped('public_native_video_document_not_received')
        observed = hot_capture.video_record(dict(data=dict(aweme_detail=primary)))
        if any(observed[k] != expected[k] for k in ('id', 'author_id', 'published_at')):
            raise LibraryStopped('public_native_video_identity_changed')
        records = {native_id: primary}
        for parent, detail in related:
            if parent == native_id and str(detail.get('aweme_id', '')).isdecimal():
                records.setdefault(str(detail['aweme_id']), detail)
        rows = [primary, *(r for aid, r in records.items() if aid != native_id)]
        label_source = next(row for row in origin.calls if row['response_path'] == str(path)
                            and row['response_sha256'] == proof['response_sha256'])
        sources.insert(0, label_source)
        self._save(key, json.dumps(dict(records=rows, sources=sources, native_hot_label=proof),
            ensure_ascii=False).encode(), endpoint='native_hot_label_then_video_document', status='derived')
        return rows

    def _navigate_topic(self, tid, primary, related, sources, *, video_id=None):
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
        errors, parse_errors = [], []
        def observe(request):
            try:
                response = request.response()
            except PlaywrightError:
                errors.append(dict(error='response_unavailable'))
                write_json(self.output / ('topic_transport_' + tid + '.json'), errors)
                return
            if response is None:
                return
            url = urllib.parse.urlsplit(response.url)
            if url.hostname != 'www.douyin.com' or response.status != 200:
                return
            document = response.request.resource_type == 'document' and url.path.startswith(('/hot/', '/video/'))
            recommendation = url.path == '/aweme/v1/web/aweme/related/'
            detail_request = video_id is not None and url.path == '/aweme/v1/web/aweme/detail/'
            if not document and not recommendation and not detail_request:
                return
            endpoint = urllib.parse.urlunsplit((url.scheme, url.netloc, url.path, '', ''))
            try:
                raw = response.body()
            except PlaywrightError:
                # A document can navigate away before Chromium exposes its body.
                # Record the transport failure; it is not a received-body receipt.
                errors.append(dict(endpoint=endpoint, error='response_body_unavailable'))
                write_json(self.output / ('topic_transport_' + tid + '.json'), errors)
                return
            if len(raw) > 4*1024**2:
                return
            row = self._save('public_topic_response', raw, endpoint=endpoint,
                request_sha256=hashlib.sha256(response.url.encode()).hexdigest(), http_status=response.status,
                requested_topic_id=tid, related_parent_id=(urllib.parse.parse_qs(url.query).get('aweme_id', [''])[0]
                    if recommendation else None), requested_video_id=(
                    urllib.parse.parse_qs(url.query).get('aweme_id', [''])[0] if detail_request else None))
            sources.append(row)
            self._read_topic_source(row, tid, primary, related, video_id=video_id)
        def finished(request):
            try:
                observe(request)
            except Exception as error:
                # Event dispatchers may swallow arbitrary callback exceptions,
                # including malformed received metadata or a failed disk write.
                # Re-raise in normal control flow, with the raw receipt intact.
                parse_errors.append(error)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, chromium_sandbox=True,
                executable_path='/opt/omni-public-browser-1248/chrome-headless-shell-linux64/chrome-headless-shell')
            page = None
            try:
                page = browser.new_page(viewport=dict(width=1280, height=800))
                page.route('**/*', lambda route: route.abort() if route.request.resource_type == 'media' else route.continue_())
                page.on('requestfinished', finished)
                target = ('https://www.douyin.com/video/' + video_id if video_id is not None
                          else 'https://www.douyin.com/hot/' + tid)
                page.goto(target, wait_until='domcontentloaded', timeout=45000)
                for _ in range(12):
                    page.wait_for_timeout(1000)
                    if primary and related:
                        break
                text = page.locator('body').inner_text()
                if '验证码' in page.title() or any(word in text for word in ['请完成下列验证', '请完成下方验证', '拖动滑块', '滑块验证']):
                    raise LibraryStopped('public_capture_requires_human_verification')
                if parse_errors:
                    raise parse_errors[0]
                if not primary and video_id is None:
                    # A browser DOM snapshot is observed evidence, explicitly
                    # distinguished from an HTTP response. The same exact hotId
                    # binding is required; no inferred or executed script data.
                    url = urllib.parse.urlsplit(page.url)
                    if url.hostname == 'www.douyin.com' and url.path.startswith(('/hot/', '/video/')):
                        raw = page.content().encode()
                        if len(raw) <= 4*1024**2:
                            row = self._save('public_topic_response', raw,
                                endpoint=urllib.parse.urlunsplit((url.scheme, url.netloc, url.path, '', '')),
                                status='derived', observation='browser_dom_snapshot', requested_topic_id=tid)
                            sources.append(row)
                            self._read_topic_source(row, tid, primary, related)
                if not primary and '你要观看的视频不存在' in text:
                    reason = 'public_hot_representative_unavailable'
                    self._save('topic_unavailable_' + tid,
                        json.dumps(dict(reason=reason, sources=sources)).encode(),
                        endpoint='public_response_normalization', status='derived')
                    raise hot_capture.CandidateUnavailable(reason)
                if not primary and video_id is not None:
                    raise LibraryStopped('public_native_video_document_not_received')
            finally:
                if page is not None:
                    page.remove_listener('requestfinished', finished)
                browser.close()

    def call(self, name, endpoint, params):
        if name == 'hot_challenges':
            return self._board()
        if name.startswith('related_'):
            tid = str(params['query_id'])
            if not tid.isdecimal():
                raise LibraryStopped('public_invalid_topic_id')
            records = topic_priority(self._topic(tid), tid)
            self.records.update({str(r['aweme_id']):r for r in records})
            return dict(data=dict(item_list=[dict(item_id=str(r['aweme_id'])) for r in records],
                                  required_member_id=str(records[0]['aweme_id'])))
        if name.startswith('video_') and str(params['aweme_id']) in self.records:
            return dict(data=dict(aweme_detail=self.records[str(params['aweme_id'])]))
        raise LibraryStopped('public_source_operation_not_supported')


def capture_reference(home, output, *, max_candidates=10, freshness_hours=72, visual_library=None,
                      exclude_trend_ids=(), exclude_reference_sha256s=(),
                      max_video_bytes=hot_capture.DEFAULT_MAX_VIDEO_BYTES,
                      min_free_bytes=hot_capture.DEFAULT_MIN_FREE_BYTES, cache_root=None):
    if type(max_video_bytes) is not int or max_video_bytes < 1 or type(min_free_bytes) is not int or min_free_bytes < 0:
        raise ValueError('capture_invalid_download_limits')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    cache = Path(home) / 'shared/data/hot_references'
    with file_lock(cache / '.capture.lock'):
        # All paid MODEL unknowns still stop this branch across every source/hour.
        # The unchanged Wellbyte branch retains its global paid-API guard too.
        for path in [*cache.glob('*/template_*/model/library_state.json'),
                     *cache.glob('*/template_*/audio_review/state.json')]:
            value = json.loads(path.read_text())
            if any(c['status'] in {'submitted', 'uncertain'} for c in value.get('calls', [value])):
                raise LibraryStopped('capture_existing_unknown_stops_branch:' + str(path))
        now = datetime.now(timezone.utc)
        # Reuse recently received public bytes across a clock-hour boundary too.
        roots = []
        for ledger in cache.glob('public_*/public_calls.json'):
            calls = json.loads(ledger.read_text())
            board = next((c for c in calls if c['name'] == 'public_board' and c['status'] == 'received'), None)
            if board and 0 <= (now - datetime.fromisoformat(board['finished_at'])).total_seconds() < 3600:
                roots.append(ledger.parent)
        root = max(roots, key=lambda p:p.name) if roots else cache / ('public_' + now.strftime('%Y%m%d_%H'))
        if cache_root is not None:
            root = Path(cache_root).resolve(strict=True)
            if root.parent != cache.resolve() or not root.name.startswith('public_') or not (root/'public_calls.json').is_file():
                raise ValueError('public_capture_invalid_existing_cache_root')
        api = PublicSource(home, root)
        if cache_root is not None and api._cached('public_board') is None:
            raise LibraryStopped('public_capture_existing_board_receipt_required')
        return hot_capture._capture_from_api(home, output, api, root, now,
            max_candidates=max_candidates, freshness_hours=freshness_hours, visual_library=visual_library,
            excluded_trends={str(v) for v in exclude_trend_ids}, excluded_references=set(exclude_reference_sha256s),
            max_video_bytes=max_video_bytes, min_free_bytes=min_free_bytes)
