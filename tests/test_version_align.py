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


def test_probe_requires_jail(monkeypatch, tmp_path):
    """2026-11-11: Homebrew cask answered `--version` plainly but hung inside the pipelines' sandbox-exec jail."""
    jail = tmp_path / "jail.sb"; jail.write_text("(version 1)")
    monkeypatch.setattr(va, "JAIL", jail)
    monkeypatch.setattr(va, "WORKDIR", tmp_path / "wd")
    calls = []

    def fake(cmd):
        calls.append(cmd)
        return None if cmd[0] == "sandbox-exec" else "0.167.1"
    monkeypatch.setattr(va, "_version", fake)
    assert va.probe("/brew/codex") is None
    assert calls[1][:3] == ["sandbox-exec", "-f", str(jail)] and calls[1][-2:] == ["/brew/codex", "--version"]


def test_probe_without_jail_is_plain(monkeypatch, tmp_path):
    monkeypatch.setattr(va, "JAIL", tmp_path / "missing.sb")
    monkeypatch.setattr(va, "_version", lambda cmd: "0.167.1")
    assert va.probe("/d/codex") == "0.167.1"
