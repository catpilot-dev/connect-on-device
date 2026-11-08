"""Tests for requirements_check: the boot-time "is pip needed?" check."""

import requirements_check as rc


def _req(tmp_path, text):
    p = tmp_path / "requirements.txt"
    p.write_text(text)
    return p


def _installed(monkeypatch, versions):
    def version(name):
        try:
            return versions[name.lower()]
        except KeyError:
            raise rc.PackageNotFoundError(name) from None
    monkeypatch.setattr(rc, "version", version)


def test_all_satisfied(tmp_path, monkeypatch):
    _installed(monkeypatch, {"aiohttp": "3.13.5", "pillow": "12.1.1"})
    p = _req(tmp_path, "# comment\naiohttp==3.13.5\n\npillow>=12.1.1  # floor\n")
    assert rc.unmet(p) == []


def test_reports_wrong_and_missing(tmp_path, monkeypatch):
    _installed(monkeypatch, {"aiohttp": "3.13.3"})
    p = _req(tmp_path, "aiohttp==3.13.5\nyarl==1.24.2\n")
    assert rc.unmet(p) == ["aiohttp==3.13.5 (have 3.13.3)", "yarl==1.24.2 (missing)"]


def test_main_exit_codes(tmp_path, monkeypatch, capsys):
    _installed(monkeypatch, {"aiohttp": "3.13.5"})
    assert rc.main([str(_req(tmp_path, "aiohttp==3.13.5\n"))]) == 0
    assert rc.main([str(_req(tmp_path, "aiohttp==3.13.6\n"))]) == 1
    assert "aiohttp==3.13.6 (have 3.13.5)" in capsys.readouterr().out


def test_repo_requirements_parse():
    """Every line in the shipped requirements.txt must parse."""
    reqs = rc.parse(rc.DEFAULT_PATH)
    assert "aiohttp" in {r.name for r in reqs}
