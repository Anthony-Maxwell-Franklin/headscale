# Headscale paper research artifact

The original coursework report remains in `../Report/Report.tex`. This directory
contains research data and scripts; it is not yet a submission-ready paper.

## Linux reproduction

Reproduction requires Bash, Git, uv and Python 3.12. Basic measurements and
data collection use Python's standard library; all Python entry points run
through the same locked uv environment, including plotting and statistics.
Set up the environment as described below, then run from the workspace root:

```bash
bash reproduce.sh --upper v0.28.0 --output Paper/results/v0.28.0
bash reproduce.sh --upper v0.29.5 --output Paper/results/v0.29.5
```

The entry point is offline. Each output manifest pins the tags, commit hashes,
script hash, legacy issue CSV hash and measurement limitations. JSON preserves
full precision; CSV supplies analysis tables. The legacy mode intentionally
preserves the coursework definitions for comparison. In particular, the old
PowerShell odd-sample M3 median uses rounded integer indexing; the mathematically
correct median is also exported. The legacy HHI reciprocal uses rounded HHI.

Quarterly tables separately report production Go, tests, generated code,
documentation and other artifacts. They use committer time and hashed email
identities, whereas legacy tables use author time and case-insensitive names.
Neither identity rule establishes a validated person identity. Path statistics
do not yet follow file renames. First/last quarters can be partial. Counts and
concentration are descriptive, not evidence of technical debt or causal effects.

## Independent upstream history

An existing dirty checkout must not be reset. Create a separate bare repository:

```bash
git clone --bare --no-hardlinks headscale Paper/data/headscale.git
git --git-dir=Paper/data/headscale.git fetch https://github.com/juanfont/headscale.git \
  '+refs/tags/*:refs/tags/*' '+refs/heads/main:refs/heads/research-upstream-main'
```

Tags are analysis endpoints, not automatically official release dates. API
release metadata is needed to establish publication time. Do not advance the
endpoint without recording a new snapshot.

## Public issue/PR collection

```bash
bash run-research.sh python Paper/scripts/collect_github.py --output Paper/data/github-2026-10-13
# If interrupted:
bash run-research.sh python Paper/scripts/collect_github.py --output Paper/data/github-2026-10-13 --resume
```

Anonymous access is supported. Authentication reads `GITHUB_TOKEN`, then
`GH_TOKEN`, then the existing protected local `.secrets/github_token` file
(mode 600). Never put a credential in the script or a committed command file.
This collects all states and labels, retaining raw pages and integrity hashes.
It does not yet collect review events, detailed PR merge data or repair links.
Only a manifest with `complete: true` represents finished list pagination.

The issue durations in the legacy output still use the original closed-bug CSV;
the new endpoint must not be represented as having newly collected issue data.
Research analysis must distinguish verified repairs, administrative closures,
reopened issues and open/censored observations before reporting resolution time.

## Validation

```bash
bash run-research.sh python -m unittest discover -s Paper/scripts -p 'test_*.py' -v
bash -n reproduce.sh
```

The golden regression compares all five original stages against the coursework
commit counts, author counts, touches, HHIs and closed-issue sample sizes.
See `research_protocol.tex` for research questions and submission-readiness gates.

## Baseline audit limits

`results/baseline_audit.json` records the parity scope and remaining differences.
Expansion M3 now reproduces 42.22% after preserving PowerShell case-insensitive
path keys. Expansion M4A has 157 versus 156 samples (mean 8.34 versus 8.36 days). Full
PowerShell parity is not claimed. These require an event-level audit; tests of
M1/M2 parity do not prove equivalence of the entire pipeline.

## Research measurements and PR process data

```bash
bash run-research.sh python Paper/scripts/research_metrics.py
bash run-research.sh python Paper/scripts/collect_process.py --output Paper/data/pr-process-2026-10-13 --max-requests 300
bash run-research.sh python Paper/scripts/analyze_process.py
```

`research_metrics.py` verifies raw issue pages, exports a 100-case human coding
sample, includes open issues as right-censored observations, and calculates
time-to-closure rather than verified repair time. Rename sensitivity follows
Git's inferred rename edges; path reuse, split files and low-similarity moves
remain validation concerns. The creation cohorts use current labels, not
reconstructed historical labels.

