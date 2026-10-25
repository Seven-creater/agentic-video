import json
import pytest
from omni_story.evaluation import reference_exam as exam


def setup(tmp_path, monkeypatch, adjudicated):
    ledger=tmp_path/'request_budget.json';ledger.write_text('{}')
    monkeypatch.setenv('OMNI_EVAL_BUDGET_FILE',str(ledger));monkeypatch.setenv('HUNTER_RECEIPTS_DIR',str(tmp_path/'r'))
    q=tmp_path/'grading/t_x/elements_ids_v1/q_abc';q.mkdir(parents=True)
    (q/'attempt.abandoned_by_user.json').write_text('{}')
    if adjudicated:
        (tmp_path/'adjudications.jsonl').write_text(json.dumps(dict(call_id='judge:t_x/elements_ids_v1/q_abc',
            decision='abandoned_by_user',decided_by='user'))+'\n')
    return q,[]


def test_adjudication_id_matches_only_user_abandoned_question(tmp_path,monkeypatch):
    q,_=setup(tmp_path,monkeypatch,True)
    assert exam._judge_adjudication(q)=='judge:t_x/elements_ids_v1/q_abc'
    assert exam._judge_adjudication(q.parent/'q_other') is None


def test_no_adjudication_means_no_retry(tmp_path,monkeypatch):
    q,_=setup(tmp_path,monkeypatch,False)
    assert exam._judge_adjudication(q) is None


def test_non_user_decision_is_ignored(tmp_path,monkeypatch):
    q,_=setup(tmp_path,monkeypatch,False)
    (tmp_path/'adjudications.jsonl').write_text(json.dumps(dict(call_id='judge:t_x/elements_ids_v1/q_abc',
        decision='abandoned_by_user',decided_by='agent'))+'\n')
    assert exam._judge_adjudication(q) is None
