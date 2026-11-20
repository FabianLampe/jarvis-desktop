"""Projekte anlegen, Pakete installieren (immer mit Rückfrage), committen, pushen, venv in run_script."""
import os
import subprocess
from dataclasses import dataclass

import pytest

from jarvis_core.tools import project_tools as pt
from jarvis_core.tools.registry import ALLOW, CONFIRM, InvalidArgs
from tests.tools_helpers import runtime


@dataclass
class Res:
    ok: bool = True
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    timed_out: bool = False
    cancelled: bool = False


class FakeExec:
    """Ersetzt project_tools._exec: legt bei `venv` eine .venv an und merkt sich alle Aufrufe."""
    def __init__(self, fail_pip=False):
        self.calls, self.fail_pip = [], fail_pip

    def __call__(self, ctx, argv, cwd, timeout=60.0):
        self.calls.append((list(argv), cwd))
        if argv[1:4] == ["-m", "venv", ".venv"]:
            py = cwd / ".venv" / "bin" / "python"
            py.parent.mkdir(parents=True)
            py.write_text("#!/bin/sh\n")
        if "pip" in argv and self.fail_pip:
            return Res(False, "", "ERROR: No matching distribution")
        return Res()


def execute(rt, tool, **args):
    return rt.registry.execute(rt.ctx, tool, args, 20000)


def assess(rt, tool, **args):
    return rt.registry.assess(rt.ctx, tool, args)


def test_project_create_builds_structure(tmp_path, monkeypatch):
    fx = FakeExec()
    monkeypatch.setattr(pt, "_exec", fx)
    rt = runtime(tmp_path)
    assert assess(rt, "project_create", name="rechner").level == ALLOW
    r = execute(rt, "project_create", name="rechner")
    root = rt.ctx.workspace / "Code" / "rechner"
    assert r.ok and root.is_dir()
    for f in ("README.md", ".gitignore", "main.py"):
        assert (root / f).is_file()
    assert ".env" in (root / ".gitignore").read_text() and ".venv/" in (root / ".gitignore").read_text()
    argvs = [c[0] for c in fx.calls]
    assert any(a[1:4] == ["-m", "venv", ".venv"] for a in argvs) and any(a[:2] == ["git", "init"] for a in argvs)
    assert not rt.ctx.tainted
    assert not execute(rt, "project_create", name="rechner").ok          # gibt es schon


@pytest.mark.parametrize("bad", ["../evil", "a b", "x/y", "-rf", "", "ä", "a" * 80, "a;b"])
def test_project_name_is_restricted(tmp_path, bad):
    rt = runtime(tmp_path)
    with pytest.raises(InvalidArgs):
        rt.registry.assess(rt.ctx, "project_create", {"name": bad})


@pytest.mark.parametrize("bad", ["git+https://evil/x", "../x", "/tmp/x.whl", "--index-url=http://x", "-e .", "pkg @ http://x",
                                 "a b", "http://x/y.tar.gz", "pkg;rm", "$(id)", "x==1 --pre"])
def test_package_names_are_validated(tmp_path, bad):
    with pytest.raises(InvalidArgs):
        pt.valid_packages([bad])


def test_good_packages_pass():
    assert pt.valid_packages(["requests", "flask>=3,<4", "numpy==1.26.4", "uvicorn[standard]", "Pillow", "ruamel.yaml"]) == [
        "requests", "flask>=3,<4", "numpy==1.26.4", "uvicorn[standard]", "Pillow", "ruamel.yaml"]


def test_install_always_confirms_and_passes_packages_as_argv(tmp_path, monkeypatch):
    fx = FakeExec()
    monkeypatch.setattr(pt, "_exec", fx)
    rt = runtime(tmp_path)
    execute(rt, "project_create", name="app")
    a = assess(rt, "project_install", project="app", packages=["requests"])
    assert a.level == CONFIRM and "requests" in a.summary
    r = execute(rt, "project_install", project="app", packages=["requests", "flask>=3"])
    assert r.ok and rt.ctx.tainted
    argv = fx.calls[-1][0]
    assert argv[-2:] == ["requests", "flask>=3"] and argv[1:4] == ["-m", "pip", "install"]
    assert argv[0].endswith(".venv/bin/python")
    with pytest.raises(InvalidArgs):
        rt.registry.assess(rt.ctx, "project_install", {"project": "app", "packages": ["--pre"]})
    assert not execute(rt, "project_install", project="gibtsnicht", packages=["x"]).ok


def test_create_with_packages_confirms_and_reports_pip_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "_exec", FakeExec(fail_pip=True))
    rt = runtime(tmp_path)
    assert assess(rt, "project_create", name="p", packages=["requests"]).level == CONFIRM
    r = execute(rt, "project_create", name="p", packages=["requests"])
    assert not r.ok and "FEHLGESCHLAGEN" in r.text and "FREMDER INHALT" in r.text
    rt.ctx.tainted = True
    assert assess(rt, "project_create", name="q").level == CONFIRM


def test_run_script_uses_project_venv(tmp_path):
    rt = runtime(tmp_path)
    proj = rt.ctx.workspace / "Code" / "p"
    (proj / ".venv" / "bin").mkdir(parents=True)
    py = proj / ".venv" / "bin" / "python"
    py.write_text("#!/bin/sh\necho VENV-PYTHON $1\n")
    py.chmod(0o755)
    (proj / "main.py").write_text("print('x')\n")
    r = execute(rt, "run_script", path=str(proj / "main.py"))
    assert r.ok and "VENV-PYTHON" in r.text and "main.py" in r.text
    other = rt.ctx.workspace / "solo.py"
    other.write_text("print('plain')\n")
    r = execute(rt, "run_script", path=str(other))
    assert r.ok and "plain" in r.text and "VENV-PYTHON" not in r.text