`collect_process.py` uses resumable, hashed endpoints and a per-run request
budget. It collects all PR list records and a deterministic year-stratified
pilot of detail/review/comment records. Only complete endpoint sets enter pilot
analysis. Partial pilot sampling does not support population estimates. Use
`--all-details` in a separate output directory for a complete PR event census.
`--anonymous` explicitly uses public anonymous access when authentication is
unavailable; invalid authentication is never silently treated as successful.

`analyze_process.py` reports merge and closure without merge as competing
dispositions. First human responses exclude author self-comments and bots.
Current draft state and association do not reconstruct historical readiness
or maintainer status; those require additional timeline data.

## uv-managed research environment

Workspace-local uv is installed at `.tools/uv/uv`. The project definitions are
`Paper/pyproject.toml`, `Paper/.python-version` and `Paper/uv.lock`.
The managed environment is `Paper/.venv`; no shell activation is needed when
using `run-research.sh`. The local installation reports uv 0.13.0 and Python 3.12.3.

```bash
UV_CACHE_DIR="$PWD/.cache/uv" .tools/uv/uv sync --project Paper --locked --python /usr/bin/python3
bash run-research.sh python --version
bash run-research.sh python -m unittest discover -s Paper/scripts -p 'test_*.py' -v
bash run-research.sh python Paper/scripts/research_metrics.py
bash run-research.sh python Paper/scripts/analyze_process.py
bash run-research.sh python Paper/scripts/make_figures.py
```

`run-research.sh` uses the locked uv project offline, keeping its cache inside
the workspace. Network access is only needed for the initial sync and API
collection. Data snapshots, not the environment, determine analysis cutoffs.
Six standalone PDF/PNG/SVG figures are generated from CSV exports with a
source-hash manifest; the KM survival values are independently checked using
SciPy's censored empirical distribution implementation.

## Manuscript and ongoing inference

The working manuscript is `manuscript.tex`; it reports completed measurements
and explicitly identifies incomplete inference and independent coding. Its
numbers are generated from source-hashed exports, rather than typed estimates.

```bash
bash run-research.sh python Paper/scripts/analyze_issue_evidence.py
bash run-research.sh python Paper/scripts/write_manuscript_data.py
XDG_CACHE_HOME="$PWD/.cache" .tools/tectonic/tectonic --untrusted --outdir Paper/build Paper/manuscript.tex
bash run-research.sh python Paper/scripts/verify_artifact.py
```

Tectonic is installed locally from a SHA-256-verified official release. The first
compile requires official TeX resource downloads; subsequent cached compilation
can use `--only-cached`. No shell escape is enabled.

The full PR metadata census is separate from the completed 76-PR process pilot:

```bash
bash run-research.sh python Paper/scripts/collect_process.py --output Paper/data/pr-census-2026-10-13 --all-details --detail-only --max-requests 1600
bash run-research.sh python Paper/scripts/analyze_census.py --require-timelines
bash run-research.sh python Paper/scripts/fit_maintenance_models.py
```

Submitted reviews and lifecycle timelines have separate completeness gates:

```bash
bash run-research.sh python Paper/scripts/collect_process.py --output Paper/data/pr-census-2026-10-13 --all-details --reviews-only --max-requests 1800
bash run-research.sh python Paper/scripts/collect_pilot_timelines.py --input Paper/data/pr-census-2026-10-13 --max-requests 2300
```

Both commands resume from complete hashed endpoints. Metadata, reviews and
timelines now cover all 2,166 planned PRs. The primary models use initial
processing episodes and reconstruct pending queues from strictly prior lifecycle
events. Final-disposition descriptions remain separate. Brief close/reopen
transitions are not assumed to be permanent decisions. Frozen metadata-only
exports and their fitting scripts remain in `results/metadata-only-baseline` and
`metadata-baseline-scripts`; they document the previous backlog approximation.

Exposure-window and elapsed-time diagnostics are retained with their own
feature and model provenance:

