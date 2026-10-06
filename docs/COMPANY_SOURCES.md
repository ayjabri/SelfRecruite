# Companies reachable through the current sources

Checked on October 6, 2026 by calling each site's public API. Posting counts are worldwide, before the US-only and title filters, and change daily. Workday reports at most 2,000.

Three sources exist today: **Greenhouse** job boards, **Workday** career sites and **Oracle Recruiting Cloud** career sites. A company is only reachable if it uses one of them; the last section lists well-known names that do not.

To add a company, copy its line into the matching section of `config/companies.yaml`.

## Already configured

| Company | Source | Line in companies.yaml |
|---|---|---|
| Anthropic | Greenhouse | `- anthropic` |
| NVIDIA | Workday | `- {company: NVIDIA, site: "nvidia:wd5:NVIDIAExternalCareerSite"}` |
| Citi | Workday | `- {company: Citi, site: "citi:wd5:2"}` |
| Capital One | Workday | `- {company: Capital One, site: "capitalone:wd12:Capital_One"}` |
| JPMorgan Chase | Oracle | `- {company: JPMorgan Chase, host: jpmc.fa.oraclecloud.com, site_number: CX_1001}` |
| BNY | Oracle | `- {company: BNY, host: eofe.fa.us2.oraclecloud.com, site_number: BNY-Careers}` |
| Goldman Sachs | Oracle | `- {company: Goldman Sachs, host: hdpc.fa.us2.oraclecloud.com, site_number: LateralHiring}` |
| Citizens Bank | Oracle | `- {company: Citizens Bank, host: hcgn.fa.us2.oraclecloud.com, site_number: CX_1}` |

## Workday (verified)

Add under `workday:` as `- {company: <name>, site: "<site key>"}`.

### Banks

| Company | Site key | Postings |
|---|---|---|
| Ally | `ally:wd1:Ally` | 91 |
| Bank of America | `ghr:wd1:Lateral-US` | 1,969 |
| Deutsche Bank | `db:wd3:DBWebsite` | 1,164 |
| Fannie Mae | `fanniemae:wd1:FannieMaeCareers` | 53 |
| Morgan Stanley | `ms:wd5:External` | 1,347 |
| PNC | `pnc:wd5:External` | 2,000 |
| Santander US | `santander:wd3:SantanderCareers` | 929 |
| Mizuho (Americas) | `mizuho:wd1:mizuhoamericas` | 118 |
| TD Bank | `td:wd3:TD_Bank_Careers` | 1,517 |
| Truist | `truist:wd1:Careers` | 1,110 |
| U.S. Bank | `usbank:wd1:US_Bank_Careers` | 1,185 |

### Asset management and custody

| Company | Site key | Postings |
|---|---|---|
| AIG | `aig:wd1:AIG` | 474 |
| BlackRock | `blackrock:wd1:BlackRock_Professional` | 308 |
| Blackstone | `blackstone:wd1:Blackstone_Careers` | 172 |
| Fidelity | `fmr:wd1:FidelityCareers` | 772 |
| Northern Trust | `ntrs:wd1:NorthernTrust` | 601 |
| Prudential | `pru:wd5:Careers` | 165 |
| State Street | `statestreet:wd1:Global` | 1,077 |
| T. Rowe Price | `troweprice:wd5:TRowePrice` | 143 |
| Vanguard | `vanguard:wd5:vanguard_external` | 364 |

### Payments, market infrastructure and data

| Company | Site key | Postings |
|---|---|---|
| Broadridge | `broadridge:wd5:Careers` | 291 |
| CME Group | `cmegroup:wd1:CME_Careers` | 59 |
| FIS | `fis:wd5:SearchJobs` | 509 |
| Fiserv | `fiserv:wd5:EXT` | 290 |
| Mastercard | `mastercard:wd1:CorporateCareers` | 1,048 |
| Nasdaq | `nasdaq:wd1:Global_External_Site` | 181 |
| S&P Global | `spgi:wd5:SPGI_Careers` | 273 |

### Technology

| Company | Site key | Postings |
|---|---|---|
| Adobe | `adobe:wd5:external_experienced` | 469 |
| Salesforce | `salesforce:wd12:External_Career_Site` | 1,511 |
| Workday | `workday:wd5:Workday` | 382 |

## Oracle Recruiting Cloud (verified)

Add under `oracle:` as `- {company: <name>, host: <host>, site_number: <site>}`.

| Company | Host | Site number | Postings |
|---|---|---|---|
| JPMorgan Chase | jpmc.fa.oraclecloud.com | CX_1001 | 7,300 |
| BNY | eofe.fa.us2.oraclecloud.com | BNY-Careers | 1,365 |
| Goldman Sachs | hdpc.fa.us2.oraclecloud.com | LateralHiring | 954 |
| Citizens Bank | hcgn.fa.us2.oraclecloud.com | CX_1 | 402 |
| Computershare | fa-evdq-saasfaprod1.fa.ocs.oraclecloud.com | computersharecareers | 91 (mostly outside the US) |
| Jefferies | careers.jefferies.com | CX_1 | unverified: the site was returning Cloudflare error 522 when checked |

Oracle hosts are random-looking codes, so they cannot be guessed; these came from web searches. Finding more means searching for a company name together with `oraclecloud.com/hcmUI/CandidateExperience`.

