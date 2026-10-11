"""Drive the page in a headless browser as a throwaway member: filters, detail, settings pages.
Prints console errors and failed requests. Needs a server on BASE (default a test server on 8766)
and playwright's chromium (`playwright install chromium`; on this host run with
LD_LIBRARY_PATH=<conda env>/lib for libasound).

    python scripts/ui_smoke.py [BASE]
"""
import json
import sys

from playwright.sync_api import sync_playwright
from sqlalchemy import text

from jobagent.db import session

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"
EMAIL, PW = "ui-smoke@example.com", "a strong password 1"


def main() -> int:
    errors, failed = [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(ignore_https_errors=True)
        page = ctx.new_page()
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("response", lambda r: failed.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        r = ctx.request.post(BASE + "/api/auth/register", data=json.dumps({"email": EMAIL, "password": PW, "name": "UI smoke"}),
                             headers={"Content-Type": "application/json"})
        uid = r.json()["id"]
        with session() as s, s.begin():  # borrow user 1's scope and scores so the list is not empty
            s.execute(text(f"insert into user_jobs (user_id, job_id, in_scope, fit_score, fit_details, user_label) "
                           f"select {uid}, job_id, in_scope, fit_score, fit_details, user_label from user_jobs where user_id=1"))
            s.execute(text(f"insert into resumes (user_id, filename, text, sha, active) select {uid}, filename, text, sha, true "
                           f"from resumes where user_id=1 and active limit 1"))
        try:
            page.goto(BASE + "/")
            page.wait_for_selector(".row", timeout=15000)
            count = lambda: page.locator("#list .count").inner_text()  # noqa: E731
            checks = []
            base = count()
            page.select_option("#label", "interested"); page.wait_for_timeout(700); checks.append(("label", base, count()))
            page.select_option("#label", "all"); page.fill("#q", "treasury"); page.wait_for_timeout(700); checks.append(("search", base, count()))
            page.fill("#q", ""); page.fill("#min_score", "80"); page.wait_for_timeout(700); checks.append(("min score", base, count()))
            page.fill("#min_score", "0"); page.select_option("#posted", "7d"); page.wait_for_timeout(700); checks.append(("posted", base, count()))
            page.select_option("#posted", "any"); page.wait_for_timeout(700)
            for name, before, after in checks:
                print(f"{'ok ' if before != after else 'BAD'} filter {name}: {before!r} -> {after!r}")
                if before == after:
                    errors.append(f"filter {name} did not change the list")
            page.click("#list .row >> nth=0"); page.wait_for_timeout(1500)
            print("ok  detail:", page.locator("#detail h2").first.inner_text()[:50])
            page.click('button[data-tab="settings"]'); page.wait_for_timeout(800)
            for name in ("scope", "targets", "groups", "skills", "llm", "resume", "account"):
                page.click(f'#cfgnav button[data-cfg="{name}"]'); page.wait_for_timeout(700)
            print("ok  settings pages:", page.locator("#cfgnav").inner_text()[:80].replace("\n", " "))
            # company groups: a new profile starts with the suggested groups; state an interest and save
            page.click('#cfgnav button[data-cfg="groups"]'); page.wait_for_selector(".gcard", timeout=10000)
            tech = page.locator('.gcard[data-kind="group"]', has=page.locator('input.g-name[value="Tech"]'))
            tech.locator('input[data-fam="G_tpm"]').check()
            for _ in range(20):
                if "capped" in tech.locator(".g-counts").inner_text():
                    break
                page.wait_for_timeout(500)
            else:
                errors.append("the groups preview did not show capped jobs for Tech")
            print("ok  groups preview:", tech.locator(".g-counts").inner_text()[:110])
            page.select_option("#g-addown", "Citi"); page.wait_for_selector('.gcard[data-kind="company"]')
            page.fill('.gcard[data-kind="company"] .g-kw', "alm, liquidity"); page.wait_for_timeout(1200)
            page.click("#cfg-save"); page.wait_for_selector("#cfg-after", timeout=10000)
            with session() as s:
                saved = s.execute(text("select targets from profiles where user_id = :u"), {"u": uid}).scalar()
            ok = saved["company_groups"]["Tech"]["interest"].get("families") == ["G_tpm"] and saved["company_interest"]["Citi"]["keywords"] == ["alm", "liquidity"]
            print(("ok " if ok else "BAD"), "groups saved:", json.dumps(saved["company_interest"]))
            if not ok:
                errors.append("the groups page did not save what was entered")
            page.click('button[data-tab="jobs"]'); page.wait_for_selector("#group:not([hidden])", timeout=10000)
            before = count(); page.select_option("#group", "Tech"); page.wait_for_timeout(900)
            print(f"{'ok ' if before != count() else 'BAD'} filter group: {before!r} -> {count()!r}")
            if before == count():
                errors.append("the group filter did not change the list")
            page.select_option("#group", ""); page.select_option("#label", "conflicts"); page.wait_for_timeout(1200)
            print("ok  conflicts filter:", count()); page.select_option("#label", "all"); page.wait_for_timeout(700)
            page.click('button[data-tab="run"]'); page.wait_for_selector("#pref-train", timeout=10000)
            print("ok  preference card:", page.locator("#pref-body").inner_text()[:90].replace("\n", " "))
            page.click("#pref-train")   # a member may retrain their own model
            for _ in range(120):   # the page's CSP forbids eval, so poll from here instead of wait_for_function
                if "AUC" in page.locator("#pref-body").inner_text():
                    break
                page.wait_for_timeout(1000)
            else:
                errors.append("the preference model did not finish training in 2 minutes")
            print("ok  trained:", page.locator("#pref-body").inner_text()[:160].replace("\n", " "))
            # score check: mark a job (its score at that moment is recorded), then read the report
            page.click('button[data-tab="jobs"]'); page.select_option("#label", "unlabelled"); page.wait_for_timeout(900)
            page.click("#list .row >> nth=0"); page.wait_for_timeout(900)
            page.click('#detail button[data-label="not_interested"]'); page.wait_for_timeout(900)
            with session() as s:
                recorded = s.execute(text("select count(*) from user_jobs where user_id = :u and label_score is not null"), {"u": uid}).scalar()
            print(("ok " if recorded == 1 else "BAD"), "score recorded with the mark:", recorded)
            if recorded != 1:
                errors.append("marking a job did not record its score")
            page.select_option("#label", "all")
            page.click('button[data-tab="check"]'); page.wait_for_selector("#check .checknums", timeout=15000)
            body = page.locator("#check").inner_text()
            for needle in ("held-out AUC", "By score band", "Interested, but scored below", "By your company groups", "Forward test", "At each training"):
                if needle.lower() not in body.lower():
                    errors.append(f"score check is missing: {needle}")
            print("ok  score check:", " ".join(page.locator("#check .checknums").first.inner_text().split())[:150])
            if len(sys.argv) > 2:
                page.set_viewport_size({"width": 1400, "height": 1000}); page.screenshot(path=sys.argv[2], full_page=True)
            page.locator("#check a[data-check-job]").first.click(); page.wait_for_timeout(1200)
            print("ok  opened from score check:", page.locator("#detail h2").first.inner_text()[:50])
        finally:
            ctx.request.post(BASE + "/api/auth/delete", data=json.dumps({"password": PW}),
                             headers={"Content-Type": "application/json", "X-CSRF-Token": page.evaluate("me && me.csrf") or ""})
            b.close()
    with session() as s, s.begin():
        s.execute(text("delete from users where email = :e"), {"e": EMAIL})
    print("ERRORS:\n  " + "\n  ".join(errors) if errors else "no console errors")
    print("FAILED:\n  " + "\n  ".join(failed) if failed else "no failed requests")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
