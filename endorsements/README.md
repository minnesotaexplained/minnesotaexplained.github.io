# Ballot endorsements

`index.html?district=MPLS` (the default) shows every race on a Minneapolis ballot;
`index.html?district=61A` shows just House District 61A. Add `&race=state-house`
(or any filter chip's key) to open with a filter applied.

## Files

| File | What it is |
|---|---|
| `data/61A.json` | The 61A ballot: races shared by all of Minneapolis plus 61A's district races. Edit endorsements, descriptions, and the Election Day box here. |
| `data/MPLS.json` | **Generated.** Don't edit by hand; run `scripts/build_mpls.py`. |
| `data/sources/mpls_candidates.json` | Profiles for Minneapolis district-race candidates not on the 61A ballot. Edit freely. |
| `data/sources/mpls_ballots.json` | Every Minneapolis precinct's ballot, from the Secretary of State. |

## Scripts

```
python3 scripts/build_mpls.py           # rebuild data/MPLS.json after any edit
python3 scripts/fetch_mpls_ballots.py   # re-download precinct ballots (slow; resumable)
python3 scripts/fetch_ballot.py <id>    # starter JSON for one address's ballot
```

Who is endorsed in Minneapolis district races, the "not endorsed" list, and the
page's intro text are settings at the top of `scripts/build_mpls.py`.

### Candidate fields

`name`, `party`, `description` (your endorsement text), `analysis`, `image`, `links`
(`{label, url}`), optional `legislation`, `board_record`, `integrity_index`, and
`"endorsed": false` to list someone for information only.

### Known gap

The precinct data covers 135 of the city's 137 precincts (Ward 1 Precinct 2 and
Ward 11 Precinct 3 are missing; the state's site stopped responding mid-download).
Run `fetch_mpls_ballots.py` again when it's back, then `build_mpls.py`; the page's
warning disappears once all 137 are present.
