"""Health must be able to say which commit the running process came from."""
from pathlib import Path

import build_info

SHA = "dcac952" + "0" * 33


def _checkout(tmp_path: Path, *, packed: bool = False, detached: bool = False) -> Path:
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    if detached:
        (git / "HEAD").write_text(SHA + "\n", encoding="utf-8")
    else:
        (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        if packed:
            (git / "packed-refs").write_text(f"# pack-refs\n{SHA} refs/heads/main\n", encoding="utf-8")
        else:
            (git / "refs" / "heads" / "main").write_text(SHA + "\n", encoding="utf-8")
    return tmp_path


def test_loose_packed_and_detached_heads_all_resolve(tmp_path):
    for kw in ({}, {"packed": True}, {"detached": True}):
        root = _checkout(tmp_path / str(kw), **kw)
        assert build_info.resolve(root, env={}) == {
            "commit": SHA[:12], "commit_full": SHA, "source": "git"}


def test_env_override_wins_and_garbage_is_ignored(tmp_path):
    root = _checkout(tmp_path)
    assert build_info.resolve(root, env={"NBX_BUILD_COMMIT": "ABCDEF1"})["source"] == "env"
    assert build_info.resolve(root, env={"NBX_BUILD_COMMIT": "ABCDEF1"})["commit"] == "abcdef1"
    assert build_info.resolve(root, env={"NBX_BUILD_COMMIT": "not a sha"})["source"] == "git"


def test_no_checkout_is_reported_as_unknown_not_guessed(tmp_path):
    assert build_info.resolve(tmp_path, env={}) == {"commit": None, "commit_full": None, "source": None}
    (tmp_path / ".git").write_text("gitdir: nowhere\n", encoding="utf-8")
    assert build_info.resolve(tmp_path, env={})["commit"] is None


def test_health_exposes_the_frozen_build_block():
    import asyncio

    import main

    body = asyncio.run(main.health())
    assert body["build"] == build_info.BUILD
    assert set(body["build"]) == {"commit", "commit_full", "source"}
