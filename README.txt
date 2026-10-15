Headscale maintenance research
=============================

This research branch contains Xiaoyu SHEN's coursework baseline and the evolving
English research artifact, "Contribution Concentration and Maintenance Outcomes:
A Longitudinal Case Study of Headscale". Affiliation: University of Nottingham
Ningbo China. The paper is a working draft; human outcome coding, explanatory
validation, author review and final publication declarations remain unfinished.

Start here
----------
Paper/build/manuscript.pdf                  Compiled 20-page working draft
Paper/manuscript.tex                        Manuscript source
Paper/README.md                             Reproduction and measurement details
Paper/research_protocol.tex                 Research design and limitations
Paper/readiness_assessment.txt              Outstanding research requirements
Paper/submission/                          Review package and literature checks
Report/Report.tex                           Original coursework report

The study snapshot ends at official Headscale v0.29.5, commit
0f7d1e536bed9f01dd7161f7637af19bb0908335, released 2026-10-13.
Raw public GitHub API snapshots, integrity manifests, analysis outputs and
native before/after component-test evidence accompany the source. The results
describe this pinned snapshot. Blank human coding forms do not represent
completed human validation. This branch is a development archive, not a journal
or conference submission or a permanent archival deposit.

Fresh clone and Linux setup
--------------------------
git clone --branch research/headscale-maintenance --single-branch \
  https://github.com/Anthony-Maxwell-Franklin/headscale.git headscale-research
cd headscale-research

Install uv and provide Python 3.12, Bash and Git, then run:
uv sync --project Paper --locked --python python3.12

The upstream source repository is deliberately separate from this branch:
git clone https://github.com/juanfont/headscale.git headscale
git clone --bare --no-hardlinks headscale Paper/data/headscale.git
git --git-dir=Paper/data/headscale.git fetch https://github.com/juanfont/headscale.git \
  '+refs/tags/*:refs/tags/*' '+refs/heads/main:refs/heads/research-upstream-main'
git --git-dir=Paper/data/headscale.git rev-parse 'v0.29.5^{commit}'

Verify the printed revision matches the pinned hash above before analysis.
The Git repositories, downloaded toolchains, uv environment and caches are
regenerated locally. Existing dirty upstream checkouts must be preserved.

bash run-research.sh python -m unittest discover -s Paper/scripts -p 'test_*.py' -v
bash reproduce.sh --upper v0.29.5 --output Paper/results/v0.29.5
bash run-research.sh python Paper/scripts/verify_artifact.py

Full manuscript rebuilding additionally requires Tectonic; native repair-case
execution requires Go and can download the pinned Go 1.24.0 toolchain. See
Paper/README.md for commands, completeness checks and each analysis stage.
The existing raw snapshots permit analysis without recollecting GitHub data.
API authentication uses GITHUB_TOKEN, GH_TOKEN or a protected local
.secrets/github_token file. Credentials are never part of this branch.

Three frozen public PR-file pages include added/removed copies of the same
upstream integration-test TLS key fixture (Git blob
8a2df34be514dc4a02d02a9070a2a393fce2e698). Its public fixture content was verified
against the upstream Git object; the frozen API pages remain byte-for-byte
unchanged. This test fixture is unrelated to local account credentials.
