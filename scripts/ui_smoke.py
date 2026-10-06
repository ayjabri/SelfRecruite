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
            for name in ("scope", "targets", "skills", "llm", "resume", "account"):
                page.click(f'#cfgnav button[data-cfg="{name}"]'); page.wait_for_timeout(700)
            print("ok  settings pages:", page.locator("#cfgnav").inner_text()[:80].replace("\n", " "))
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
