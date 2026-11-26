# Build Brief — Static demo site on GitHub Pages

## GOAL
A static demo site on GitHub Pages so a recruiter can see the project
without cloning it. No backend, no database access at runtime, no
secrets, no external requests.

## PAGES
1. Home: two or three short paragraphs on what the project does, a link
   to the repo, and links to the two pages below. State plainly that it
   is a work in progress and the course content is a sample (module 1
   of a planned course).
2. Course demo: the SCORM package for the published managers version
   (version 8da81e12), served as plain HTML in its existing preview mode.
3. Run report for job 4d12de16, built from its stored evidence:
   - summary counts (blocks, touch rate, checks run/passed, planned /
     surviving / reverted adaptations, verifier findings)
   - the touch list with PLAN's reason for each block
   - original and adapted text side by side for all 10 planned blocks,
     with the reverted block marked and its cause shown
   - the deterministic check results by check name
   - the models used for each stage
   - a short note that three earlier runs halted, and why

## BUILD
- scripts/build_demo.py reads the database locally and writes static
  files to site/. The generated site/ folder is committed.
- A GitHub Actions workflow publishes site/ to Pages. The workflow must
  not need database access or any secret.
- All links and asset paths relative, so the site works under /refract/.
- Reuse the design tokens in web/app/globals.css, system fonts only.
  Responsive, keyboard accessible, no colour-only meaning.

## SAFETY
- The export must contain no emails, keys, connection strings or user
  ids. Run gitleaks over site/ before committing.
- Footer on every page: source-available, all rights reserved, per
  LICENSE-NOTICE.

## ACCEPTANCE
- Serve site/ locally and click through every page: zero console
  errors, no requests leaving the site, course completes in preview.
- Add a "Live demo" link near the top of the README in a separate commit.
- Commit and push. No attribution trailers on any commit.
- Report the URL and anything I need to do in GitHub settings.

If the database is paused, tell me and wait; do not work around it.
