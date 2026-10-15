"""Package verified working-manuscript sources for local author/supervisor review."""
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "Paper"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    verified = json.loads((PAPER / "build/verification.json").read_text())
    if digest(PAPER / "build/manuscript.pdf") != verified["pdf_sha256"]:
        raise ValueError("Working PDF differs from its verification record")
    for name, expected in verified["compiled_input_sha256"].items():
        if digest(PAPER / name) != expected:
            raise ValueError("Manuscript changed after verification: " + name)
    directory = PAPER / "submission/manuscript-review"
    directory.mkdir(parents=True, exist_ok=True)
    source_files = [PAPER / "manuscript.tex", PAPER / "references.bib",
                    PAPER / "build/manuscript.pdf",
                    *sorted((PAPER / "generated").glob("*.tex")),
                    *sorted((PAPER / "figures").glob("*.pdf")),
                    PAPER / "submission/declarations.txt",
                    PAPER / "submission/author_metadata.json",
                    PAPER / "submission/venue_assessment.txt",
                    PAPER / "submission/literature_overlap_assessment.txt",
                    PAPER / "submission/literature_search_log.json"]
    names = [path.name for path in source_files]
    if len(names) != len(set(names)):
        raise ValueError("Flattened source filenames collide")
    expected_names = set(names) | {"README.txt", "manifest.json"}
    if set(path.name for path in directory.iterdir()) - expected_names:
        raise ValueError("Review directory contains extra files; refusing to overwrite its contents")
    source_hashes = {}
    for source in source_files:
        source_hashes[str(source.relative_to(PAPER))] = digest(source)
        destination = directory / source.name
        if source.name == "manuscript.tex":
            text = source.read_text().replace("{generated/", "{").replace("{figures/", "{")
            destination.write_text(text)
        else:
            shutil.copyfile(source, destination)
    (directory / "README.txt").write_text(
        "Headscale maintenance study: working-manuscript review package\n\n"
        "This is a local draft for author/supervisor review. No venue submission\n"
        "or permanent archival deposit has taken place. Semantic coding remains deferred\n"
        "and unperformed; corresponding authorship and final declarations are pending.\n\n"
        "The source files are flattened for standalone LaTeX compilation. With\n"
        "Tectonic installed, run from this folder:\n"
        "  tectonic --untrusted manuscript.tex\n"
        "The included manuscript.pdf is the verified working draft.\n\n"
        "This package contains manuscript sources and figures, not the full raw-data\n"
        "reproduction artifact. Data, scripts and locked dependencies remain in the\n"
        "parent research workspace. No credentials or virtual environment are included.\n"
        "Regenerate this package from the workspace using:\n"
        "  bash run-research.sh python Paper/scripts/verify_artifact.py\n"
        "  bash run-research.sh python Paper/scripts/package_review_sources.py\n"
        "Edit the main Paper/manuscript.tex source, rather than the generated copy.\n"
    )
    packaged = {path.name: digest(path) for path in directory.iterdir() if path.name != "manifest.json"}
    manifest = dict(status="Working draft for local review; validation and declarations incomplete",
                    source_sha256=source_hashes, packaged_sha256=packaged,
                    script_sha256=digest(Path(__file__)),
                    verified_working_pdf_sha256=verified["pdf_sha256"],
                    pages=verified["pages"])
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    archive = PAPER / "submission/headscale-manuscript-review.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(directory.iterdir()):
            output.write(path, arcname=path.name)
    with zipfile.ZipFile(archive) as packaged_archive:
        if packaged_archive.testzip() is not None:
            raise ValueError("Review archive integrity check failed")
        for name, expected in packaged.items():
            if hashlib.sha256(packaged_archive.read(name)).hexdigest() != expected:
                raise ValueError("Review archive content differs from generated files")
    print(json.dumps(dict(archive=str(archive.relative_to(ROOT)),
                          source_files=len(packaged), pages=verified["pages"],
                          bytes=archive.stat().st_size, archive_sha256=digest(archive))))


if __name__ == "__main__":
    main()
