Headscale maintenance study: working-manuscript review package

This is a local draft for author/supervisor review. No venue submission
or permanent archival deposit has taken place. Semantic coding remains deferred
and unperformed; corresponding authorship and final declarations are pending.

The source files are flattened for standalone LaTeX compilation. With
Tectonic installed, run from this folder:
  tectonic --untrusted manuscript.tex
The included manuscript.pdf is the verified working draft.

This package contains manuscript sources and figures, not the full raw-data
reproduction artifact. Data, scripts and locked dependencies remain in the
parent research workspace. The decision-chain plan and instructions are
included; their preparation does not establish completed human review.
No credentials or virtual environment are included.
Regenerate this package from the workspace using:
  bash run-research.sh python Paper/scripts/verify_artifact.py
  bash run-research.sh python Paper/scripts/package_review_sources.py
Edit the main Paper/manuscript.tex source, rather than the generated copy.