## Greenhouse (verified)

Add under `greenhouse:` as `- <slug>`.

### Banks and payments fintech

| Company | Slug | Postings |
|---|---|---|
| Adyen | `adyen` | 227 |
| Affirm | `affirm` | 184 |
| Alloy | `alloy` | 24 |
| BILL | `billcom` | 44 |
| Betterment | `betterment` | 29 |
| Block (Square, Cash App) | `block` | 230 |
| Blockchain.com | `blockchain` | 38 |
| Brex | `brex` | 284 |
| Carta | `carta` | 74 |
| Chime | `chime` | 65 |
| Coinbase | `coinbase` | 226 |
| Fireblocks | `fireblocks` | 28 |
| Gusto | `gusto` | 95 |
| Lithic | `lithic` | 11 |
| Melio | `melio` | 21 |
| Mercury | `mercury` | 64 |
| Monzo | `monzo` | 68 |
| N26 | `n26` | 44 |
| Oportun | `oportun` | 37 |
| Payoneer | `payoneer` | 102 |
| Robinhood | `robinhood` | 156 |
| SoFi | `sofi` | 57 |
| Stripe | `stripe` | 719 |
| Toast | `toast` | 324 |
| Upgrade | `upgrade` | 31 |
| Upstart | `upstart` | 91 |
| Wise | `wise` | 15 |

### Trading firms and hedge funds

| Company | Slug | Postings |
|---|---|---|
| AQR Capital | `aqr` | 53 |
| Akuna Capital | `akunacapital` | 41 |
| DRW | `drweng` | 171 |
| ExodusPoint | `exoduspoint` | 2 |
| Flow Traders | `flowtraders` | 45 |
| Hudson River Trading | `wehrtyou` | 87 |
| IMC Trading | `imc` | 170 |
| Jane Street | `janestreet` | 231 |
| Jump Trading | `jumptrading` | 109 |
| Optiver (US) | `optiverus` | 164 |
| Point72 | `point72` | 214 |
| Schonfeld | `schonfeld` | 68 |
| Squarepoint Capital | `squarepointcapital` | 88 |
| Tower Research Capital | `towerresearchcapital` | 93 |
| Virtu Financial | `virtu` | 48 |

### Technology

| Company | Slug | Postings |
|---|---|---|
| Airbnb | `airbnb` | 151 |
| Anthropic | `anthropic` | 643 |
| Cloudflare | `cloudflare` | 416 |
| Databricks | `databricks` | 885 |
| Datadog | `datadog` | 432 |
| Discord | `discord` | 51 |
| Dropbox | `dropbox` | 40 |
| Elastic | `elastic` | 399 |
| Figma | `figma` | 161 |
| GitLab | `gitlab` | 210 |
| Instacart | `instacart` | 122 |
| Lyft | `lyft` | 189 |
| MongoDB | `mongodb` | 394 |
| Oscar Health | `oscar` | 255 |
| Pinterest | `pinterest` | 174 |
| Reddit | `reddit` | 151 |
| Scale AI | `scaleai` | 188 |
| Twilio | `twilio` | 132 |
| Zocdoc | `zocdoc` | 60 |
| xAI | `xai` | 302 |

## Checked and not reachable with the current sources

| Company | What they use | Note |
|---|---|---|
| Amazon | amazon.jobs (own site) | Has a public JSON search; needs a new adapter |
| Netflix | Eightfold (explore.jobs.netflix.net) | Has a public JSON listing; needs a new adapter |
| Meta | metacareers.com (own site) | No open endpoint found |
| Wells Fargo | Workday on myworkdaysite.com | Different host pattern; the Workday tool would need a small change |
| American Express | Eightfold | Needs a new adapter |
| Visa | SmartRecruiters | In the build plan; not built yet |
| Charles Schwab | iCIMS | Not supported; JobSpy would catch it |
| Barclays, HSBC, UBS | Own sites or other systems | Not on the three sources under the names tried |
| Bloomberg, Two Sigma, Citadel, D. E. Shaw | Own sites | Jane Street and Point72 are on Greenhouse (above) |
| KKR, Apollo, Carlyle, Moody's, MetLife, Travelers, Chubb, Discover, Synchrony, Intuit, Snowflake, Dell | Not found on Workday under the names tried | May use Workday with a different tenant or site name, or another system |
| Regional banks (Fifth Third, KeyBank, Huntington, M&T, Regions, Comerica, Zions, Webster, Valley, Flagstar) | Not found on Workday under the names tried | Same |
| Insurers (New York Life, MassMutual, Guardian, Equitable, Voya, Lincoln, Allstate, Progressive, Liberty Mutual, Principal) | Not found on Workday under the names tried | Same |

"Not found under the names tried" means my guess at the tenant and site name failed, not that the company is certainly elsewhere. A company's real Workday address shows in the URL of its careers page: `https://<tenant>.<wdN>.myworkdayjobs.com/<site>` gives the key `tenant:wdN:site`.

## Sources not built yet

The build plan also lists Lever, Ashby, SmartRecruiters and JobSpy (Indeed, LinkedIn, Glassdoor). Each would be one tools file and one agent file. JobSpy covers employers on iCIMS, Taleo and SuccessFactors, and companies with their own sites such as Meta, but scraping those boards may breach their terms.
