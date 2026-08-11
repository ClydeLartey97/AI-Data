# Redfish test fixtures

A pruned copy of the DMTF `public-rackmount1` Redfish mockup tree, taken from
https://github.com/DMTF/Redfish-Mockup-Server (BSD-3-Clause, Copyright DMTF).
Each JSON file retains its original `@Redfish.Copyright` notice.

These are real DMTF-published payload shapes, not hand-invented fixtures —
the same verification pattern used for the GB adapter, which was checked
against its upstream project's own fixtures before any live call. Directory
layout mirrors resource paths: `<path>/index.json` is the representation of
`/redfish/v1/<path>`, and `index.json` at this root is the service root.
