# Patents — Brain Engine

This directory contains filing-ready independent-claim drafts for the
patent candidates identified in `latest_research.md`.

## Contents

| File | Purpose |
| --- | --- |
| `PATENT_CLAIMS.md` | USPTO-style independent claim drafts for 8 candidates |
| `IMPLEMENTATION_MAP.md` | Per-candidate map of claim limitations → dev-branch modules |

## Workflow

1. **In-house engineering review.** Verify every claim limitation
   maps to a named module under `brain_engine/` on the `dev`
   branch.  Run `pytest tests/` to confirm reduction-to-practice.
2. **Outside counsel review.** §101 patentable-subject-matter,
   §102 novelty, §103 obviousness, §112 enablement / written
   description.  Counsel decides provisional vs non-provisional
   vs PCT.
3. **Inventor declaration** + **assignment** to Cendra / Bookly.
4. **Drawing preparation.** Each candidate needs at least one
   architecture block diagram.  The repo's module hierarchy under
   `brain_engine/<moat>/` is the figure source-of-truth.

## Honest scope

These drafts are **engineering-prepared**.  They have not been
reviewed by patent counsel.  Do not submit to USPTO before
attorney finalisation.  External-blocker dependent claims (RSSM
with learned latent for Candidate 2; production GRPO rollout for
Candidate 6) describe planned embodiments — counsel should decide
whether to carve them into a continuation-in-part filed once the
implementations land.
