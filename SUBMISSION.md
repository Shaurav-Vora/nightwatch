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

## Who it is for, and the problem

> Cities decide where to plant trees and open cooling centres using maps of how
> hot it gets. Across a single city that number barely moves. In Phoenix the
> entire afternoon spread from the coolest block to the hottest is 1.81 °C,
> which is nothing you can act on, so a conventional heat map gives a heat
> office almost nothing to prioritise with.
>
> Ask the same blocks how many hours a day they spend above a danger threshold
> and the answer spans 9.07 hours. The two questions do not pick the same
> ground: take the hundred hottest blocks at 3pm and the hundred that stay
> dangerous longest, and in Phoenix not one block is on both lists.
>
> NIGHTWATCH is built for whoever decides where a city's cooling budget goes.
> It finds the joined-up areas that stayed worst across every hot day measured,
> ranks them by resident count rather than by area, and exports the list with
> coordinates as CSV and GeoJSON.

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

> Claude (Anthropic) was used throughout, as a pair programmer and editor: for
> the Python harvest and analysis scripts, the frontend, and for drafting and
> repeatedly correcting the written material. Every figure that appears in the
> app, the README or the video was recomputed from the shipped data files and
> checked against the interface before being stated.

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
