# Internet-Drafts

This directory holds IETF Internet-Draft sources for standards-track
specifications derived from Brain Engine's patent-defensible
artifacts.

## Contents

| File | Status |
| --- | --- |
| `draft-cendra-owner-policy-dsl-00.txt` | initial individual submission |

## Submission workflow

1. **In-house engineering review** — confirm the I-D matches the
   reference implementation under `brain_engine/owner_policy/` on
   the `dev` branch.
2. **Outside counsel review** — IP positioning relative to the
   patent claims in `patents/PATENT_CLAIMS.md` (Candidate 3).
3. **Submit to IETF datatracker** via
   https://datatracker.ietf.org/submit/ — the `-00` filename suffix
   marks the first submission; subsequent revisions increment to
   `-01`, `-02`, ….
4. **Discussion forum** — the I-D author may post to relevant IETF
   working-group lists (probably `dispatch@ietf.org` first) to
   request a venue.  `secdispatch@ietf.org` is a candidate for the
   security-considerations heavy aspects.

## Honest scope

- The I-D is an **individual submission** authored by Cendra; it
  has not been adopted by any working group.  Adoption depends on
  community review and chair selection.
- The format conforms to the conventions in
  https://authors.ietf.org/ for plain-text I-Ds: 72-column width,
  page-feed footers, BCP-14 keywords, ABNF grammar, mandatory
  sections (Abstract / Status / Copyright / TOC / Body / Security /
  IANA / References / Author).
- The reference implementation in this repository is the
  authoritative source.  The I-D paraphrases the runtime; any
  divergence should be resolved in favour of the code.
