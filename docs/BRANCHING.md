# Branching and GitHub rulesets

## The flow

```
feature/<name>  ──PR──▶  dev  ──PR──▶  uat  ──PR──▶  main
```

- Work happens on `feature/<name>` branches (lowercase, digits, `.`, `_`, `-`). No other branch
  names can be created.
- `dev`, `uat` and `main` only change through pull requests, each reviewed and approved by the
  repository owner (`.github/CODEOWNERS`), with the `tests`, `docker` and `branch-flow` checks
  green and the branch up to date with its target. They cannot be deleted or force-pushed.
- `branch-flow` (`.github/workflows/branch-flow.yml`) is what enforces the path: a pull request
  into `dev` must come from a `feature/` branch, into `uat` only from `dev`, into `main` only
  from `uat`. Rulesets alone cannot look at a pull request's source branch, so this check does,
  and the ruleset requires it.

## Installing the rulesets

The two files in `.github/rulesets/` are in GitHub's import format.

1. On GitHub: **Settings → Rules → Rulesets → New ruleset ▾ → Import a ruleset**, pick
   `protected-branches.json`, save; repeat for `feature-branch-names.json`.
2. Or with the API (needs a token with repository administration):

   ```bash
   gh api -X POST repos/ayjabri/SelfRecruite/rulesets --input .github/rulesets/protected-branches.json
   gh api -X POST repos/ayjabri/SelfRecruite/rulesets --input .github/rulesets/feature-branch-names.json
   ```

3. Remove the older classic branch protection on `main` (**Settings → Branches**) so only the
   rulesets apply; both would otherwise be evaluated.

## The owner's own pull requests

GitHub never lets an author approve their own pull request. With one required approval from the
code owner, the owner's own pull requests could not merge in a repository with a single
collaborator. `protected-branches.json` therefore lists the **Repository admin** role as a bypass
actor in `pull_request` mode: the owner can merge their own pull requests with the bypass button
(the audit log records it), while everyone else needs the owner's approval. Delete the
`bypass_actors` entry to make the rule absolute, in which case a second reviewer is needed.
