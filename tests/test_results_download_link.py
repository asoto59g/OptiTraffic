"""Download-link helpers for result artifacts."""

from ui.step_results import _download_href, _download_link


def test_download_href_encodes_binary_payload() -> None:
    assert _download_href(b"abc", "text/plain") == "data:text/plain;base64,YWJj"


def test_download_link_escapes_label_and_filename() -> None:
    link = _download_link("<CSV>", b"ok", 'edges"bad.csv', "text/csv")

    assert "data:text/csv;base64,b2s=" in link
    assert "&lt;CSV&gt;" in link
    assert 'download="edges&quot;bad.csv"' in link
    assert "<CSV>" not in link
