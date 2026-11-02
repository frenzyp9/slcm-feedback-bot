# MUJ SLCM Feedback Automation Bot

Automatically fills course/faculty feedback forms on the **Manipal University Jaipur SLCM portal** (`mujslcm.jaipur.manipal.edu`) using Python + Playwright in headed mode.

**You log in manually** — the script never touches your credentials.

---

## How it works (quick summary)

```
1. Opens mujslcm.jaipur.manipal.edu in a visible Chrome window
2. Waits for YOU to log in (username + password, any OTP)
3. Navigates directly to /Student/Survey/FeedbackList
4. Finds every pending form (the "btn-clean" links on the list page)
5. For each pending form:
   - Clicks all radio buttons (class="yescheck") — the "positive" answer
   - Sets all dropdowns to value "Y" (the portal's "Yes/Agree" value)
   - Fills the comment textarea with your configured text
   - Shows a summary; waits for Enter if confirm_before_submit = true
   - Clicks button#btnSubmit
   - Saves a screenshot to /logs
6. Prints a final report
```

---

## Installation

```powershell
# 1. Open a terminal in this folder
cd slcm-feedback-bot

# 2. Create and activate a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Download Playwright's Chromium browser
playwright install chromium
```

---

## Configuration (`config.json`)

Open `config.json` and set these before running:

| Field | Type | What it does |
|---|---|---|
| `default_rating` | int 1–5 | Selects the Nth radio in scale groups; `"Y"` is used for all dropdowns |
| `default_comment` | string | Text typed into the comment/textarea box of every form |
| `overrides` | object | `{"subject substring": rating}` — case-insensitive partial match on form label |
| `comments` | object | `{"subject substring": "comment text"}` |
| `confirm_before_submit` | bool | `true` → press Enter before each submit; `false` → fully automatic |
| `headless` | bool | `false` = visible browser (recommended); `true` = background (not advised) |
| `delay_between_actions_ms` | `[min, max]` | Random delay range in milliseconds between actions |
| `login_wait_timeout_seconds` | int | How long to wait for your manual login (default 300 s = 5 min) |
| `max_forms` | int | Safety cap — max number of forms to process in one run |
| `screenshot_on_submit` | bool | Save PNG screenshots before/after each submission |
| `logs_dir` | string | Folder for screenshots (created automatically) |

### Example overrides
```json
"default_rating": 4,
"overrides": {
  "Data Structures": 5,
  "Operating Systems": 3
},
"default_comment": "Well-taught course.",
"comments": {
  "Data Structures": "Excellent explanations and problem-solving sessions."
}
```

---

## Running

### Safe first run — dry run (fills but never submits)
```powershell
python main.py --dry-run
```
Check the console output to confirm ratings and selectors are working, then do the real run.

### Real run
```powershell
python main.py
```

### Fully automatic (no confirmation prompts)
Set `"confirm_before_submit": false` in `config.json`, then:
```powershell
python main.py
```

### Custom config path
```powershell
python main.py --config C:\path\to\other_config.json
```

---

## Login instructions

When the browser opens:

1. Type your **username** in the format `firstname.registrationnumber`  
   *(e.g. `prachi.230901001` — no `@muj.manipal.edu`, the portal adds it)*
2. Type your **password**
3. Click **Sign In**
4. Handle any OTP / MFA if prompted
5. Once your dashboard appears, the script takes over automatically

---

## Logs and screenshots

All screenshots are saved to `./logs/`:

```
logs/
  20261102_221500_pre_submit_Data_Structures_Lab.png
  20261102_221502_post_submit_Data_Structures_Lab.png
  ...
```

> **Privacy note:** Screenshots may contain your name, roll number, and marks. Keep the `logs/` folder private.

---

## Troubleshooting

### "No btn-clean elements found on page"
The portal may have changed its markup, or you may not be on the FeedbackList page.  
→ After logging in, navigate to `/Student/Survey/FeedbackList` manually and inspect the pending form links. Share the HTML with me to update the selector.

### Radios not being filled / 0 radios found
The script looks for `input[type="radio"][class="yescheck"]`.  
→ Open a feedback form in the browser, press F12, and check whether the radio inputs have `class="yescheck"`. If not, share the HTML.

### "Session expired — redirected to login mid-run"
Your SLCM session timed out.  
→ Increase `delay_between_actions_ms` slightly, or set `confirm_before_submit: true` so you stay engaged. Then re-run.

### Submit button not found
The script looks for `button#btnSubmit`.  
→ In the form page, press F12 → Inspector → search for `btnSubmit`. If the ID is different, update `SEL_SUBMIT` in `main.py` (line near the top, clearly labeled).

### `playwright install chromium` fails
Try: `python -m playwright install chromium`

### SSL certificate errors
Add `ignore_https_errors=True` to `browser.new_context(...)` in `main.py` (line ~193).

---

## Project files

```
slcm-feedback-bot/
├── main.py          ← Automation script (all logic here)
├── config.json      ← Your settings (edit this before running)
├── requirements.txt ← Python dependencies (just playwright)
├── README.md        ← This file
└── logs/            ← Created at runtime; screenshots saved here
```

---

## Key selectors (for debugging)

| Element | Selector used |
|---|---|
| Feedback list links | `a.btn-clean` |
| Radio buttons (positive) | `input[type='radio'][class='yescheck']` |
| Dropdown selects | `select` (value `"Y"`) |
| Comment textarea | `textarea` |
| Submit button | `button#btnSubmit` |
| Feedback list URL | `/Student/Survey/FeedbackList` |

All selectors are defined as constants at the top of `main.py` for easy updates.
