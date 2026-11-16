# Drakelicit online

An open, always-on version of [Drakelicit](https://github.com/ferdesmello/drakelicit): anyone can give a
probability distribution for each parameter of the Drake equation, see the resulting distribution of N, and compare
it with other participants. It runs on **GitHub Pages**: no server, no database, no hosting cost.

* Everything the participant sees is computed **in the browser** (`js/drake.js`).
* At the end, the answers are sent once to a **Google Form** (`config.js`); they land in a Google Sheet.
* A **GitHub Action** runs **monthly** (or whenever you press the button), validates the new rows, appends them to
  `data/part-NNNN.jsonl/.csv` (open data, CC BY 4.0) and recomputes `data/community.json`, which the results page reads.

```
index.html intro.html consent.html context.html survey.html results.html   the pages (relative paths only)
config.js                      Form address, repo URL (empty Form address = dev mode: nothing is sent)
js/drake.js                    shared browser code: Monte Carlo + KDE, payload, results link
questions.json  i18n/*.json    the 7 parameters; pt / en / es texts (also the translated CNPq areas)
areas.json                     CNPq broad field -> specific areas (single source for the context page and the validator)
assets/slider_demo.gif         demo shown on the intro page
data/                          open data + community.json (written by the Action only; see data/README.md)
scripts/update_data.py         Sheet CSV -> validate -> data/ ; community distribution (uses scripts/compute.py)
scripts/make_fake_rows.py      fake Sheet for testing the above
.github/workflows/update-data.yml
```

## Test locally (no Form needed)

```bash
python -m http.server 8000          # then open http://localhost:8000
# with fake data:
pip install -r scripts/requirements.txt
python scripts/make_fake_rows.py --n 2500 --out /tmp/fake.csv
python scripts/update_data.py --csv /tmp/fake.csv --data-dir /tmp/data-test
```

## Going live

1. **Google Form**: one question, type *Paragraph* (long answer), not required. No sign-in required, no file
   upload, no "limit to 1 response". Link it to a new Sheet (Responses -> Link to Sheets).
2. **Form address and field name**: open the Form's preview, copy the address of the form and change
   `viewform` into `formResponse`; that is `FORM_URL`. For `FORM_ENTRY`: in the preview page's source, the
   `entry.<digits>` of the question (or use "Get pre-filled link" and read it from the link). Put both in `config.js`.
   **Do not edit that question afterwards**: its `entry.` number would change.
3. **Sheet -> CSV**: in the Sheet, File -> Share -> Publish to web -> the response sheet -> CSV. Copy the link.
4. **Secret**: repo -> Settings -> Secrets and variables -> Actions -> New repository secret `SHEET_CSV_URL`.
5. **Pages**: Settings -> Pages -> Deploy from a branch -> `main` / root.
6. **Test**: answer once, then check the Sheet got a row, then Actions -> *Update data* -> *Run workflow*.
7. Check the Form really receives the POST from the page (browser dev tools, Network tab). The answer is opaque by design.

## Things to remember

* Data in `data/` is public and **irrevocable**; the consent text says so. Keep `consent.html`, the i18n consent
  texts and the license in sync if you change anything.
* GitHub disables scheduled workflows after 60 days without repository activity; every run commits
  `data/last_run.txt` as a keepalive. If the Actions tab shows the workflow disabled, re-enable it there.
* The community curve only changes when the Action runs; the results page says so.

## Licenses

* **Code**: MIT (`LICENSE`).
* **Data** in `data/`: CC BY 4.0 (see `data/README.md`).
