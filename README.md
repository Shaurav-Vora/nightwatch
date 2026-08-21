# NIGHTWATCH

**The heat maps cities use today measure the wrong thing.**

Live: **https://shaurav-vora.github.io/nightwatch/**

Cities decide where to plant trees and open cooling centres using maps of *how
hot* it gets. Across a single city that number barely moves. *How long* a block
stays dangerous moves by hours, and the same ground comes out worst every
time.

---

## The finding

We measured every 100-metre block across 83 square miles of Phoenix on 22 July
2025. From the coolest block to the hottest, the entire afternoon spread was
**1.81 °C**.

Then we asked the same blocks a different question: how many hours in a row do
you spend above the danger threshold? That answer spans **9.07 hours**.

| | Phoenix | Houston | Chicago |
|---|---|---|---|
| Afternoon temperature spread | 1.81 °C | 4.20 °C | 5.56 °C |
| Hours-above-threshold spread | 9.07 h | 5.56 h | 11.56 h |
| R² (does 3pm predict duration?) | **0.106** | 0.802 | 0.783 |
| Blocks measured | 21,453 | 22,333 | 9,606 (1,430 water) |
| Threshold | 35 °C | 32 °C | 25 °C |
| Top 100 by heat also in top 100 by duration | **0** | 45 | **0** |

R² is the row that matters, and the only one you can read across cities
rather than down a column (see the threshold caveat below). In Phoenix,
knowing a block's afternoon temperature tells you almost nothing about how
long it stays above the danger threshold. So a conventional heat map of
Phoenix says very little about duration, and that is the map cities use.

**Two real blocks in Phoenix both read 99.7 °F at 3pm.** One spends 6.1 hours
above 35 °C. The other spends 11.2. Five hours apart, identical on a
temperature map. The app shows you both, with coordinates.

## The threshold, and why it differs per city

"Dangerously hot" needs a temperature to sit above, and that temperature is a
FortyGuard API parameter (`threshold`, in °C, with `direction: "above"`).
Everything the project calls *exposure* is the longest unbroken run of hours
above it.

We set it per city, midway between that city's own measured 3pm and 3am means:

| City | 3pm mean | 3am mean | Midpoint | Threshold used |
|---|---|---|---|---|
| Phoenix | 38.33 °C | 31.31 °C | 34.82 | **35 °C** (95 °F) |
| Houston | 36.20 °C | 27.29 °C | 31.75 | **32 °C** (89.6 °F) |
| Chicago | 29.99 °C | 20.74 °C | 25.36 | **25 °C** (77 °F) |

A single fixed number across all three would measure nothing. At 35 °C,
Chicago never crosses the threshold and every block scores zero. At 25 °C,
Phoenix never drops below it and every block scores twenty-four. The midpoint
guarantees the day partly clears the threshold, which is the only place
duration has room to vary. It lives in `THRESHOLDS` in `duration_test.py`.

> **Compare within a city, not between them.** Because each city has its own
> threshold, the hour figures above are not interchangeable. Chicago's
> 11.56-hour spread is not "worse" than Houston's 5.56-hour spread; it is
> measured against a different threshold. What *is* comparable across cities
> is R², which is dimensionless, and the repeat ratios below. Every CSV and GeoJSON
> export carries its own `threshold_c` so the number cannot travel without it.

### When the counted hours actually fall

One call covers a full 24 hours and returns the longest unbroken stretch above
the threshold. In Chicago that stretch runs **09:00 to 22:00**, so it covers
the afternoon and ends about two hours after sunset. By 3am Chicago is back
near 21 °C, four degrees below its own threshold, which is why the 3am layer
has no block above the threshold. The landing page carries the hour-by-hour
curve with both crossing points marked.

Blocks differ at both ends of that stretch. Some cross the threshold at 09:00
and others at 11:00; some drop back below it at 20:00 and others hold on until
22:00. That is where the spread in hours comes from.

Changing the threshold is not a display setting. It is a request parameter, so
a new threshold means re-harvesting that city from the API and re-running
`export_cities.py`. The slider in the app's left panel is a **percentile
filter** ("show me the worst 20%"), not a threshold control.

## Does it repeat, or was it one bad day?

That was the first objection, so it was the first thing we tested. We re-ran
the full measurement across five hot days between 2022 and 2025 and counted
how often each block landed in the worst quarter of its own city. Blocks are
ranked *within* each date, so a hot day and a mild one count equally and the
absolute threshold cancels out.

