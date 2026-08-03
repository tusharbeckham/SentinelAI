---
name: Feature or model proposal
about: Propose a capability, a detector, or a change to the evaluation
title: ""
labels: enhancement
assignees: ""
---

## The problem

<!-- Describe the analyst-facing or scientific problem, not the solution you
have in mind. "Lateral movement recall is 0%" is a problem. "Add a GNN" is a
solution looking for one. -->

## Proposal

## If this touches the model

Answer these or the proposal cannot be evaluated:

- Which metric does it move, and by how much do you expect it to move?
- Does it help at the **operating point** (50 alerts/day, threshold 0.6252), or
  only in threshold-free ranking? AP that improves while budgeted recall does
  not is not an improvement for the analyst.
- What does it cost in end-to-end runtime? The full pipeline is ~136 s and that
  number is quoted in the README.
- Does it introduce a dependency? The repo deliberately ships two runtime
  packages, numpy and pandas. A third needs a strong argument.

## If this touches the console

- Which section, and does it survive `prefers-reduced-motion`?
- Does it need new artifact JSON? Those are generated, not hand-written.

## Alternatives considered

## Willing to implement it yourself?

- [ ] Yes
- [ ] No, proposing only
