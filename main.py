"""
SLCM Feedback Automation Bot — MUJ (Manipal University Jaipur)
================================================================
Portal: https://mujslcm.jaipur.manipal.edu
Feedback list:  /Student/Survey/FeedbackList
Individual form: URL from href on each .btn-clean element

DOM facts (verified from live portal):
  • Feedback list: each pending form is an <a class="btn-clean"> element
      - .text_content()  → "Pending" / "Submitted" / etc.
      - .get_attribute("href") → direct URL to the individual feedback form
  • Individual form (NO iframe — main DOM only):
      - Radio questions : input[type="radio"][class="yescheck"]
      - Dropdown questions: <select> elements  (positive value = "Y")
      - Comment textarea : <textarea>  (one per form)
      - Submit button    : button#btnSubmit

Usage:
    python main.py [--dry-run] [--config path/to/config.json]

Flags:
    --dry-run     Fill every form but never click Submit.
    --config      Path to config file (default: config.json next to this script).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, datefmt="%H:%M:%S")
log = logging.getLogger("slcm-bot")

# ---------------------------------------------------------------------------
# Portal constants (MUJ SLCM — change here if the portal is updated)
# ---------------------------------------------------------------------------

PORTAL_BASE        = "https://mujslcm.jaipur.manipal.edu"
LOGIN_URL          = PORTAL_BASE + "/"
FEEDBACK_LIST_URL  = PORTAL_BASE + "/Student/Survey/FeedbackList"

# Selectors — kept as constants so they're easy to fix in one place
SEL_PENDING_LINKS  = "a.btn-clean"          # all feedback links on the list page
SEL_RADIO_YES      = "input[type='radio'][class='yescheck']"  # radio buttons in form
SEL_SELECTS        = "select"               # dropdown selects in form
SEL_TEXTAREA       = "textarea"             # comment box
SEL_SUBMIT         = "button#btnSubmit"     # submit button

# Positive dropdown value (what "yes / strongly agree / excellent" maps to)
SELECT_POSITIVE_VALUE = "Y"

# Post-login URL indicators (after a successful sign-in, URL no longer ends with "/")
POST_LOGIN_PATH_PATTERNS = ["/Student/", "/Home/", "/Dashboard/", "/student/", "/home/"]

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)

    def strip_comments(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: strip_comments(v) for k, v in obj.items() if not k.startswith("_comment")}
        return obj

    return strip_comments(raw)


def resolve_rating(config: dict, form_label: str) -> str:
    """
    Return the rating value to use for radio/select elements.
    For MUJ SLCM the radio class is 'yescheck' (just click it — no numeric value needed),
    and for selects the positive value is always "Y".
    The numeric rating from config drives WHICH radio index to pick on
    forms that have a scale (e.g. 1-5), but defaults to last/highest.
    """
    return str(config.get("default_rating", 4))


def resolve_comment(config: dict, form_label: str) -> str:
    label_lower = form_label.lower()
    for key, comment in config.get("comments", {}).items():
        if key.lower() in label_lower:
            return str(comment)
    return str(config.get("default_comment", ""))

# ---------------------------------------------------------------------------
# Human-like delay
# ---------------------------------------------------------------------------

def human_delay(config: dict) -> None:
    bounds = config.get("delay_between_actions_ms", [700, 1400])
    if isinstance(bounds, list) and len(bounds) == 2:
        lo, hi = int(bounds[0]), int(bounds[1])
    else:
        lo = hi = int(bounds)
    ms = random.randint(lo, hi)
    time.sleep(ms / 1000)

# ---------------------------------------------------------------------------
# Screenshot helper
# ---------------------------------------------------------------------------

def screenshot(page: Page, logs_dir: Path, label: str) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^\w\-]", "_", label)[:60]
    path = logs_dir / f"{ts}_{safe}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        log.info("  📸 Screenshot → %s", path.name)
    except PlaywrightError as e:
        log.warning("  Screenshot failed: %s", e)

# ---------------------------------------------------------------------------
# Step 1 — Login wait
# ---------------------------------------------------------------------------

def wait_for_login(page: Page, config: dict) -> None:
    """
    Open the SLCM login page, then pause until the dashboard is detected.
    The user must type their credentials manually.
    No credentials are ever touched by this script.
    """
    timeout_s: int = config.get("login_wait_timeout_seconds", 300)

    log.info("Opening: %s", LOGIN_URL)
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    log.info("=" * 62)
    log.info("ACTION REQUIRED ▶  Log in to the MUJ SLCM portal in the")
    log.info("  browser window that just opened.")
    log.info("  (Enter username + password, handle any OTP if prompted.)")
    log.info("  The script will continue automatically once you're logged in.")
    log.info("  Waiting up to %d seconds…", timeout_s)
    log.info("=" * 62)

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            current_url = page.url
            # After login, the URL will no longer be the root login page
            if any(pat in current_url for pat in POST_LOGIN_PATH_PATTERNS):
                log.info("✓ Login detected  (landed on: %s)", current_url)
                return
            # Also check if any menu / logout link is now visible
            page_text = page.inner_text("body").lower()
            if "logout" in page_text or "sign out" in page_text:
                log.info("✓ Login detected  (logout link visible).")
                return
        except PlaywrightError:
            pass
        time.sleep(2)

    raise TimeoutError(
        f"Timed out after {timeout_s}s waiting for login. "
        "Increase 'login_wait_timeout_seconds' in config.json if needed."
    )

# ---------------------------------------------------------------------------
# Step 2 — Navigate to FeedbackList
# ---------------------------------------------------------------------------

def navigate_to_feedback_list(page: Page, config: dict) -> None:
    """Direct navigation — the URL is known and stable."""
    log.info("Navigating to feedback list: %s", FEEDBACK_LIST_URL)
    human_delay(config)
    page.goto(FEEDBACK_LIST_URL, wait_until="networkidle", timeout=30_000)

    # Sanity check: if we got bounced back to login, raise
    if page.url.rstrip("/") == LOGIN_URL.rstrip("/"):
        raise RuntimeError(
            "Redirected back to login after navigating to FeedbackList. "
            "Your session may have expired — please re-run the script."
        )

    log.info("✓ On feedback list page.")

# ---------------------------------------------------------------------------
# Step 3 — Collect pending forms
# ---------------------------------------------------------------------------

def collect_pending_forms(page: Page, config: dict) -> list[dict]:
    """
    Find all <a class="btn-clean"> elements on the FeedbackList page.
    Returns only those whose text contains "Pending" (case-insensitive).
    Each entry: {label, url}
    """
    human_delay(config)

    try:
        page.wait_for_selector(SEL_PENDING_LINKS, timeout=15_000)
    except PlaywrightTimeout:
        log.warning(
            "No '%s' elements found on page. "
            "The portal may have changed its markup.", SEL_PENDING_LINKS
        )
        return []

    all_links = page.locator(SEL_PENDING_LINKS).all()
    log.info("Found %d btn-clean link(s) on listing page.", len(all_links))

    forms: list[dict] = []
    for link in all_links:
        try:
            text  = (link.inner_text() or "").strip()
            href  = link.get_attribute("href") or ""
            # Skip if already submitted
            if re.search(r"submitted|filled|completed|done", text, re.IGNORECASE):
                log.info("  ↳ Skipping (already submitted): %s  [%s]", text, href)
                continue
            # Only keep pending / open ones
            # If text is blank or "Pending" or "Fill" — keep it
            if href:
                # Make absolute if relative
                if href.startswith("/"):
                    href = PORTAL_BASE + href
                # Try to get a label from surrounding context (parent row / sibling text)
                try:
                    parent_text = link.locator("xpath=..").inner_text()
                    label = parent_text.strip().split("\n")[0][:80]
                except PlaywrightError:
                    label = text or href
                forms.append({"label": label, "url": href, "status_text": text})
                log.info("  ↳ Pending form: %s", label)
        except PlaywrightError as e:
            log.warning("  Error reading btn-clean element: %s", e)
            continue

    log.info("Total pending forms to fill: %d", len(forms))
    return forms

# ---------------------------------------------------------------------------
# Step 4 — Fill a single feedback form
# ---------------------------------------------------------------------------

def fill_form(
    page: Page,
    form_info: dict,
    config: dict,
    dry_run: bool,
    logs_dir: Path,
) -> dict[str, Any]:
    """
    Navigate to a single feedback form and fill it.
    Returns a result dict.
    """
    label    = form_info["label"]
    form_url = form_info["url"]
    result: dict[str, Any] = {
        "label":            label,
        "url":              form_url,
        "status":           "pending",
        "radios_filled":    0,
        "selects_filled":   0,
        "comment_filled":   False,
        "error":            None,
    }

    try:
        log.info("  Opening form: %s", form_url)
        page.goto(form_url, wait_until="networkidle", timeout=30_000)
        human_delay(config)

        # Session-timeout guard
        if page.url.rstrip("/") == LOGIN_URL.rstrip("/"):
            raise RuntimeError("Session expired — redirected to login mid-run.")

        comment = resolve_comment(config, label)

        # ── Radio buttons ──────────────────────────────────────────────────
        # The portal uses input[type="radio"][class="yescheck"]
        # These are binary Yes/No or agree/strongly-agree radios.
        # Strategy: click every yescheck radio (they represent the positive/yes answer).
        # If a group has multiple radios (e.g. 1–5 scale stored as radios),
        # we use the numeric rating from config to pick the right one by index.

        radios = page.locator(SEL_RADIO_YES).all()
        log.info("  Radios (yescheck): %d found", len(radios))

        # Group radios by their 'name' attribute so we handle multi-radio groups correctly
        radio_groups: dict[str, list] = {}
        for r in radios:
            name = r.get_attribute("name") or "__unnamed__"
            radio_groups.setdefault(name, []).append(r)

        numeric_rating = int(config.get("default_rating", 4))

        for grp_name, group in radio_groups.items():
            try:
                if len(group) == 1:
                    # Binary yes/no — just click it
                    group[0].check()
                else:
                    # Scale radio group — pick by index based on rating
                    idx = max(0, min(numeric_rating - 1, len(group) - 1))
                    group[idx].check()
                result["radios_filled"] += 1
                human_delay(config)
            except PlaywrightError as e:
                log.warning("  Radio group '%s' error: %s", grp_name, e)

        # ── Dropdowns ──────────────────────────────────────────────────────
        # The portal uses <select> elements; positive value is "Y"
        selects = page.locator(SEL_SELECTS).all()
        log.info("  Selects: %d found", len(selects))

        for sel in selects:
            try:
                # Try "Y" first (confirmed portal value)
                options = sel.locator("option").all()
                option_values = [o.get_attribute("value") or "" for o in options]

                if "Y" in option_values:
                    sel.select_option(value="Y")
                    result["selects_filled"] += 1
                elif option_values:
                    # Fallback: pick the last non-empty option (highest rating)
                    non_blank = [v for v in option_values if v]
                    if non_blank:
                        sel.select_option(value=non_blank[-1])
                        result["selects_filled"] += 1
                human_delay(config)
            except PlaywrightError as e:
                log.warning("  Select error: %s", e)

        # ── Textarea (comment) ─────────────────────────────────────────────
        textarea = page.locator(SEL_TEXTAREA)
        if textarea.count() > 0 and comment:
            try:
                textarea.first.click()
                textarea.first.fill(comment)
                result["comment_filled"] = True
                log.info("  Comment filled.")
                human_delay(config)
            except PlaywrightError as e:
                log.warning("  Textarea error: %s", e)

        # ── Summary ────────────────────────────────────────────────────────
        print("\n" + "─" * 62)
        print(f"FORM   : {label}")
        print(f"URL    : {form_url}")
        print(f"Rating : {numeric_rating}  |  Comment: {comment or '(none)'}")
        print(f"Radios filled  : {result['radios_filled']} group(s)")
        print(f"Selects filled : {result['selects_filled']}")
        print(f"Comment filled : {result['comment_filled']}")
        print("─" * 62)

        # ── Screenshot ─────────────────────────────────────────────────────
        if config.get("screenshot_on_submit", True):
            screenshot(page, logs_dir, f"pre_submit_{label}")

        # ── Submit (or dry-run skip) ───────────────────────────────────────
        if dry_run:
            log.info("  [DRY RUN] Not submitting: %s", label)
            result["status"] = "dry_run"
            return result

        if config.get("confirm_before_submit", True):
            try:
                input(f"\n  ▶ Press Enter to SUBMIT '{label}'  (Ctrl+C to skip)… ")
            except KeyboardInterrupt:
                log.info("  Skipped by user: %s", label)
                result["status"] = "skipped"
                return result

        # Click submit
        submit_btn = page.locator(SEL_SUBMIT)
        if submit_btn.count() == 0:
            # Fallback: any button with text "Submit"
            submit_btn = page.get_by_role("button", name=re.compile("submit", re.IGNORECASE))

        if submit_btn.count() > 0:
            submit_btn.first.click()
            page.wait_for_load_state("networkidle", timeout=20_000)
            log.info("  ✓ Submitted: %s", label)
            result["status"] = "submitted"
            if config.get("screenshot_on_submit", True):
                screenshot(page, logs_dir, f"post_submit_{label}")
        else:
            log.error("  ✗ Submit button (button#btnSubmit) not found on page!")
            result["status"] = "submit_failed"

    except PlaywrightTimeout as e:
        log.error("  Timeout on '%s': %s", label, e)
        result["status"] = "error"
        result["error"] = str(e)
    except RuntimeError as e:
        log.error("  Runtime error on '%s': %s", label, e)
        result["status"] = "error"
        result["error"] = str(e)
        raise  # re-raise so the orchestrator can stop
    except Exception as e:  # noqa: BLE001
        log.error("  Unexpected error on '%s': %s", label, e)
        result["status"] = "error"
        result["error"] = str(e)

    return result

# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run(config: dict, dry_run: bool) -> None:
    logs_dir = Path(config.get("logs_dir", "logs"))
    logs_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    with sync_playwright() as pw:
        browser: Browser = pw.chromium.launch(
            headless=config.get("headless", False),
            slow_mo=150,
        )
        context: BrowserContext = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page: Page = context.new_page()

        # ── Step 1: wait for manual login ──────────────────────────────────
        try:
            wait_for_login(page, config)
        except TimeoutError as e:
            log.error(str(e))
            browser.close()
            sys.exit(1)

        # ── Step 2: go to FeedbackList ─────────────────────────────────────
        try:
            navigate_to_feedback_list(page, config)
        except (PlaywrightError, RuntimeError) as e:
            log.error("Could not load feedback list: %s", e)
            browser.close()
            sys.exit(1)

        # ── Step 3: collect pending forms ──────────────────────────────────
        pending = collect_pending_forms(page, config)
        max_forms: int = config.get("max_forms", 50)
        pending = pending[:max_forms]

        if not pending:
            log.info("🎉 No pending feedback forms found — all done!")
            browser.close()
            return

        log.info("=" * 62)
        log.info("Starting to fill %d pending form(s)…", len(pending))
        if dry_run:
            log.info("[DRY RUN] — forms will be filled but NOT submitted.")
        log.info("=" * 62)

        # ── Step 4: fill each form ─────────────────────────────────────────
        for idx, form_info in enumerate(pending):
            log.info("\n[%d/%d] %s", idx + 1, len(pending), form_info["label"])
            try:
                result = fill_form(page, form_info, config, dry_run, logs_dir)
                results.append(result)
            except RuntimeError:
                # Session expired mid-run — stop gracefully
                results.append({
                    "label": form_info["label"],
                    "status": "error",
                    "radios_filled": 0,
                    "selects_filled": 0,
                    "comment_filled": False,
                    "error": "Session expired",
                })
                log.error("Stopping due to session expiry. Re-run the script.")
                break

            human_delay(config)

        browser.close()

    # ── Step 5: final report ───────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("FINAL REPORT")
    print("=" * 62)
    counts: dict[str, int] = {}
    for r in results:
        st = r.get("status", "unknown")
        counts[st] = counts.get(st, 0) + 1
        sym = {"submitted": "✓", "dry_run": "~", "skipped": "–",
               "error": "✗", "submit_failed": "!"}.get(st, "?")
        print(
            f"  {sym} [{st.upper():13s}] {r['label']}  "
            f"(radios={r.get('radios_filled',0)}, "
            f"selects={r.get('selects_filled',0)}, "
            f"comment={'yes' if r.get('comment_filled') else 'no'})"
        )
        if r.get("error"):
            print(f"               Error: {r['error']}")
    print("─" * 62)
    print(f"  Submitted   : {counts.get('submitted', 0)}")
    print(f"  Dry-run     : {counts.get('dry_run', 0)}")
    print(f"  Skipped     : {counts.get('skipped', 0)}")
    print(f"  Errors      : {counts.get('error', 0) + counts.get('submit_failed', 0)}")
    print("=" * 62)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MUJ SLCM Feedback Automation Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fill all forms but never click Submit.",
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config.json"),
        help="Path to config.json (default: config.json next to this script).",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        log.error("Config file not found: %s", config_path)
        sys.exit(1)

    config = load_config(config_path)

    if args.dry_run:
        log.info("DRY RUN mode — forms will be filled but NOT submitted.")

    run(config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
