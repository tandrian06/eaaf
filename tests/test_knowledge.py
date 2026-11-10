import pytest

from eaaf.knowledge import load_knowledge, retrieve


def manifest(tmp_path, file):
    config = tmp_path / "config"
    config.mkdir()
    path = config / "knowledge.yaml"
    path.write_text(
        f"hr:\n  - id: leave\n    title: Leave\n    keywords: [congés]\n    file: {file}\n",
        encoding="utf-8",
    )
    return path


def test_loads_a_registered_markdown_document(tmp_path):
    department = tmp_path / "knowledge" / "hr"
    department.mkdir(parents=True)
    (department / "leave.md").write_text("# Leave\n\nUse the People Portal.", encoding="utf-8")

    corpus = load_knowledge(manifest(tmp_path, "leave.md"))
    sources = retrieve(corpus["hr"], "Comment demander des conges ?")

    assert [(source.id, source.excerpt) for source in sources] == [("leave", "Use the People Portal.")]


def test_rejects_a_reference_to_another_departments_document(tmp_path):
    department = tmp_path / "knowledge" / "it"
    department.mkdir(parents=True)
    (department / "vpn.md").write_text("# VPN\n\nIT-only content.", encoding="utf-8")

    with pytest.raises(ValueError, match="within hr"):
        load_knowledge(manifest(tmp_path, "../it/vpn.md"))