| City | Dates used | Blocks worst *every* time | Expected by chance | Ratio |
|---|---|---|---|---|
| Houston | 5 of 5 | 1,408 | 21.8 | **64.6×** |
| Chicago | 3 of 5 | 1,298 | 127.8 | **10.2×** |
| Phoenix | 3 of 5 | 817 | 335.2 | **2.4×** |

Houston kept all five dates. Phoenix and Chicago kept three; the rest were
dropped because the city was saturated or too cool to tell blocks apart, and
the app names the dropped dates rather than hiding them.

Houston and Chicago have a persistent core: the same ground comes out worst
whatever the weather does, which makes it a property of the built environment
and therefore something you can fix. Phoenix at 2.4× is weak, and the app says so on screen
instead of presenting its site list with the same confidence.

## Who it affects

Population comes from the US Census (ACS 2022 5-year, joined to 2023 Gazetteer
tract centroids).

| City | Residents in the persistent core | Of area total | Density vs city average |
|---|---|---|---|
| Phoenix | 11,485 | 486,587 | 0.62× |
| Houston | 29,128 | 550,848 | 0.84× |
| Chicago | 64,442 | 669,093 | 0.71× |

All three come out below 1. The ground that is reliably hottest is mostly
industrial and commercial, so heat and residents overlap only in part. Rank
sites by area and you send money to the wrong places. The app ranks by
residents instead, and shows you which sites move when it does.

---

## Running the demo

Nothing to install. Open **https://shaurav-vora.github.io/nightwatch/**, read
the two cards, and click through to the map. A guided tour opens on its own.

The path through the app, roughly ninety seconds:

1. **Phoenix loads by default** because its R² is the lowest, which is the
   strongest form of the argument.
2. **Switch to "Afternoon heat", then back to "Hottest every time".** That
   toggle is the whole insight: the first is the map cities use today.
3. **Drag the filter left** to strip everything but the worst-hit blocks.
4. **Click a row in the ranked list.** The map flies there and outlines the
   actual blocks, not a circle approximating them.
5. **Generate full report** opens a self-contained printable page with inline
   SVG charts. It works offline and prints to PDF.
6. **Switch to Houston** for the 64.6× repeat figure and the street-level
   photographs with FortyGuard's segmentation over them.

Everything on screen is exportable: all blocks as CSV, ranked sites as CSV,
and the grid as GeoJSON for QGIS or ArcGIS.

## Architecture

Offline harvest → static GeoJSON → client-side WebGL. There is no backend and
no live API call in the demo path, so the page cannot fail in front of a judge
and re-rendering the UI costs zero credits.

```
FortyGuard API  ──►  disk cache (SQLite, keyed on request-body hash)
                          │
                          ▼
                   export_cities.py  ──►  web/data/*.json   (committed)
                          │
                          ▼
        deck.gl PolygonLayer over MapLibre GL, CARTO dark basemap
```

- **Frontend** is one self-contained `web/index.html`. No build step, no
  framework, no bundler.
- **Python** is dependency-light on purpose: `requests`, `python-dotenv`,
  `pytest`, `tzdata`. No geopandas, no numpy. Spearman, OLS, flood fill,
  farthest-point sampling and the census join are all written out.
- **Compact tile format.** Three cities at 100 m is ~53,000 tiles, about 20 MB
  as GeoJSON polygons. Every tile is an identical square on a regular grid, so
  we ship one centroid plus a shared cell size and rebuild the squares in the
  browser. Phoenix drops from ~8 MB to under 1 MB.

## What we found out about the API

Worth reporting back, and all of it is in `probe_report.json` from day one.

- **`start_time` is local, not UTC.** The docs imply otherwise. We established
  it by sweeping all 24 hours and locating the pre-dawn minimum. Getting this
  wrong shifts the entire analysis by hours *and the map still looks correct*,
  so `nightwatch/timeutil.py` does the conversion in one place and has 24 unit
  tests on it.
- **Failed tasks are free.** Credits are deducted only on success. This
  inverts the usual risk model: validate request shape on a tiny polygon,
  because the expensive mistake is a *successful* call against the wrong AOI.
- **The AOI ceiling is at least 60 mi²** on the hackathon plan, confirmed by
  probe, well past the 10 mi² documented for Basic. Our city AOIs run 37 to 86
  mi² as single requests.
- **Granularity is close to cosmetic.** 60 m returns 2.78× the tiles with
  the same standard deviation and range, so the underlying resolution is
  coarser than 100 m and block-level detail should not be over-read.
- **`persistence` and `exceedance` can disagree impossibly**, with persistence
  exceeding exceedance on some tiles. We use `persistence` throughout because
  the longest *unbroken* run is the mortality-relevant quantity: eight
  consecutive dangerous hours is an event, eight scattered ones are not.
