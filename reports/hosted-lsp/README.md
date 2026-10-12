# Hosted LSP evidence

Start with [final results](final-results.md) for the complete native pytest run,
exact command, source/binary identity and failure classification. Neither mapping
checkboxes nor source-review coverage mean a test passed.

## Current source evidence

- [Design and explicit scope limits](../../reviews/lsp/hosted-lsp.md)
- [Frozen executable inputs](input-index-final.json)
- [Final semantic coverage review](b-coverage-review.md) and its
  [all-case structured bindings](b-coverage-review.json)
- [Scoped mapping check](scoped-validation.json), with
  [byte-proven baseline orphan defects](baseline-mapping-issues.json)
- [SDK adapter verification](adapter-validation.md)
- [Runtime build and mining verification](runtime-validation.md)

The source review selects 51 behavior cases, mapped through 60 bindings to 50
functions and 72 concrete selectors. It is not exhaustive coverage of every LSP
feature, and reviewer isolation is not host-attested. Native pytest outcomes are
reported at suite/selector level; no controlled-framework per-case acceptance
receipt or product-merge approval is claimed.

## Historical evidence

`input-index.json`, `input-index-v2.json`, the pre-namespace-revision input index,
`b-design-review*.json` and `a-design-response.json` describe earlier design
snapshots. Their hashes and execution state are historical, not the final result.
The historical indexes reference a locally generated `product-lsp.diff`, excluded
to avoid copying product source into this repository. The underlying comparison
is reproducible with `git -C ../fiber-lsp diff 23a597c5e81d06f5c49a3744dffe244e73530a60..8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8`;
the design cites the relevant source paths at that immutable head.
The final coverage report preserves the original findings and explains their
verified dispositions; [implementation responses](a-coverage-response.md) are
not independent execution evidence.

[Registry](registry-runtime.md), [payments](payments-runtime.md), and the earlier
[signing liveness observation](signing-liveness-finding.json) preserve focused and
initial attempts, including genuine failures and setup defects. Later focused
passes do not erase those attempts or establish repeatability by themselves.
Final execution evidence takes precedence when describing the frozen suite.

Raw process logs, other-attempt JUnit output, ephemeral configuration and wallet
stores remain local. The complete final pytest output and JUnit were inspected
and included with their hashes; they and the diagnostic extracts contain no
wallet private keys, bearer tokens or payment preimages. GitHub CI uploads pytest
output and JUnit only, never process stores.
