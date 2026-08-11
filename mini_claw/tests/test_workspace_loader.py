"""WorkspaceLoader path containment tests."""

from datetime import date

import pytest

from script.workspace_loader import WorkspaceLoader


def _make_loader(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return WorkspaceLoader(str(workspace)), workspace


def test_workspace_allows_valid_file_and_diary_access(tmp_path) -> None:
    loader, workspace = _make_loader(tmp_path)

    loader.write_file("MEMORY.md", "remember me")
    diary_date = date.today().isoformat()
    loader.append_diary("finished the security review", diary_date)

    assert loader.read_file("MEMORY.md") == "remember me"
    assert "finished the security review" in loader.read_diary(diary_date)
    assert (workspace / "memory" / f"{diary_date}.md").is_file()


@pytest.mark.parametrize(
    "invalid_date",
    [
        "2026-8-1",
        "20260801",
        "2026-02-30",
        " 2026-08-01",
    ],
)
def test_diary_rejects_noncanonical_dates(tmp_path, invalid_date: str) -> None:
    loader, _ = _make_loader(tmp_path)

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        loader.read_diary(invalid_date)


def test_diary_traversal_cannot_read_or_modify_external_file(tmp_path) -> None:
    loader, _ = _make_loader(tmp_path)
    external_file = tmp_path / "outside.md"
    external_file.write_text("outside content", encoding="utf-8")

    with pytest.raises(ValueError):
        loader.read_diary("../../outside")
    with pytest.raises(ValueError):
        loader.append_diary("malicious entry", "../../outside")

    assert external_file.read_text(encoding="utf-8") == "outside content"


def test_workspace_rejects_absolute_path(tmp_path) -> None:
    loader, _ = _make_loader(tmp_path)
    external_file = tmp_path / "outside.md"
    external_file.write_text("outside content", encoding="utf-8")

    with pytest.raises(ValueError):
        loader.read_file(str(external_file.resolve()))


def test_workspace_symlink_cannot_escape_or_modify_external_file(tmp_path) -> None:
    loader, workspace = _make_loader(tmp_path)
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    external_file = external_dir / "secret.md"
    external_file.write_text("outside content", encoding="utf-8")
    memory_dir = workspace / "memory"
    memory_dir.mkdir()
    try:
        (memory_dir / "escape").symlink_to(external_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    with pytest.raises(ValueError):
        loader.read_file("memory/escape/secret.md")
    with pytest.raises(ValueError):
        loader.write_file("memory/escape/secret.md", "changed")

    assert external_file.read_text(encoding="utf-8") == "outside content"
