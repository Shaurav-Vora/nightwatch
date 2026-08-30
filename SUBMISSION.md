# Submission answers

Draft answers for https://forms.gle/jLgBzVTG1NhJ3gNe6
Fill in the bracketed bits, paste the rest.

---

## Team

**Team name:** [your team name, or your own name if solo]
**Members:** Shaurav Vora, shauravvora@gmail.com
[add any other members here with their emails]

## Project

**Title:** NIGHTWATCH

**One-line pitch:**
> An interactive map of how many hours a day each city block spends above its
> danger threshold, for Phoenix, Houston and Chicago, ranked by how many people
> live there.

## Project description

Use the long one unless the field is short. Both say the same thing.

### Long (about 270 words)

> NIGHTWATCH is an interactive map of how many hours a day each city block
> spends above its danger threshold, built on FortyGuard's temperature API, for
> Phoenix, Houston and Chicago.
>
> Cities pick where to plant trees and open cooling centres using maps of peak
> temperature. Across a single city that number barely moves. In Phoenix the
> entire afternoon spread, from the coolest 100-metre block to the hottest, is
> 1.81 °C, which gives a heat office nothing to prioritise with. Ask the same
> 21,453 blocks how long they stay above a danger threshold and the answer
> spans 9.07 hours.
>
> The two questions do not pick the same ground. Take the hundred hottest
> blocks at 3pm and the hundred that stay dangerous longest: in Phoenix not one
> block is on both lists. R² between afternoon temperature and duration is
> 0.106, so the map cities already have explains about a tenth of what matters.
>
> What it produces is a shortlist. Joined-up patches of blocks that stayed in
> the hottest quarter of their city on every hot day measured, ranked by how
> many people live in them rather than by area, with coordinates, exportable as
> CSV and GeoJSON alongside a printable report carrying the method and the
> limitations.
>
> The result is tested rather than asserted. Across five hot days between 2022
> and 2025, 1,408 Houston blocks came out worst every single time against 22
> expected by chance. Phoenix reaches only 2.4 times chance, and the app says
> so on screen instead of presenting its site list with the same confidence.

### Short (about 60 words)

> An interactive map of how many hours a day each city block spends above its
> danger threshold, for Phoenix, Houston and Chicago. Peak temperature varies
> by 1.81 °C across Phoenix; hours above the threshold vary by 9.07. The two
> measures pick different blocks entirely. NIGHTWATCH ranks the worst areas by
> resident count and exports them with coordinates.

## Track

**Primary:** Government & Environment (heat vulnerability mapping)

**Secondary (up to 2):** Interactive Maps · Data Analysis

## Who this is for

The form asks who makes a decision *differently*, so name the decision, not
the job titles.

> The people who choose where a city puts shade, cool roofs and cooling
> centres: heat officers, urban planners, and the public health teams who
> advise them on which neighbourhoods to help first. Today they rank by peak
> afternoon temperature or by the size of an area; NIGHTWATCH ranks by hours
> above the danger threshold and by how many people live there, which in
> Phoenix picks an entirely different hundred blocks.

## Location and time period

**Area:** three US cities, each a single AOI at 100 m resolution
- Phoenix, AZ — 82.8 mi², 21,453 blocks
- Houston, TX — 86.2 mi², 22,333 blocks
- Chicago, IL — 37.3 mi², 9,606 blocks (1,430 water, excluded)

**Time period:** five hot days between 2022 and 2025:
19 July 2022, 25 July 2023, 9 July 2024, 6 August 2024, 22 July 2025.
The single-day layers all use 22 July 2025.

## How we used the Temperature API

