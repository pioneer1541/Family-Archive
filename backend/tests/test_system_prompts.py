def test_system_prompts_snapshot_endpoint(client):
    r = client.get("/v1/system/prompts")
    assert r.status_code == 200
    data = r.json()

    assert data["version"] == "prompt-v2"
    assert len(str(data["hash"])) == 64
    assert isinstance(data["items"], dict)
    for key in ["document_summary", "page_summary", "section_summary", "final_summary", "friendly_name", "category"]:
        assert key in data["items"]
        assert str(data["items"][key]).strip()
