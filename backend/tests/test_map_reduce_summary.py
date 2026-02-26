from pathlib import Path


def test_map_reduce_summary_endpoint(client, tmp_path: Path):
    sample = tmp_path / 'long_notes.txt'
    sample.write_text(
        'Section one about finance and bills. ' * 120 +
        'Section two about maintenance tasks and schedules. ' * 120,
        encoding='utf-8',
    )

    r = client.post('/v1/ingestion/jobs', json={'file_paths': [str(sample)]})
    assert r.status_code == 200

    rs = client.post('/v1/search', json={'query': 'finance', 'top_k': 1, 'score_threshold': 0, 'ui_lang': 'en', 'query_lang': 'en'})
    assert rs.status_code == 200
    hit = rs.json()['hits'][0]
    before_doc = client.get(f"/v1/documents/{hit['doc_id']}").json()

    rm = client.post('/v1/summaries/map-reduce', json={'doc_id': hit['doc_id'], 'ui_lang': 'zh', 'chunk_group_size': 4})
    assert rm.status_code == 200
    out = rm.json()

    assert out['doc_id'] == hit['doc_id']
    assert out['status'] == 'completed'
    assert out['total_chunks'] >= 1
    assert len(out['sections']) >= 1
    assert len(out['sources']) >= 1
    assert out['quality_state'] in {'ok', 'needs_regen', 'llm_failed'}
    assert isinstance(out['fallback_used'], bool)
    assert isinstance(out['quality_flags'], list)
    assert out['longdoc_mode'] in {'normal', 'sampled'}
    assert int(out['pages_total']) >= int(out['pages_used']) >= 1
    assert isinstance(out['applied'], bool)
    assert isinstance(out['apply_reason'], str)
    assert isinstance(out['category_recomputed'], bool)
    assert isinstance(out['tags_recomputed'], bool)
    assert isinstance(out['qdrant_synced'], bool)
    assert isinstance(out['cascade_applied'], bool)
    assert isinstance(out['cascade_reason'], str)
    assert all(sec['summary']['en'] or sec['summary']['zh'] for sec in out['sections'])
    assert len(out['sources']) <= 10

    rd = client.get(f"/v1/documents/{hit['doc_id']}")
    assert rd.status_code == 200
    doc = rd.json()
    assert doc['summary_quality_state'] == out['quality_state']
    if out['quality_state'] == 'ok':
        assert out['applied'] is True
        assert out['apply_reason'] == 'ok'
        assert doc['summary_en'] == out['short_summary']['en']
        assert doc['summary_zh'] == out['short_summary']['zh']
        if out['cascade_applied'] is True:
            assert out['cascade_reason'] == 'ok'
            assert out['category_recomputed'] is True
            assert out['tags_recomputed'] is True
    else:
        assert out['applied'] is False
        assert out['apply_reason'] in {'needs_regen', 'llm_failed', 'quality_not_ok'}
        assert out['cascade_applied'] is False
        assert doc['summary_en'] == before_doc['summary_en']
        assert doc['summary_zh'] == before_doc['summary_zh']
