"""Static checks for the release workflow's permission split."""

from __future__ import annotations

import pathlib

WORKFLOW = pathlib.Path(__file__).parents[1] / ".github/workflows/release.yml"


def test_release_workflow_splits_test_and_write_jobs():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in text
    assert "jobs:\n  test:" in text
    assert "  release:\n    needs: test" in text
    assert "      contents: write" in text
    assert "      id-token: write" in text
    assert "      attestations: write" in text
    assert "GH_TOKEN: ${{ github.token }}" in text
