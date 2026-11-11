from job_tracker import version_align as va


def c(path, version, source):
    return {"path": path, "version": version, "source": source}


def test_prefers_working_binary_matching_daemon():
    cands = [c("/brew/0.167.0/codex", None, "brew"),            # hangs
             c("/d/0.167.0/codex", "0.167.0", "daemon"),
             c("/d/0.167.1/codex", "0.167.1", "daemon")]
    pick, status, why = va.choose(cands, daemon="0.167.1")
    assert pick["path"] == "/d/0.167.1/codex"
    assert status == "error" or status == "needs_review"
    assert "brew" in why


def test_all_aligned_is_ok():
    cands = [c("/brew/0.167.1/codex", "0.167.1", "brew"), c("/d/0.167.1/codex", "0.167.1", "daemon")]
    pick, status, _ = va.choose(cands, daemon="0.167.1")
    assert status == "done" and pick["source"] == "brew"


def test_brew_behind_daemon_is_flagged_but_routed():
    cands = [c("/brew/0.166.0/codex", "0.166.0", "brew"), c("/d/0.167.1/codex", "0.167.1", "daemon")]
    pick, status, why = va.choose(cands, daemon="0.167.1")
    assert pick["version"] == "0.167.1" and status == "needs_review" and "0.166.0" in why


def test_nothing_works():
    pick, status, _ = va.choose([c("/brew/x", None, "brew")], daemon="0.167.1")
    assert pick is None and status == "error"


def test_no_daemon_uses_newest_working():
    cands = [c("/a", "0.166.0", "brew"), c("/b", "0.167.0", "daemon")]
    pick, status, _ = va.choose(cands, daemon=None)
    assert pick["path"] == "/b"


def test_version_key():
    assert va.vkey("0.167.10") > va.vkey("0.167.9")
