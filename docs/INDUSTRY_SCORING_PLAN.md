# Plan: interest by company group and by company

Date: Oct 10, 2026 (revised the same day). Status: **approved and built Oct 10, 2026.**

As built, two things differ from the text below: groups and statements live inside the profile's
`targets` (keys `company_groups`, `company_interest`) with their own Settings page, and "score my
jobs" now re-scores by itself whatever was scored under older settings or another resume
(`targets_sha` and `golden_source_sha` in `fit_details`), so members no longer need an admin-only
re-score after changing their profile.

The request: the same role can be wanted at one kind of company and not at another. At a bank
or fintech, applied AI/ML and pure technical roles are welcome; at technology companies only
technical program manager roles; at Citi perhaps only Treasury / ALM roles. Each user defines
their own **groups** of companies, says what interests them **per group**, and **per company**
where one company differs. The score must both obey those statements and learn along the same
dimension from the user's marks.

## Why the score cannot do this today

- **No grouping of companies.** A company is just a name. The rule score has a `domain` part
  (10%) that says "banking or fintech: yes/no" and nothing else.
- **Role families do not depend on the company.** `D_ai_ml` weighs 0.9 everywhere. There is no
  TPM family: "technical program manager" sits in `A_product_delivery_ba` with product managers.
- **The learned model is additive.** A logistic regression over title words, company,
  seniority, family, the rule parts and the embedding can learn "NVIDIA: lower" and "machine
  learning: higher", but not "machine learning *at a tech company*: lower".

Measured on the owner's 285 marks (read-only experiments, Oct 10; groups bank / fintech / tech):

| Model | 5-fold AUC | A company with no marks yet | NVIDIA unseen | Anthropic unseen |
|---|---|---|---|---|
| Current | 0.79 | 0.61 | 0.43 | 0.39 |
| + group × role family (TPM split out) | **0.82** | **0.70** | **0.72** | 0.53 |
| + group × role and company × role for every company | 0.82 | 0.68 | 0.53 | 0.46 |

"No marks yet" = trained on every other company's marks, tested on that one: what happens when
an employer is added. Today the model is worse than a coin flip on an unseen tech company. The
group × role feature fixes most of it. Adding company × role for *every* company does not help
and generalises worse (each company's few marks are learned in isolation), so the company
dimension is added only where the user asks for it (below).

## What changes

### 1. Groups: per user, in the profile

New profile section, edited in Settings → **Company groups** (each user has their own):

```yaml
company_groups:
  Banks:    {companies: [JPMorgan Chase, Citi, BNY, Goldman Sachs, Bank Of America, ...]}
  Fintech:  {companies: [Stripe, Master Card, Visa]}
  Tech:
    companies: [NVIDIA, Anthropic, Databricks]
    interest: {families: [G_tpm], keywords: [], otherwise: cap}
company_interest:            # one company that differs from its group
  Citi: {families: [E_treasury_alm], keywords: [alm, liquidity, irrbb], otherwise: cap}
```

- A user creates, renames and deletes groups and assigns companies with a picker listing every
  company in the database. A company is in at most one group; unassigned = "no group".
- New profiles start with suggested groups (Banks, Fintech, Tech) filled from the current
  company list and **no interest rules**; nothing changes for a user until they add one.
- No shared industry list and no admin step: grouping is the user's own view.

### 2. Interest: what you would take there

An interest statement has three fields, the same at group and at company level:

- `families`: role families you would take (from Settings → Scoring → Title families);
- `keywords`: extra phrases; a job also qualifies if its **title or requirement bullets**
  contain one (whole words, case-insensitive);
- `otherwise`: what happens to every other job there: `cap` (score at most 30, reason shown on
  the job: "not a role you target at Tech companies") or `hide` (out of scope).

A job qualifies if its title matches **any** listed family (every family it matches is checked,
not only the best one) **or** contains any keyword ("any", not "both": confirmed by the owner
on Oct 10). Empty statement = no restriction.
**The company's statement, when there is one, replaces its group's.** The rule applies over the
learned score too, like the existing gates: what the user states beats what the model guesses.

A new default role family, `G_tpm: {weight: 1.0, keywords: [technical program manager,
technical project manager, tpm]}`, is added to the template and offered to existing profiles.

### 3. Learning along the same dimension

The preference model gains two categorical features:

- the job's **segment**: the company itself if it has its own `company_interest` entry,
  otherwise its group, otherwise "none";
- **segment × role family**.

So "AI/ML at Tech" and "AI/ML at Banks" get separate learned weights, and a company the user
singled out (Citi) gets its own, while everything else still shares strength through the common
features. One model per user stays; separate models per group would have 20–80 marks each and
overfit. The saved model records the grouping it was trained with; changing groups or interest
makes it out of date, and the existing retrain-when-stale stage retrains it at the next scoring
run (or "Retrain now").

### 4. Marks that contradict a rule

Stating a rule can contradict older marks (the owner marked 26 Anthropic jobs interested on
Oct 5, none of them TPM). Such marks keep teaching the model the opposite of the rule. The Job
tracker gets a filter, **"marks that conflict with my rules"**, listing interested marks on
jobs a rule now caps or hides, to flip or clear in one pass. The Company groups page shows the
count next to each rule.

### 5. Screens

- Settings → Company groups: groups with their companies; per group and per company the
  interest editor (family checkboxes, keyword tags, cap/hide); live counts ("Tech: 212 jobs in
  scope, 14 qualify, 198 would be capped").
- Job page: the group, and the rule that capped the job when one did.
- Job tracker filter bar: a group filter next to the company filter; the conflict filter.

### 6. Tests and checks

Group and company resolution (company overrides group; unassigned); qualify logic (several
families, keywords in title vs bullets, empty statement); cap vs hide; TPM family choice; the
model learns a segment-dependent pattern on synthetic data, and a model saved before this
change is detected as stale and retrained; profile validation (a company in two groups is
rejected); the browser smoke test edits a group and a rule; a before/after table of the owner's
top 30 and of scores at Tech companies.

## Order of work

1. Profile sections + validation + resolution helper + Settings page (no scoring effect yet).
2. `G_tpm` family; interest rules in scope/scoring; job page reason; counts.
3. Model features, staleness on grouping changes, retrain; before/after report.
4. Conflict filter and group filter in the tracker.

No database migration (everything lives in the profile JSON). Python changes: restart
`jobagent-ui.service` at the end. Commits only when asked.

## Not in this plan

Soft per-group weights (a rule is qualify / cap / hide; nuance comes from the learned part);
an LLM deciding the group of a new company; group-level pay floors or seniority rules.
