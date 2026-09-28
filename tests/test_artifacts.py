import zipfile

import pytest

from venturi.artifacts import export_bundle, seal, unpack_bundle, verify
from venturi.models import write_json


def bundle(tmp_path):
    folder = tmp_path / "original"
    write_json(folder / "result.json", {"status": "failed"})
    (folder / "report.html").write_text("<p>Failed</p>")
    (folder / "native.dat").write_bytes(b"preserved bytes")
    seal(folder)
    return folder


def test_roundtrip_and_no_overwrite(tmp_path):
    folder = bundle(tmp_path)
    archive = export_bundle(folder, tmp_path / "run.zip")
    restored = unpack_bundle(archive, tmp_path / "with spaces Ω")
    assert verify(restored) == verify(folder)
    assert (restored / "native.dat").read_bytes() == b"preserved bytes"
    with pytest.raises(FileExistsError):
        export_bundle(folder, archive)


@pytest.mark.parametrize("mutation", ["change", "delete", "extra", "symlink", "report"])
def test_modified_evidence_cannot_be_exported(tmp_path, mutation):
    folder = bundle(tmp_path)
    if mutation == "change":
        (folder / "native.dat").write_text("edited")
    elif mutation == "delete":
        (folder / "native.dat").unlink()
    elif mutation == "extra":
        (folder / "extra.dat").touch()
    elif mutation == "symlink":
        (folder / "external").symlink_to(tmp_path)
    else:
        (folder / "report.html").write_text("<p>Passed</p>")
    with pytest.raises(ValueError):
        export_bundle(folder, tmp_path / "bad.zip")


@pytest.mark.parametrize(
    "entry", ["../escape", "root/../../escape", "/abs/escape", "root\\escape", "root/C:escape"]
)
def test_zip_paths_cannot_escape(tmp_path, entry):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr(entry, "bad")
    with pytest.raises(ValueError):
        unpack_bundle(archive, tmp_path / "extracted")
    assert not (tmp_path / "escape").exists()