```bash
bash run-research.sh python Paper/scripts/analyze_census.py --require-timelines --lookback-days 30 --output Paper/results/pr-census-window30
bash run-research.sh python Paper/scripts/fit_maintenance_models.py --lookback-days 30 --input Paper/results/pr-census-window30 --output Paper/results/inference-window30
bash run-research.sh python Paper/scripts/fit_piecewise_models.py --lookback-days 30 --input Paper/results/pr-census-window30 --output Paper/results/inference-window30
bash run-research.sh python Paper/scripts/fit_piecewise_models.py
```

Repeat the three window-specific commands with 180 replacing 30 for the longer
exposure window. The elapsed-time diagnostic splits the PR's risk interval at
day 30 for every exposure window. It permits different concentration coefficients
before/after that split and uses role/experience baseline strata. This is an
exploratory check, not a confirmatory or causal claim. All model variants enter
the generated manuscript tables; numerical source/script/lock hashes and PDF
freshness are checked by `verify_artifact.py`. The statistical verification suite
contains 41 tests, including known synthetic hazards, risk-interval boundaries,
historical pending queues, matched review-credit conservation and human-coding
input/adjudication checks, plus exact commit linkage and neutral evidence rendering.

Endpoint and quarter-influence diagnostics use:

```bash
bash run-research.sh python Paper/scripts/check_model_robustness.py
```

This joins closures followed by reopening within five minutes as a sensitivity,
leaving actual historical covariates intact. Near-cutoff closures remain censored
if the complete grace window cannot be observed. It also omits each informative
creation quarter from fitting. These related fits are influence checks, not
predictions or independent replications; every fit and failure is retained.

Full review-role analysis and the ongoing merged-PR file sensitivity use:

```bash
bash run-research.sh python Paper/scripts/analyze_review_census.py --require-timelines
bash run-research.sh python Paper/scripts/analyze_review_identity_bounds.py
bash run-research.sh python Paper/scripts/first_disposition_curve.py
bash run-research.sh python Paper/scripts/collect_pr_files.py --max-requests 1500
bash run-research.sh python Paper/scripts/analyze_file_roles.py
bash run-research.sh python Paper/scripts/audit_event_clocks.py
bash run-research.sh python Paper/scripts/prepare_validation.py
```

The review census distinguishes attributable submitted reviews from comments,
bots, self-reviews and unattributed actors. One fractional review credit per PR
is divided among observed reviewers for matched author/reviewer/integrator
comparisons; it is not an effort measurement. Complete file lists for 1,318 merged
human-account PRs support production-code versus documentation/configuration
comparisons, including a subset whose changed paths are exclusively Go files.
These are nonexclusive final-file scopes; they neither identify what each reviewer
read nor supply submission-time model covariates. The primary disposition figure
uses initial episodes, while the earlier final-state curve remains archived.
The two human coding forms in `validation/` are blank and independently ordered;
their instructions and reviewer declarations do not establish completed coding.
Running preparation again preserves filled human fields. The codebook allows
mixed responsibility and records administrative closure separately from supported
repair evidence.

The identity sensitivity keeps the 1,259 known-reviewed merged PRs fixed and
applies time/open-interval eligibility to unattributed review records. It exports
per-PR inputs, an unattributed-record audit and quarterly conservative HHI outer
bounds in `results/review-identity-sensitivity/`. The endpoints relax per-PR
assignment constraints and are not sharp bounds or confidence intervals.
Hypothetical identity scenarios are kept separate from observed account data.
Tests enumerate small feasible assignments and independently optimize the lower
bound. The analysis does not cover deleted or informal reviews, known-account
aliases, or knowledge ownership.

Historical component checks use a fixed plan in `repair_validation_plan.json`:

```bash
bash run-research.sh python Paper/scripts/validate_repair_cases.py --prepare-only
bash run-research.sh python Paper/scripts/validate_repair_cases.py --allow-downloads
```

The first command prepares isolated official source archives without executing
tests. The second can download the pinned Go 1.24.0 toolchain and checksum-verified
modules into workspace caches. Omit `--allow-downloads` for a cached run. Each
versioned output directory under `results/repair-validation/` retains the plan,
Go JSON test events, stderr, source hashes and explicit test outcomes. A failed
build or an unexecuted target cannot count as a reproduced defect. Checks use
the two source-available final-closure commits with exact PR matches; they do not
estimate repair prevalence or fill the deferred human coding forms.