- **`env_params` heat index is a humidity curve, not a diurnal one.** It holds
  one temperature anchor across all 24 hours and peaks around 2am. For a
  project about night-time heat this is a lethal false friend, because it looks
  like spectacular confirmation of the thesis and is an artifact. We do not use
  it.
- **`/streetview` needs a public road.** It failed at the geometric centroid of
  every persistent core, which is itself informative: that ground is not on the
  road network. Houston eventually succeeded at a block 1.8 km off-centre;
  Chicago and Phoenix failed at all 14 candidates each, at no cost.
- **Satellite segmentation returned implausible classes**, including "mountain"
  at 15.9% in flat Houston, so we report the land-cover comparison as a null
  result rather than leaning on it.

## Limitations

Several of these cut against us.

- Modelled temperature field for a single day, not sensor measurement.
- Population is matched to the **nearest tract centroid**, not by polygon
  intersection, and assumed uniform within a tract. Dependable for ranking
  areas of hundreds of blocks, indicative only for any single block.
- Refuge access, where present, uses **straight-line distance**. This
  *understates* the problem: walls, highways and rail only make a cool place
  harder to reach.
- The land-cover comparison across 30 blocks found **no significant difference
  at any of 10 classes**. The street-view photographs are illustrative of one
  location, not a sample, and both results are reported.
- Phoenix's persistence ratio of 2.4× is weak. Its site list should not be
  treated as fixed, and the app says so where the list appears.
- **Each city uses its own threshold**, so hour figures are comparable within a
  city and not between cities. R² and the repeat ratios are the cross-city
  numbers. A single common threshold would be a fair robustness check and we
  did not have the credits to run one.
- We say heat duration **correlates with excess mortality in the
  epidemiological literature**. We do not claim a body count.

---

## Running it yourself

```bash
git clone https://github.com/Shaurav-Vora/nightwatch.git
cd nightwatch
python -m venv .venv && .venv\Scripts\activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                 # add FORTYGUARD_API_KEY
pytest                                               # 24 tests, no API key needed
```

Serve the site locally (the app fetches JSON, so `file://` will not work):

```bash
python -m http.server 8000
# then open http://localhost:8000/
```

`python export_cities.py` rebuilds every dataset from the disk cache at **zero
credits**. Only a cache miss costs anything.

### What each script does

**The pipeline** (these produce what the site serves):

| Script | Purpose |
|---|---|
| `city_test.py` | AOI definitions and the per-city fetch helpers |
| `duration_test.py` | Holds `THRESHOLDS`, the per-city danger temperature |
| `multidate.py` | Re-runs the measurement across five dates |
| `consistency.py` | Counts blocks in the worst quartile every time, vs chance |
| `population.py` | Census join, appends residents per block |
| `amplification.py` | Temperature span vs exposure span, R², slope |
| `explain_core.py` | Land-cover comparison inside vs outside the core |
| `core_photos.py` | Finds a street-view location inside the core, four headings |
| `export_cities.py` | Bakes everything into `web/data/*.json` |
| `preview_maps.py` | Renders the landing-page maps from the baked data |
| `diurnal.py` | Draws the 24 h curve showing where the counted hours fall |

**The investigation trail** (kept deliberately, this is where the failed
hypothesis lives):

| Script | What it settled |
|---|---|
| `probe.py` | Day-one API probe: costs, schemas, AOI ceiling, granularity |
| `validate_lag.py` | Confirmed `start_time` is local, not UTC |
| `hourly_curve.py` | 24-hour sweep locating the pre-dawn minimum |
| `cooling_deficit.py` | Day/night cooling metric and the Spearman helper |
| `water_confound.py` | Measures how distance from water drives the day/night link |
| `detrend.py` | Tests longitude as an alternative confound |
| `granularity_test.py` | Showed 60 m adds tiles but no information |
| `sv_probe.py` | Separated "endpoint broken" from "no imagery here" |

Note that `cooling_deficit.py` and `detrend.py` are imported by `city_test.py`,
so the trail is load-bearing, not dead weight.

### Layout

```
index.html              landing page, the argument
web/index.html          the app, self-contained
web/data/               baked datasets and street-view imagery
nightwatch/             client, disk cache, geometry, timezone conversion
tests/                  24 tests, all on the timezone conversion
probe_report.json       day-one API probe results
```

---

Data from the FortyGuard API. Basemap © CARTO, © OpenStreetMap contributors.
Population from the US Census Bureau.
