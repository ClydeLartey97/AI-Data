# Renewables.ninja fixtures

`pv.json` and `wind.json` are real API responses, captured from
`https://www.renewables.ninja/api/data/pv` and `.../data/wind` for 51.5°N,
-0.12°E on 2023-06-01 at a capacity of 1.0. Eight hours each.

They are copied from the National Grid Tool's own test fixtures rather than
hand-written, for the same reason the Redfish fixtures are a pruned copy of
DMTF's published mockup: a fixture invented to match a parser proves the parser
matches the fixture and nothing else. These carry the real payload shape —
millisecond-epoch string keys, an `electricity` value in kW against the
requested capacity, and the `metadata.params` echo that records what was
actually simulated.

Renewables.ninja data is CC BY-NC 4.0. See
https://www.renewables.ninja/documentation.