Both selected cases have completed the before/after checks, including a cached
rerun. There are three regression oracles and two normal-case controls; each
executes on both versions. Semantic failure diagnostics and actual Go test events
are verified with:

```bash
bash run-research.sh python Paper/scripts/verify_repair_validation.py
```

These checks establish bounded component behavior, not original deployment
reproduction or an estimate of the fraction of repaired reports.

## Declared human coding and adjudication

Neutral offline evidence packets follow each reviewer's existing case order:

```bash
bash run-research.sh python Paper/scripts/build_review_materials.py
```

Open `results/review-materials/rater_1.html` or `rater_2.html`. The packets expose
all cached timeline events, comments, 48 available commit patches and context for
12 exactly matched merged PRs across 10 sampled issues. A reference or matching
merge SHA is a pointer, not a repair label. Unavailable commits remain explicit;
later-retrieved PR context requires timestamp and edit checks. Existing coding
forms stay separate and unchanged, and no assistant suggestions are included.
The bundle manifest hashes both source pages and generated files.

```bash
bash run-research.sh python Paper/scripts/analyze_human_validation.py
```

The entry point currently exits 2 because both coding forms are empty and human
completion is undeclared. No agreement or category estimates are generated from
those inputs. It validates pinned membership, codebook values and source
declarations; reviewer identity and independence remain self-reported.

When actual paired coding is available, it exports original labels and sample
agreement before adjudication. Disagreement templates retain the source labels
and their input hash. Population estimates require complete consensus/adjudicated
classifications and use the sampling probabilities. Conservative per-category
intervals invert exact finite-population hypergeometric stratum tails, adjusted
across strata. They address sampling error, not annotation truth.

`--mode single-rater` supports an explicitly declared single-reviewer design with
no inter-rater agreement claim. Input forms stay read-only; versioned `run_*`
directories prevent mixing results from different inputs or code versions.
Synthetic test fixtures never enter the study's annotation files or manuscript.

The census analysis refuses to estimate role concentration or fit models while
any planned metadata endpoint is missing. Metadata completeness does not imply
review-event completeness. Submission covariates use only earlier events. Final
change size is exported descriptively but excluded from the primary model.
Response opportunities end at disposition; disposition without response is a
competing event rather than continued waiting until the collection date.

## English manuscript and local review package

The working manuscript, protocol, review instructions and submission notes are
in English. The author-confirmed affiliation is University of Nottingham Ningbo
China, and the current sole author is Xiaoyu SHEN. The contact email is
provisional; corresponding authorship and possible supervisor coauthorship
remain unsettled. Human coding is deferred at the author's request.

The same-project and thematic overlap check is recorded in
`submission/literature_overlap_assessment.txt` and `literature_search_log.json`.
It is a targeted scoping search with explicit access limitations; no first-study
claim follows from a missing search result.

After compiling the working PDF and verifying it, create a local source package:

```bash
bash run-research.sh python Paper/scripts/verify_artifact.py
bash run-research.sh python Paper/scripts/package_review_sources.py
```

`submission/headscale-manuscript-review.zip` contains flattened manuscript
sources, figures, the working PDF and draft author/venue statements. It is for
local review and contains no credentials. It does not include the full raw-data
research artifact, and creating it does not submit or publish the work.

## Completed research evidence

- Release-pinned Git history and author/committer-time sensitivity.
- Four core aliases attributed through representative GitHub commits.
- Complete list and detailed metadata for all 2,166 PRs in the pinned census.
- Paired author/integrator concentration on identical human PR populations.
- Complete reviews/comments/timelines for the fixed 76-PR process pilot.
- Complete comments/timelines for the 100-issue validation sample.
- Exploratory cause-specific merge models with quarter-clustered intervals and
  a role/experience baseline-stratification sensitivity.

Models do not establish causality, and their residual diagnostics require
further validation. Semantic outcome coding and independent author review are
still pending. `build/manuscript.pdf` is a compiled working draft, not a claim
of acceptance or submission readiness. `build/verification.json` checks its
source hashes and basic PDF integrity.

Human annotations in `issue_validation_sample.csv` survive analysis reruns.
Evidence collection pins sample membership separately from annotation columns.
Provisional assistant case suggestions are explicitly identified in
`results/issue-validation/illustrative_case_suggestions.json`.