> Every number in the project comes from `POST /v1/heatmap`, submitted and then
> polled on `GET /v1/status/{activity_id}`.
>
> The hero metric is `analytic_type: "persistence"` with `filter_type: 3`, a
> full 24-hour day, which returns the longest unbroken run of hours above a
> threshold. We chose persistence over `exceedance` deliberately: eight
> consecutive dangerous hours is an event, eight scattered ones are not.
>
> Two `tcm` calls per city per date, at 15:00 and 03:00 local, give the
> afternoon and pre-dawn fields. Their means set each city's threshold, midway
> between the two, so a mild city and an extreme one are both measurable.
>
> `POST /v1/streetview` provides ground-level imagery with FortyGuard's own
> segmentation inside Houston's persistent core. `POST /v1/satellite` was used
> for a land-cover comparison across 30 blocks, which returned a null result
> and is reported as one.
>
> `POST /v1/system/fetch-api-key-usage` was the first call made, on day one, to
> measure cost per call before designing the harvest.
>
> Everything is cached to disk on a hash of the request body, so the site is
> served from static JSON and re-rendering the UI costs zero credits. The
> README carries one full request and response.

**Things we found that are worth reporting back:**
- `start_time` is local, not UTC. Established by sweeping all 24 hours and
  locating the pre-dawn minimum. The docs imply otherwise.
- Failed tasks are free; credits are deducted only on success.
- The AOI ceiling is at least 60 mi² on this plan, well past the 10 mi²
  documented for Basic.
- 60 m granularity returns 2.78× the tiles with the same standard deviation
  and range, so the native resolution is coarser than 100 m.
- `env_params` heat index holds one temperature anchor across 24 hours and
  peaks around 2am. It looks like a spectacular confirmation of any night-heat
  thesis and is an artifact. We did not use it.
- `persistence` and `exceedance` return `properties.value`; `tcm` returns
  `properties.average_temperature` with no `value` field.

## API keys

Solo submission: my own key. [If you have teammates, every member's key must
be listed here.]
The key is not in the repository. `.env` is gitignored and has never been
committed; `.env.example` shows the variable name only.

## AI tools used

Add any other tool you used. Disclosure is not penalised, so err towards more.

> **Claude (Anthropic)**, used through the Claude desktop app, as a pair
> programmer and editor across the whole build.
>
> **Code.** The Python side: the API client with its submit-then-poll loop and
> a SQLite disk cache keyed on a hash of the request body, the UTC-to-local
> conversion and its 24 unit tests, the per-city threshold selection, the
> multi-date consistency counting and its chance baseline, the census join, the
> flood-fill that groups blocks into sites, and the export scripts. The
> frontend: one self-contained HTML file with deck.gl over MapLibre, five map
> layers, the ranked-site panel, the CSV and GeoJSON exports, and the printable
> report with inline SVG charts.
>
> **Writing.** Drafts of the landing page, the README and the video script.
>
> **What I kept.** Choosing what to measure and what to cut, reading
> FortyGuard's documentation and designing the harvest to fit the credit
> budget, and verification. Every figure in the app, the README and the video
> was recomputed from the shipped data files and checked against the running
> interface before it was stated.
>
> That last step earned its place. AI drafts repeatedly produced claims that
> sounded right and were not: a comparison between two ranked areas that the
> numbers contradicted, a line calling a map "almost flat" while it rendered
> deep red to deep blue, and an early framing of the whole project as
> night-time heat when the measured window actually runs from mid-morning to
> about two hours after sunset. Each was caught by checking the data or the
> screen rather than by rereading the text, and each correction is in the
> commit history.

### Short version, if the field is small

> Claude (Anthropic), as a pair programmer and editor: the Python harvest and
> analysis scripts, the deck.gl frontend, and drafts of the README and video
> script. I chose what to measure, designed the harvest around the credit
> budget, and verified every figure against the shipped data before it was
> stated. That check caught several confident but wrong AI-drafted claims,
> which are corrected in the commit history.

## Links

**Live demo:** https://shaurav-vora.github.io/nightwatch/
**Code repo:** https://github.com/Shaurav-Vora/nightwatch
**Video:** [paste the unlisted YouTube or Loom URL]

---

# Pre-submit checklist

- [ ] `git push origin main` — there are unpushed commits
- [ ] Wait for GitHub Pages to rebuild, then open the live URL in a **private
      window** and confirm both pages load with no login
- [ ] Confirm the repo is public, or add hackathon@fortyguard.com as a
      collaborator
- [ ] Record the video, max 3 minutes, voiceover, showing the app working
- [ ] Upload as unlisted and check the link in a private window
- [ ] Paste all three links into the form
- [ ] Disclose the API key
- [ ] Disclose AI tool use