# --------------------------------------------------------------------- Git

@pytest.fixture
def repo(tmp_path, monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "T",
                 "GIT_COMMITTER_EMAIL": "t@example.org", "GIT_CONFIG_GLOBAL": os.devnull,
                 "GIT_CONFIG_SYSTEM": os.devnull}.items():
        monkeypatch.setenv(k, v)
    rt = runtime(tmp_path)
    root = rt.ctx.workspace / "r"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    return rt, root


def test_git_commit_in_workspace_is_allowed_and_skips_secrets(repo):
    rt, root = repo
    (root / "a.txt").write_text("a")
    (root / ".env").write_text("TOKEN=geheim")
    (root / "key.pem").write_text("x")
    assert assess(rt, "git_commit", repo=str(root), message="Erster Stand").level == ALLOW
    r = execute(rt, "git_commit", repo=str(root), message="Erster Stand")
    assert r.ok and "Erster Stand" in r.text and ".env" in r.text and "key.pem" in r.text
    files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
    assert files == ["a.txt"]
    assert not execute(rt, "git_commit", repo=str(root), message="nochmal").ok           # nichts zu committen


def test_git_commit_paths_message_and_policy(repo, tmp_path):
    rt, root = repo
    (root / "a.txt").write_text("a")
    (root / "b.txt").write_text("b")
    r = execute(rt, "git_commit", repo=str(root), message="-m nur a", paths=["a.txt"])
    assert r.ok
    assert subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split() == ["a.txt"]
    with pytest.raises(InvalidArgs):
        rt.registry.assess(rt.ctx, "git_commit", {"repo": str(root), "message": "x", "paths": ["--all"]})
    with pytest.raises(InvalidArgs):
        rt.registry.assess(rt.ctx, "git_commit", {"repo": str(root), "message": "x", "paths": ["../../etc/passwd"]})
    with pytest.raises(InvalidArgs):
        rt.registry.assess(rt.ctx, "git_commit", {"repo": str(root), "message": "  "})
    rt.ctx.tainted = True
    assert assess(rt, "git_commit", repo=str(root), message="x").level == CONFIRM


def test_git_commit_outside_workspace_confirms(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert assess(rt, "git_commit", repo=str(outside), message="x").level == CONFIRM


def test_git_commit_does_not_run_hooks(repo):
    rt, root = repo
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {root}/HOOK-RAN\nexit 0\n")
    hook.chmod(0o755)
    (root / "a.txt").write_text("a")
    assert execute(rt, "git_commit", repo=str(root), message="x").ok
    assert not (root / "HOOK-RAN").exists()


def test_git_push_always_confirms_never_forces(repo, tmp_path):
    rt, root = repo
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=root, check=True)
    (root / "a.txt").write_text("a")
    assert execute(rt, "git_commit", repo=str(root), message="x").ok
    a = assess(rt, "git_push", repo=str(root))
    assert a.level == CONFIRM and "origin" in a.summary
    for bad in ({"branch": "--force"}, {"branch": "+main"}, {"branch": "main:other"}, {"remote": "--mirror"}):
        with pytest.raises(InvalidArgs):
            rt.registry.assess(rt.ctx, "git_push", {"repo": str(root), **bad})
    assert not execute(rt, "git_push", repo=str(root), remote="nope").ok
    r = execute(rt, "git_push", repo=str(root), branch="main")
    assert r.ok
    heads = subprocess.run(["git", "--git-dir", str(bare), "branch"], capture_output=True, text=True).stdout
    assert "main" in heads


def test_project_routing():
    from jarvis_core.tool_intent import select_groups
    assert {"projects", "terminal"} <= select_groups("Lege ein neues Python-Projekt rechner an")
    assert "git" in select_groups("Committe die Änderungen") and "git" in select_groups("Pushe das Repo")


def test_git_commit_leaves_tracked_files_alone_and_skips_new_secrets(repo):
    rt, root = repo
    (root / ".env.example").write_text("KEY=\n")
    (root / "a.txt").write_text("a")
    assert execute(rt, "git_commit", repo=str(root), message="erst").ok
    ls = lambda: subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
    assert ls() == [".env.example", "a.txt"]                   # Vorlage ist kein Geheimnis
    (root / ".env.example").write_text("KEY=neu\n")
    (root / ".env").write_text("TOKEN=x")
    (root / "a.txt").write_text("b")
    r = execute(rt, "git_commit", repo=str(root), message="zweit")
    assert r.ok and ".env" in r.text
    assert ls() == [".env.example", "a.txt"]                   # getrackte Datei bleibt, neue .env nicht aufgenommen
    show = subprocess.run(["git", "show", "--stat", "--format=", "HEAD"], cwd=root, capture_output=True, text=True).stdout
    assert "deleted" not in show and ".env.example" in show and " .env " not in show.replace(".env.example", "")


def test_git_calls_disable_signing(tmp_path, monkeypatch):
    rt = runtime(tmp_path)
    seen = []
    monkeypatch.setattr(pt, "_exec", lambda ctx, argv, cwd, timeout=60.0: seen.append(argv) or Res())
    pt._git(rt.ctx, tmp_path, ["status"])
    assert "commit.gpgsign=false" in seen[0] and "tag.gpgsign=false" in seen[0]
