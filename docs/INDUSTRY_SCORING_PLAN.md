# Plan: role preferences that depend on the industry

Date: Oct 10, 2026. Status: **awaiting approval.**

The request: at a bank or fintech, applied AI/ML engineering and pure technical roles are
welcome; at technology companies (NVIDIA, Anthropic) only technical program manager roles are.
The fast score should understand that the same title can be wanted at one kind of company and
not at another.

## Why the score cannot do this today

- **No notion of industry.** A company is just a name. The rule score has a `domain` part
  (10% of the score) that says "banking or fintech: yes/no"; nothing says "technology company".
- **Role families do not depend on the company.** `D_ai_ml` has weight 0.9 everywhere.
  There is no TPM family: "technical program manager" falls in `A_product_delivery_ba` with
  product managers and business analysts.
- **The learned model is additive.** It is a logistic regression over title words, company,
  seniority, family, the rule parts and the embedding. It can learn "NVIDIA: lower" and
  "machine learning: higher" separately, but not "machine learning *at a tech company*: lower".
  It copes at NVIDIA and Anthropic only because it has many marks there and learned those two
  company names; it has nothing to carry over to a tech company it has no marks for.

Measured on the owner's 285 marks (read-only experiment, Oct 10):

| Model | 5-fold AUC | AUC on a company it has no marks for | NVIDIA unseen | Anthropic unseen |
|---|---|---|---|---|
| Current | 0.79 | 0.61 | 0.43 | 0.39 |
| + industry | 0.79 | 0.63 | 0.43 | 0.40 |
| + industry × role family (TPM split out) | **0.82** | **0.70** | **0.72** | 0.53 |

"Unseen" = trained on every other company's marks, tested on that company: what happens when a
new employer is added. Today the model is worse than a coin flip on an unseen tech company,
because it applies bank taste to it. The cross feature fixes most of that.

## What changes

### 1. Every company gets an industry (shared, admin-edited)

A new `industries:` section in `config/companies.yaml`, edited in Settings → Companies:

```yaml
industries:   # used by scoring; a company not listed is "other"
  bank:    [JPMorgan Chase, Citi, BNY, Goldman Sachs, Citizens Bank, Capital One, Bank Of America,
            U.S. Bank, MUFG, Barclays]
  fintech: [Stripe, Mastercard, Visa]
  tech:    [NVIDIA, Anthropic, Databricks]
```

Matched on the normalised company name, so it also covers companies no longer crawled. A helper
`company_industry(name)` is the single place that reads it. The Admin tab lists companies with
no industry so a new one is not forgotten.

### 2. A TPM role family

New default family in `targets.yaml` and in the owner's profile:
`G_tpm: {weight: 1.0, keywords: [technical program manager, technical project manager, tpm]}`.
It outranks `A_product_delivery_ba` (0.85) for those titles.

### 3. Per-user rule: "at these companies, only these roles" (optional, off by default)

New profile section under Scoring, one row per industry:

```yaml
industry_roles:
  tech: {only: [G_tpm], otherwise: cap}     # cap | hide
```

- A job at a company of that industry whose title matches **none** of the listed families is
  capped at 30, like the existing gates, with the reason shown on the job
  ("not a role you target at tech companies"). `hide` instead puts it out of scope.
- Checked against every family the title matches, not only the best one.
- Industries with no row (bank, fintech) are unrestricted, as today.
- Applies over the learned score too: an explicit rule beats the model.

### 4. The learned model sees industry and industry × role

`PreferenceModel` gains two categorical features: the industry, and `industry|title_family`.
Saved models carry a feature version; an older saved model is retrained at the next scoring run
(the retrain-when-stale stage already exists). Users with too few marks are unaffected.

### 5. Tests and checks

`company_industry` matching; the rule (cap, hide, title matching several families, industry with
no rule); family choice for TPM titles; the model with the new features learns an
industry-dependent pattern on synthetic data and still loads/retrains an old blob; Settings
editors for both sections in the browser smoke test; before/after table of the owner's top 30.

## A decision the owner needs to make first

The stated rule and the existing marks disagree at Anthropic. Interested marks at tech companies:

| Company | Interested | of which TPM / technical project manager | Others marked interested |
|---|---|---|---|
| NVIDIA | 12 | 8 | business systems analyst, treasury accountant, 2 ML/software engineers |
| Anthropic | 26 | 0 | 13 product/program managers and analysts, 10 data scientists / research engineers, 3 others |
| Databricks | 6 | 1 | AI transformation leads, data scientist, applied ML engineer |

All 26 Anthropic "interested" marks date from Oct 5, the first day of marking. Three ways forward:

- **A. The rule as stated: tech = TPM only.** Then those 35 older "interested" marks contradict
  it and should be cleared or flipped (a one-click "review marks that break my rules" list),
  otherwise they keep teaching the model the opposite.
- **B. A wider tech list:** TPM plus product/program management (`G_tpm`, `A_product_delivery_ba`,
  `B_ai_product`); engineering and data science at tech companies capped.
- **C. No hard rule; only parts 1, 2 and 4.** The model learns industry × role from the marks
  as they are, so Anthropic stays broad until the marks change.

Recommended: build all four parts, and start the owner's profile with **A or B** as chosen.

## Order of work

1. Industries section + helper + Settings editor + Admin "no industry" list.
2. `G_tpm` family; `industry_roles` rule in scoring; Settings editor.
3. Model features + feature version + retrain; before/after report.
4. (If A or B) the "marks that break my rules" review list.

No database migration. Python changes: restart `jobagent-ui.service` at the end. Commits only
when asked.
