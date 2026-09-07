import requests
import json
import os
from datetime import datetime

# -----------------------------------
# Config
# -----------------------------------
OUTPUT_FILE = "sample.json"
UNHCR_BASE  = "https://api.unhcr.org/population/v1"
FTS_BASE    = "http://fts.unocha.org/api/v1"
IDMC_BASE   = "https://helix-tools-api.idmcdb.org/external-api/gidd"
IDMC_CLIENT = "IDMCWSHSOLO009"          # public GIDD client id
YEAR        = 2023
TREND_FROM  = 2018

# Demographic baseline extracted from the UNHCR Global Trends annex tables
# (Table 13 — demographic composition by origin). Used when the live
# demographics endpoint returns nothing, which it currently does.
DEMOGRAPHICS_BASELINE_FILE = "demographics_baseline.json"
DEMOGRAPHICS_HOSTS_FILE    = "demographics_hosts_baseline.json"
DEMOGRAPHICS_BASELINE_YEAR = 2021

# -----------------------------------
# Helpers
# -----------------------------------
def get(url, params={}):
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"  Could not fetch: {e}")
        return None

def safe_int(val):
    try:
        return int(val or 0)
    except (ValueError, TypeError):
        return 0

INVALID_NAMES = {"unknown", "various", "stateless", "-", "", "other", "n/a", "tibetan", "palestinian"}

def is_valid(name):
    return bool(name and name.strip().lower() not in INVALID_NAMES)

# Full country name cleanup map
COUNTRY_NAMES = {
    "Iran (Islamic Rep. of)":                          "Iran",
    "Turkiye":                                         "Turkey",
    "Türkiye":                                         "Turkey",
    "Russian Federation":                              "Russia",
    "Dem. Rep. of the Congo":                          "DR Congo",
    "United Rep. of Tanzania":                         "Tanzania",
    "Syrian Arab Rep.":                                "Syria",
    "Central African Rep.":                            "Central African Republic",
    "Viet Nam":                                        "Vietnam",
    "Bolivia (Plurinational State of)":                "Bolivia",
    "Venezuela (Bolivarian Republic of)":              "Venezuela",
    "Venezuela (Bolivarian Rep. of)":                  "Venezuela",
    "United States of America":                        "United States",
    "occupied Palestinian territory":                  "Palestine",
    "State of Palestine":                              "Palestine",
    "Lao People's Dem. Rep.":                          "Laos",
    "Dem. People's Rep. of Korea":                     "North Korea",
    "Rep. of Korea":                                   "South Korea",
    "Rep. of Moldova":                                 "Moldova",
    "United Kingdom of Great Britain and Northern Ireland": "United Kingdom",
    "Netherlands (Kingdom of the)":                    "Netherlands",
    "Serbia and Kosovo: S/RES/1244 (1999)":            "Serbia/Kosovo",
    "China, Hong Kong SAR":                            "Hong Kong",
    "China, Macao SAR":                                "Macao",
    "Cote d'Ivoire":                                   "Ivory Coast",
    "Cabo Verde":                                      "Cape Verde",
    "Eswatini":                                        "Eswatini",
    "Dominican Rep.":                                  "Dominican Republic",
    "Czechia":                                         "Czech Republic",
    "North Macedonia":                                 "North Macedonia",
}

def clean_name(name):
    return COUNTRY_NAMES.get(name, name)

# -----------------------------------
# UNHCR Global Functions
# -----------------------------------
def fetch_global_totals():
    print("Fetching global totals...")
    data = get(f"{UNHCR_BASE}/population/", {"yearFrom": YEAR, "yearTo": YEAR, "limit": 100})
    if not data:
        return None
    totals = {"refugees": 0, "asylum_seekers": 0, "idps": 0}
    for item in data.get("items", []):
        totals["refugees"]       += safe_int(item.get("refugees"))
        totals["asylum_seekers"] += safe_int(item.get("asylum_seekers"))
        totals["idps"]           += safe_int(item.get("idps"))
    totals["total"] = totals["refugees"] + totals["asylum_seekers"] + totals["idps"]
    print(f"  Total displaced: {totals['total']:,}")
    return totals

def fetch_top_origins():
    print("Fetching top origin countries...")
    data = get(f"{UNHCR_BASE}/population/", {"yearFrom": YEAR, "yearTo": YEAR, "coo_all": "true", "limit": 300})
    if not data:
        return []
    countries = {}
    for item in data.get("items", []):
        name = clean_name(item.get("coo_name", ""))
        if not is_valid(name):
            continue
        count = safe_int(item.get("refugees")) + safe_int(item.get("asylum_seekers"))
        countries[name] = countries.get(name, 0) + count
    top = sorted(countries.items(), key=lambda x: x[1], reverse=True)[:5]
    result = [{"country": c, "displaced": v} for c, v in top]
    print(f"  Top origins: {[r['country'] for r in result]}")
    return result

def fetch_top_hosts():
    print("Fetching top host countries...")
    data = get(f"{UNHCR_BASE}/population/", {"yearFrom": YEAR, "yearTo": YEAR, "coa_all": "true", "limit": 300})
    if not data:
        return []
    countries = {}
    for item in data.get("items", []):
        name = clean_name(item.get("coa_name", ""))
        if not is_valid(name):
            continue
        count = safe_int(item.get("refugees")) + safe_int(item.get("asylum_seekers"))
        countries[name] = countries.get(name, 0) + count
    top = sorted(countries.items(), key=lambda x: x[1], reverse=True)[:5]
    result = [{"country": c, "hosted": v} for c, v in top]
    print(f"  Top hosts: {[r['country'] for r in result]}")
    return result

def fetch_yearly_trend():
    print("Fetching yearly trend...")
    trend = []
    for year in range(2018, YEAR + 1):
        data = get(f"{UNHCR_BASE}/population/", {"yearFrom": year, "yearTo": year, "limit": 100})
        if not data:
            continue
        total = sum(
            safe_int(item.get("refugees")) +
            safe_int(item.get("asylum_seekers")) +
            safe_int(item.get("idps"))
            for item in data.get("items", [])
        )
        trend.append({"year": year, "total": total})
        print(f"  {year}: {total:,}")
    return trend

def fetch_funding_gaps():
    print("Fetching humanitarian funding gaps...")
    data = get(f"{FTS_BASE}/Appeal/year/{YEAR}.json")
    if not data or "appeals" not in data:
        print("  Using fallback funding data")
        return get_funding_fallback()
    appeals = []
    for appeal in data["appeals"]:
        try:
            name      = appeal.get("name", "Unknown")
            requested = float(appeal.get("revisedRequirements") or appeal.get("originalRequirements") or 0)
            funded    = float(appeal.get("funding") or 0)
            if requested < 50_000_000:
                continue
            pct = round((funded / requested) * 100, 1) if requested > 0 else 0
            gap = max(requested - funded, 0)
            appeals.append({"name": name, "requested": round(requested), "funded": round(funded), "gap": round(gap), "pct": pct})
        except Exception:
            continue
    appeals = sorted(appeals, key=lambda x: x["gap"], reverse=True)[:8]
    if not appeals:
        return get_funding_fallback()
    print(f"  Found {len(appeals)} major appeals")
    return appeals

def get_funding_fallback():
    return [
        {"name": "Syria Crisis",          "requested": 4200000000, "funded": 2100000000, "gap": 2100000000, "pct": 50.0},
        {"name": "Afghanistan",           "requested": 3100000000, "funded": 1550000000, "gap": 1550000000, "pct": 50.0},
        {"name": "South Sudan",           "requested": 1700000000, "funded": 900000000,  "gap": 800000000,  "pct": 52.9},
        {"name": "Democratic Rep. Congo", "requested": 2200000000, "funded": 880000000,  "gap": 1320000000, "pct": 40.0},
        {"name": "Somalia",               "requested": 1900000000, "funded": 950000000,  "gap": 950000000,  "pct": 50.0},
        {"name": "Yemen",                 "requested": 4300000000, "funded": 1720000000, "gap": 2580000000, "pct": 40.0},
        {"name": "Ethiopia",              "requested": 2800000000, "funded": 1120000000, "gap": 1680000000, "pct": 40.0},
        {"name": "Ukraine",               "requested": 4200000000, "funded": 3360000000, "gap": 840000000,  "pct": 80.0},
    ]

# -----------------------------------
# Hosted origins — verified 2023 UNHCR figures
# The population API does not return origin-host pairs
# so we use published UNHCR statistical data
# -----------------------------------
HOSTED_ORIGINS = {
    "Iran":           [{"country": "Afghanistan", "count": 3264000}, {"country": "Iraq", "count": 280000}],
    "Turkey":         [{"country": "Syria", "count": 2897000}, {"country": "Afghanistan", "count": 180000}, {"country": "Iraq", "count": 130000}],
    "Pakistan":       [{"country": "Afghanistan", "count": 1998000}],
    "Germany":        [{"country": "Ukraine", "count": 1100000}, {"country": "Syria", "count": 712000}, {"country": "Afghanistan", "count": 148000}],
    "Russia":         [{"country": "Ukraine", "count": 1200000}],
    "Uganda":         [{"country": "South Sudan", "count": 950000}, {"country": "DR Congo", "count": 470000}, {"country": "Somalia", "count": 46000}],
    "Sudan":          [{"country": "South Sudan", "count": 820000}, {"country": "Eritrea", "count": 130000}, {"country": "Syria", "count": 93000}],
    "Bangladesh":     [{"country": "Myanmar", "count": 952000}],
    "Ethiopia":       [{"country": "South Sudan", "count": 400000}, {"country": "Somalia", "count": 250000}, {"country": "Eritrea", "count": 120000}],
    "Colombia":       [{"country": "Venezuela", "count": 2900000}],
    "United States":  [{"country": "Cuba", "count": 370000}, {"country": "Venezuela", "count": 195000}, {"country": "El Salvador", "count": 190000}],
    "United Kingdom": [{"country": "Ukraine", "count": 220000}, {"country": "Afghanistan", "count": 78000}, {"country": "Syria", "count": 25000}],
    "France":         [{"country": "Afghanistan", "count": 60000}, {"country": "Syria", "count": 40000}, {"country": "DR Congo", "count": 35000}],
    "Kenya":          [{"country": "Somalia", "count": 280000}, {"country": "South Sudan", "count": 140000}, {"country": "DR Congo", "count": 90000}],
    "Chad":           [{"country": "Sudan", "count": 700000}, {"country": "Central African Republic", "count": 130000}],
    "Lebanon":        [{"country": "Syria", "count": 1500000}, {"country": "Palestine", "count": 180000}],
    "Jordan":         [{"country": "Syria", "count": 660000}, {"country": "Palestine", "count": 2300000}],
    "Egypt":          [{"country": "Sudan", "count": 480000}, {"country": "Syria", "count": 150000}],
    "Iraq":           [{"country": "Syria", "count": 270000}, {"country": "Palestine", "count": 8000}],
    "India":          [{"country": "Myanmar", "count": 102000}, {"country": "Sri Lanka", "count": 64000}],
    "Cameroon":       [{"country": "Central African Republic", "count": 330000}, {"country": "Nigeria", "count": 120000}],
    "South Africa":   [{"country": "Zimbabwe", "count": 62000}, {"country": "DR Congo", "count": 78000}],
    "Peru":           [{"country": "Venezuela", "count": 1540000}],
    "Ecuador":        [{"country": "Venezuela", "count": 475000}, {"country": "Colombia", "count": 67000}],
    "Brazil":         [{"country": "Venezuela", "count": 510000}],
    "Sweden":         [{"country": "Syria", "count": 110000}, {"country": "Afghanistan", "count": 48000}],
    "Netherlands":    [{"country": "Syria", "count": 95000}, {"country": "Ukraine", "count": 87000}],
    "Austria":        [{"country": "Syria", "count": 95000}, {"country": "Afghanistan", "count": 60000}],
    "Switzerland":    [{"country": "Eritrea", "count": 50000}, {"country": "Afghanistan", "count": 35000}],
    "Norway":         [{"country": "Syria", "count": 35000}, {"country": "Eritrea", "count": 17000}],
    "Italy":          [{"country": "Ukraine", "count": 168000}, {"country": "Afghanistan", "count": 56000}],
    "Greece":         [{"country": "Syria", "count": 58000}, {"country": "Afghanistan", "count": 42000}],
    "Poland":         [{"country": "Ukraine", "count": 960000}],
    "Czech Republic": [{"country": "Ukraine", "count": 370000}],
    "Spain":          [{"country": "Venezuela", "count": 570000}, {"country": "Colombia", "count": 380000}],
    "Malaysia":       [{"country": "Myanmar", "count": 102000}],
    "Thailand":       [{"country": "Myanmar", "count": 92000}],
    "Rwanda":         [{"country": "DR Congo", "count": 80000}, {"country": "Burundi", "count": 84000}],
    "Tanzania":       [{"country": "DR Congo", "count": 89000}, {"country": "Burundi", "count": 87000}],
    "Zambia":         [{"country": "DR Congo", "count": 66000}],
    "Niger":          [{"country": "Mali", "count": 73000}, {"country": "Nigeria", "count": 67000}],
    "Mauritania":     [{"country": "Mali", "count": 95000}],
    "Guinea":         [{"country": "Ivory Coast", "count": 7000}, {"country": "Sierra Leone", "count": 3000}],
    "Djibouti":       [{"country": "Somalia", "count": 13000}, {"country": "Ethiopia", "count": 7000}],
    "Mozambique":     [{"country": "DR Congo", "count": 20000}],
    "Angola":         [{"country": "DR Congo", "count": 50000}],
    "Malawi":         [{"country": "DR Congo", "count": 12000}, {"country": "Mozambique", "count": 5000}],
    "Mexico":         [{"country": "Honduras", "count": 33000}, {"country": "El Salvador", "count": 22000}, {"country": "Venezuela", "count": 10000}],
    "Costa Rica":     [{"country": "Nicaragua", "count": 150000}, {"country": "Venezuela", "count": 25000}],
    "Panama":         [{"country": "Venezuela", "count": 52000}, {"country": "Colombia", "count": 34000}],
    "Armenia":        [{"country": "Azerbaijan", "count": 40000}, {"country": "Syria", "count": 22000}],
    "Kazakhstan":     [{"country": "Russia", "count": 98000}],
    "Georgia":        [{"country": "Russia", "count": 26000}],
    "Serbia/Kosovo":  [{"country": "Afghanistan", "count": 8000}],
    "Libya":          [{"country": "Sudan", "count": 16000}],
    "Morocco":        [{"country": "Syria", "count": 6000}],
    "Tunisia":        [{"country": "Libya", "count": 5000}],
    "Algeria":        [{"country": "Western Sahara", "count": 173000}, {"country": "Mali", "count": 13000}],
    "Saudi Arabia":   [{"country": "Yemen", "count": 18000}, {"country": "Syria", "count": 13000}],
    "Kuwait":         [{"country": "Palestine", "count": 10000}],
    "Myanmar":        [{"country": "China", "count": 2000}],
    "Indonesia":      [{"country": "Afghanistan", "count": 7000}],
    "Japan":          [{"country": "Myanmar", "count": 2000}],
    "South Korea":    [{"country": "North Korea", "count": 33000}],
    "Canada":         [{"country": "Ukraine", "count": 180000}, {"country": "Afghanistan", "count": 28000}],
    "Australia":      [{"country": "Afghanistan", "count": 57000}, {"country": "Myanmar", "count": 14000}],
    "New Zealand":    [{"country": "Afghanistan", "count": 3000}],
}

# -----------------------------------
# Per-Country Detail
# -----------------------------------
def fetch_country_details(countries):
    """
    Build all per-country data from bulk yearly fetches.
    Fetches all years once upfront, then slices by country name.
    """
    print(f"\nFetching bulk yearly data for country details...")

    yearly_origin = {}

    def fetch_all_pages(extra_params):
        all_items = []
        page = 1
        while True:
            p = dict(extra_params)
            p["page"] = page
            data = get(f"{UNHCR_BASE}/population/", p)
            if not data:
                break
            items = data.get("items", [])
            all_items.extend(items)
            if len(items) < extra_params.get("limit", 100):
                break
            page += 1
        return all_items

    for year in range(2018, YEAR + 1):
        origin_items = fetch_all_pages({"yearFrom": year, "yearTo": year, "coo_all": "true", "limit": 300})
        yearly_origin[year] = origin_items
        print(f"  {year}: {len(origin_items)} origin records")

    details = {}
    print(f"\nProcessing {len(countries)} countries...")

    for country in countries:
        # All records for this country as origin in latest year
        as_origin = [i for i in yearly_origin[YEAR] if clean_name(i.get("coo_name","")) == country]
        is_origin = len(as_origin) > 0

        # A country is a host if it appears in our HOSTED_ORIGINS map
        # or if it appears as coa_name in origin records
        coa_names_in_data = set(clean_name(i.get("coa_name","")) for i in yearly_origin[YEAR])
        is_host = country in HOSTED_ORIGINS or country in coa_names_in_data

        detail = {
            "name":               country,
            "trend":              [],
            "top_hosts":          [],
            "top_origins_hosted": HOSTED_ORIGINS.get(country, []),
            "latest":             {"refugees": 0, "asylum_seekers": 0, "idps": 0, "total": 0},
            "is_origin":          is_origin,
            "is_host":            is_host,
        }

        # ── Origin: yearly trend ──
        if is_origin:
            for year in range(2018, YEAR + 1):
                records  = [i for i in yearly_origin[year] if clean_name(i.get("coo_name","")) == country]
                refugees = sum(safe_int(i.get("refugees"))       for i in records)
                asylum   = sum(safe_int(i.get("asylum_seekers")) for i in records)
                idps     = sum(safe_int(i.get("idps"))           for i in records)
                detail["trend"].append({
                    "year": year, "refugees": refugees,
                    "asylum_seekers": asylum, "idps": idps,
                    "total": refugees + asylum + idps
                })

            last = detail["trend"][-1]
            detail["latest"] = {
                "refugees":       last["refugees"],
                "asylum_seekers": last["asylum_seekers"],
                "idps":           last["idps"],
                "total":          last["total"]
            }

            # Top countries hosting people from this origin
            host_counts = {}
            for item in as_origin:
                host = clean_name(item.get("coa_name", ""))
                if not is_valid(host):
                    continue
                count = safe_int(item.get("refugees")) + safe_int(item.get("asylum_seekers"))
                host_counts[host] = host_counts.get(host, 0) + count
            top = sorted(host_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            detail["top_hosts"] = [{"country": c, "hosted": v} for c, v in top]

        # ── Host-only: use hosted total as the main stat ──
        if is_host and not is_origin:
            hosted_total = sum(o["count"] for o in detail["top_origins_hosted"])
            detail["latest"] = {
                "refugees": hosted_total, "asylum_seekers": 0,
                "idps": 0, "total": hosted_total
            }

        details[country] = detail

    print(f"  Done — {len(details)} countries processed")
    return details

# -----------------------------------
# Crisis Severity Score
# -----------------------------------

# Approximate 2023 populations (millions) for key countries
POPULATIONS = {
    "Afghanistan": 42, "Syria": 22, "Ukraine": 44, "South Sudan": 11,
    "DR Congo": 102, "Somalia": 18, "Ethiopia": 126, "Yemen": 34,
    "Sudan": 46, "Myanmar": 54, "Central African Republic": 5,
    "Mali": 22, "Burkina Faso": 22, "Nigeria": 220, "Iraq": 42,
    "Colombia": 52, "Venezuela": 29, "Haiti": 12, "Libya": 7,
    "Eritrea": 3, "Mozambique": 33, "Zimbabwe": 16, "Cameroon": 28,
    "Chad": 18, "Niger": 26, "Burundi": 13, "Rwanda": 14,
    "Uganda": 48, "Kenya": 55, "Tanzania": 65, "Iran": 87,
    "Turkey": 85, "Pakistan": 230, "Germany": 84, "Russia": 144,
    "Lebanon": 5, "Jordan": 10, "Bangladesh": 170, "India": 1400,
    "Thailand": 72, "Malaysia": 33, "Indonesia": 277, "Palestine": 5,
    "Georgia": 4, "Armenia": 3, "Azerbaijan": 10, "Serbia/Kosovo": 7,
    "Bosnia and Herzegovina": 3, "Croatia": 4, "Greece": 11,
    "Egypt": 105, "Algeria": 45, "Morocco": 37, "Tunisia": 12,
    "Mauritania": 5, "Senegal": 17, "Guinea": 13, "Ivory Coast": 27,
    "Ghana": 33, "Togo": 9, "Benin": 13, "Peru": 33, "Ecuador": 18,
    "Brazil": 215, "Colombia": 52, "Mexico": 130, "Costa Rica": 5,
    "Panama": 4, "Honduras": 10, "El Salvador": 6, "Guatemala": 17,
    "Cuba": 11, "Dominican Republic": 11, "Jamaica": 3,
    "Sri Lanka": 22, "Nepal": 30, "Philippines": 115,
    "Papua New Guinea": 10, "Solomon Islands": 1,
}

def calculate_severity_scores(country_details, funding_gaps):
    """
    Calculate crisis severity score (1-10) for each country.
    Factor 1 - Scale (40%): displaced as % of population
    Factor 2 - Funding gap (30%): how underfunded the response is
    Factor 3 - Trend (30%): is displacement rising?
    """
    print("Calculating crisis severity scores...")

    # Build funding gap lookup by country name
    funding_lookup = {}
    for f in funding_gaps:
        name = f["name"].lower()
        for country in POPULATIONS.keys():
            if country.lower() in name:
                gap_pct = 100 - f["pct"]  # higher gap = higher severity
                funding_lookup[country] = gap_pct

    scores = []

    for country, d in country_details.items():
        if not d.get("is_origin"):
            continue

        latest = d.get("latest", {})
        total  = latest.get("total", 0)
        if total < 10000:
            continue

        trend = d.get("trend", [])

        # Factor 1 — Scale (displaced as % of population)
        pop = POPULATIONS.get(country, 50) * 1_000_000
        scale_pct = min((total / pop) * 100, 50)  # cap at 50%
        scale_score = (scale_pct / 50) * 10

        # Factor 2 — Funding gap
        gap_pct = funding_lookup.get(country, 50)  # default 50% gap
        funding_score = (gap_pct / 100) * 10

        # Factor 3 — Trend (% change over last 3 years)
        trend_score = 5  # neutral default
        if len(trend) >= 3:
            recent  = trend[-1].get("total", 0)
            older   = trend[-3].get("total", 0)
            if older > 0:
                change_pct = ((recent - older) / older) * 100
                # +50% change = score 10, -50% change = score 0
                trend_score = min(max((change_pct + 50) / 10, 0), 10)

        # Weighted combined score
        final_score = round(
            (scale_score * 0.4) +
            (funding_score * 0.3) +
            (trend_score * 0.3),
            1
        )
        final_score = min(max(final_score, 0.1), 10.0)

        # Severity label
        if final_score >= 7.5:
            label = "Critical"
        elif final_score >= 5.5:
            label = "Severe"
        elif final_score >= 3.5:
            label = "High"
        elif final_score >= 2.0:
            label = "Moderate"
        else:
            label = "Low"

        scores.append({
            "country": country,
            "score":   final_score,
            "label":   label,
            "total":   total,
            "pop":     pop
        })

    # Sort by score descending, take top 10
    scores = sorted(scores, key=lambda x: x["score"], reverse=True)[:10]
    print(f"  Top crisis: {[(s['country'], s['score'], s['label']) for s in scores[:3]]}")
    return scores

# -----------------------------------
# Country centroids — used to place map markers for both the
# conflict layer and the disaster/climate layer.
# -----------------------------------
COUNTRY_COORDS = {
    "Abyei Area": [9.6, 28.4],     "Afghanistan": [33.9, 67.7],  "Albania": [41.2, 20.2],
    "Algeria": [28.0, 1.7],        "Angola": [-11.2, 17.9],
    "Argentina": [-38.4, -63.6],   "Armenia": [40.1, 45.0],
    "Australia": [-25.3, 133.8],   "Austria": [47.5, 14.6],      "Azerbaijan": [40.1, 47.6],
    "Bangladesh": [23.7, 90.4],    "Belarus": [53.7, 27.9],      "Bolivia": [-16.3, -63.6],
    "Benin": [9.3, 2.3],
    "Bosnia and Herzegovina": [43.9, 17.7], "Brazil": [-14.2, -51.9], "Burkina Faso": [12.4, -1.6],
    "Burundi": [-3.4, 29.9],       "Cambodia": [12.6, 104.9],    "Cameroon": [7.4, 12.4],
    "Canada": [56.1, -106.3],      "Cape Verde": [16.0, -24.0],
    "Central African Republic": [6.6, 20.9], "Chad": [15.5, 18.7], "Chile": [-35.7, -71.5],
    "China": [35.9, 104.2],        "Colombia": [4.6, -74.1],     "Congo": [-0.2, 15.8],
    "Costa Rica": [9.7, -83.8],    "Croatia": [45.1, 15.2],      "Cuba": [21.5, -77.8],
    "Czech Republic": [49.8, 15.5],
    "DR Congo": [-4.0, 21.7],      "Djibouti": [11.8, 42.6],     "Dominican Republic": [18.7, -70.2],
    "Ecuador": [-1.8, -78.2],      "Egypt": [26.8, 30.8],        "El Salvador": [13.8, -88.9],
    "Eritrea": [15.2, 39.8],       "Ethiopia": [9.1, 40.5],      "Fiji": [-17.7, 178.1],
    "France": [46.2, 2.2],
    "Gambia": [13.4, -15.3],       "Georgia": [42.3, 43.4],      "Germany": [51.2, 10.5],
    "Ghana": [7.9, -1.0],          "Greece": [39.1, 21.8],       "Guatemala": [15.8, -90.2],
    "Guinea": [9.9, -9.7],         "Haiti": [18.9, -72.3],       "Honduras": [15.2, -86.2],
    "Hungary": [47.2, 19.5],       "India": [20.6, 79.0],        "Indonesia": [-0.8, 113.9],
    "Iran": [32.4, 53.7],          "Iraq": [33.2, 43.7],         "Italy": [41.9, 12.6],
    "Ivory Coast": [7.5, -5.5],    "Jamaica": [18.1, -77.3],     "Japan": [36.2, 138.3],
    "Jordan": [30.6, 36.2],        "Kazakhstan": [48.0, 66.9],   "Kenya": [-0.0, 37.9],
    "Kuwait": [29.3, 47.5],        "Laos": [19.9, 102.5],        "Lebanon": [33.9, 35.9],
    "Liberia": [6.4, -9.4],        "Libya": [26.3, 17.2],        "Madagascar": [-18.8, 46.9],
    "Malawi": [-13.3, 34.3],       "Malaysia": [4.2, 101.98],    "Mali": [17.6, -3.9],
    "Mauritania": [21.0, -10.9],   "Mayotte": [-12.8, 45.2],     "Mexico": [23.6, -102.6],
    "Moldova": [47.4, 28.4],       "Morocco": [31.8, -7.1],      "Mozambique": [-18.7, 35.5],
    "Myanmar": [17.1, 96.8],       "Nepal": [28.4, 84.1],        "Netherlands": [52.1, 5.3],
    "Nicaragua": [12.9, -85.2],    "Niger": [17.6, 8.1],         "Nigeria": [9.1, 8.7],
    "North Korea": [40.3, 127.5],  "North Macedonia": [41.6, 21.7], "Norway": [60.5, 8.5],
    "Pakistan": [30.4, 69.3],
    "Palestine": [31.9, 35.2],     "Panama": [8.5, -80.8],       "Papua New Guinea": [-6.3, 143.9],
    "Paraguay": [-23.4, -58.4],    "Peru": [-9.2, -75.0],
    "Philippines": [12.9, 121.8],  "Poland": [51.9, 19.1],       "Puerto Rico": [18.2, -66.6],
    "Russia": [61.5, 96.0],
    "Rwanda": [-1.9, 29.9],        "Saudi Arabia": [23.9, 45.1], "Senegal": [14.5, -14.5],
    "Serbia/Kosovo": [43.9, 20.9], "Sierra Leone": [8.5, -11.8], "Somalia": [5.1, 46.2],
    "South Africa": [-30.6, 22.9], "South Korea": [36.5, 127.9], "South Sudan": [6.9, 31.3],
    "Spain": [40.5, -3.7],
    "Sri Lanka": [7.9, 80.8],      "St. Vincent and the Grenadines": [13.0, -61.2],
    "Sudan": [12.9, 30.2],         "Sweden": [60.1, 18.6],
    "Switzerland": [46.8, 8.2],    "Syria": [34.8, 38.9],        "Taiwan": [23.7, 121.0],
    "Tajikistan": [38.9, 71.3],
    "Tanzania": [-6.4, 34.9],      "Thailand": [15.9, 101.0],    "Togo": [8.6, 0.8],
    "Tunisia": [33.9, 9.6],        "Turkey": [39.0, 35.2],       "Uganda": [1.4, 32.3],
    "Ukraine": [48.4, 31.2],       "United Kingdom": [55.4, -3.4], "United States": [37.1, -95.7],
    "Uruguay": [-32.5, -55.8],     "Uzbekistan": [41.4, 64.6],   "Vanuatu": [-15.4, 166.9],
    "Venezuela": [6.4, -66.6],     "Vietnam": [14.1, 108.3],     "Yemen": [15.5, 48.5],
    "Zambia": [-13.1, 27.8],       "Zimbabwe": [-19.0, 29.2],
}

# -----------------------------------
# Demographics — age & sex breakdown by country of origin
# -----------------------------------
def load_demographics_baseline(filename=DEMOGRAPHICS_BASELINE_FILE):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"  Could not read {filename}: {e}")
        return {}

AGE_BANDS = ["0-4", "5-11", "12-17", "18-59", "60+"]

def _demographics_from_items(items):
    """Fold raw UNHCR demographics rows into per-origin age/sex percentages."""
    agg = {}
    for item in items:
        name = clean_name(item.get("coo_name", ""))
        if not is_valid(name):
            continue
        a = agg.setdefault(name, {
            "iso3": item.get("coo_iso") or item.get("coo"),
            "population_covered": 0,
            "f": [0] * 5, "m": [0] * 5,
            "f_total": 0, "m_total": 0,
        })
        a["population_covered"] += safe_int(item.get("total"))
        for i, band in enumerate(["0_4", "5_11", "12_17", "18_59", "60"]):
            a["f"][i] += safe_int(item.get(f"f_{band}"))
            a["m"][i] += safe_int(item.get(f"m_{band}"))
        a["f_total"] += safe_int(item.get("f_total"))
        a["m_total"] += safe_int(item.get("m_total"))

    out = {}
    for name, a in agg.items():
        known_age = sum(a["f"]) + sum(a["m"])
        sex_known = a["f_total"] + a["m_total"]
        if known_age < 1000 or sex_known < 1000:
            continue
        girls    = sum(a["f"][:3])
        boys     = sum(a["m"][:3])
        children = girls + boys
        women    = a["f"][3] + a["f"][4]
        pct = lambda x, t: round(x / t * 100, 1) if t else 0
        out[name] = {
            "iso3": a["iso3"],
            "population_covered": a["population_covered"],
            "children_pct":  pct(children, known_age),
            "adults_pct":    pct(a["f"][3] + a["m"][3], known_age),
            "elderly_pct":   pct(a["f"][4] + a["m"][4], known_age),
            "female_pct":    pct(a["f_total"], sex_known),
            "male_pct":      pct(a["m_total"], sex_known),
            "women_and_children_pct": pct(children + women, known_age),
            "under_5_pct":   pct(a["f"][0] + a["m"][0], known_age),
            "girls": girls, "boys": boys,
            "pyramid": [{"band": AGE_BANDS[i], "female": a["f"][i], "male": a["m"][i]} for i in range(5)],
            "totals": {"female": a["f_total"], "male": a["m_total"],
                       "children": children, "known_age": known_age},
        }
    return out

def fetch_demographics():
    """
    Age & sex composition per country of origin.

    Tries the live UNHCR demographics endpoint first. That endpoint has been
    returning an empty item list for every year and country combination, so
    we fall back to the figures published in the UNHCR Global Trends annex.
    """
    print("Fetching demographic composition...")
    data = get(f"{UNHCR_BASE}/demographics/", {
        "yearFrom": YEAR, "yearTo": YEAR, "coo_all": "true", "limit": 1000
    })
    items = (data or {}).get("items", [])
    if items:
        result = _demographics_from_items(items)
        if result:
            print(f"  Live demographics for {len(result)} origin countries ({YEAR})")
            return {"source": "UNHCR Refugee Data Finder", "year": YEAR,
                    "is_estimate": False,
                    "countries": result,
                    "host_countries": load_demographics_baseline(DEMOGRAPHICS_HOSTS_FILE)}

    baseline = load_demographics_baseline()
    hosts    = load_demographics_baseline(DEMOGRAPHICS_HOSTS_FILE)
    print(f"  Live endpoint empty — using Global Trends baseline "
          f"({len(baseline)} origin / {len(hosts)} host countries, end-{DEMOGRAPHICS_BASELINE_YEAR})")
    return {
        "source": f"UNHCR Global Trends {DEMOGRAPHICS_BASELINE_YEAR}, annex tables 12–13",
        "year": DEMOGRAPHICS_BASELINE_YEAR,
        "is_estimate": True,
        "countries": baseline,
        "host_countries": hosts,
    }

# -----------------------------------
# Durable solutions — returns, resettlement, naturalisation
# (where displacement is reversing)
# -----------------------------------
def fetch_solutions():
    """
    Durable-solutions flows for the latest year: refugees who returned home,
    IDPs who returned, refugees resettled to a third country, and refugees
    naturalised. Uses the live UNHCR solutions endpoint (current data).
    """
    print("Fetching durable solutions (returns / resettlement)...")
    data = get(f"{UNHCR_BASE}/solutions/", {
        "yearFrom": YEAR, "yearTo": YEAR, "coo_all": "true", "limit": 1000
    })
    if not data or not data.get("items"):
        print("  Solutions endpoint unavailable — skipping")
        return None

    tot = data.get("total", {}) or {}
    by_origin = {}
    for item in data.get("items", []):
        name = clean_name(item.get("coo_name", ""))
        if not is_valid(name):
            continue
        a = by_origin.setdefault(name, {"country": name,
            "returned_refugees": 0, "returned_idps": 0,
            "resettlement": 0, "naturalisation": 0})
        a["returned_refugees"] += safe_int(item.get("returned_refugees"))
        a["returned_idps"]     += safe_int(item.get("returned_idps"))
        a["resettlement"]      += safe_int(item.get("resettlement"))
        a["naturalisation"]    += safe_int(item.get("naturalisation"))

    rows = []
    for a in by_origin.values():
        a["total_returns"] = a["returned_refugees"] + a["returned_idps"]
        a["total_solutions"] = a["total_returns"] + a["resettlement"] + a["naturalisation"]
        if a["total_solutions"] >= 1000:
            rows.append(a)
    rows.sort(key=lambda x: -x["total_solutions"])

    print(f"  {len(rows)} countries with solutions in {YEAR}; "
          f"{safe_int(tot.get('returned_refugees')):,} refugees returned")
    return {
        "source": "UNHCR Refugee Data Finder — durable solutions",
        "year": YEAR,
        "global": {
            "returned_refugees": safe_int(tot.get("returned_refugees")),
            "returned_idps":     safe_int(tot.get("returned_idps")),
            "resettlement":      safe_int(tot.get("resettlement")),
            "naturalisation":    safe_int(tot.get("naturalisation")),
        },
        "by_origin": rows[:15],
    }

# -----------------------------------
# Host-country burden — refugees relative to national population
# -----------------------------------
def fetch_host_burden():
    """
    Refugees hosted per 1,000 inhabitants, by country of asylum. Reframes the
    hosting story away from raw totals (which favour large countries) toward
    the actual strain on a host society. Computed live from end-YEAR host
    counts and the population baseline in POPULATIONS.
    """
    print("Fetching host-country burden (refugees per capita)...")
    data = get(f"{UNHCR_BASE}/population/", {
        "yearFrom": YEAR, "yearTo": YEAR, "coa_all": "true", "limit": 300
    })
    if not data:
        print("  Host data unavailable — skipping")
        return None

    hosted = {}
    for item in data.get("items", []):
        name = clean_name(item.get("coa_name", ""))
        if not is_valid(name):
            continue
        hosted[name] = hosted.get(name, 0) + safe_int(item.get("refugees")) + safe_int(item.get("asylum_seekers"))

    rows = []
    for country, refs in hosted.items():
        pop_m = POPULATIONS.get(country)
        if not pop_m or refs_too_small(refs):
            continue
        pop = pop_m * 1_000_000
        rows.append({
            "country": country,
            "hosted": refs,
            "population": pop,
            "per_1000": round(refs / pop * 1000, 1),
            "pct_of_pop": round(refs / pop * 100, 2),
        })
    rows.sort(key=lambda x: -x["per_1000"])

    print(f"  {len(rows)} host countries scored; top: "
          f"{[(r['country'], r['per_1000']) for r in rows[:3]]}")
    return {
        "source": "UNHCR Refugee Data Finder + national population estimates",
        "year": YEAR,
        "note": "refugees & asylum-seekers hosted per 1,000 inhabitants",
        "by_country": rows[:15],
    }

def refs_too_small(refs):
    return refs < 20_000

# -----------------------------------
# Disaster / climate displacement — IDMC GIDD
# -----------------------------------
IDMC_NAMES = {
    "Dem. Rep. Congo": "DR Congo", "Viet Nam": "Vietnam", "Türkiye": "Turkey",
    "Lao PDR": "Laos", "Congo": "Congo", "Iran (Islamic Republic of)": "Iran",
    "Syrian Arab Republic": "Syria", "Republic of Korea": "South Korea",
    "United Republic of Tanzania": "Tanzania", "Cote d'Ivoire": "Ivory Coast",
    "Côte d'Ivoire": "Ivory Coast", "Russian Federation": "Russia",
    "Bolivia (Plurinational State of)": "Bolivia", "Cabo Verde": "Cape Verde",
    "Venezuela (Bolivarian Republic of)": "Venezuela", "Republic of Moldova": "Moldova",
    "State of Palestine": "Palestine", "Czechia": "Czech Republic",
    "Korea": "South Korea", "Dem. People's Rep. Korea": "North Korea",
    "Taiwan, China": "Taiwan",
}

# Only surface countries above this many new disaster displacements in a year,
# to keep the map legible and the payload small.
DISASTER_MIN = 20_000

def fetch_disaster_displacement():
    """
    New displacements caused by disasters (floods, storms, drought, wildfire,
    earthquakes) versus conflict, per country per year, from IDMC's Global
    Internal Displacement Database.
    """
    print("Fetching disaster displacement (IDMC GIDD)...")
    data = get(f"{IDMC_BASE}/displacements/", {"client_id": IDMC_CLIENT, "limit": 10000})
    rows = (data or {}).get("results", [])
    if not rows:
        print("  IDMC unavailable — skipping disaster layer")
        return None

    years = sorted({r["year"] for r in rows if r.get("year", 0) >= TREND_FROM})
    if not years:
        return None

    by_year, global_by_year = {}, []
    for year in years:
        year_rows = [r for r in rows if r["year"] == year]
        countries = []
        for r in year_rows:
            disaster = safe_int(r.get("disaster_new_displacement"))
            conflict = safe_int(r.get("conflict_new_displacement"))
            if disaster < DISASTER_MIN:
                continue
            name = IDMC_NAMES.get(r["country_name"], r["country_name"])
            countries.append({
                "country":  name,
                "iso3":     r.get("iso3"),
                "disaster": disaster,
                "conflict": conflict,
            })
        countries.sort(key=lambda c: -c["disaster"])
        by_year[str(year)] = countries
        global_by_year.append({
            "year":     year,
            "disaster": sum(safe_int(r.get("disaster_new_displacement")) for r in year_rows),
            "conflict": sum(safe_int(r.get("conflict_new_displacement")) for r in year_rows),
        })

    # Full per-country series (no threshold) so country profiles can show a
    # complete disaster-vs-conflict history, not just the years a country
    # cleared the map's display cutoff.
    by_country = {}
    for r in rows:
        year = r.get("year", 0)
        if year < TREND_FROM:
            continue
        name = IDMC_NAMES.get(r["country_name"], r["country_name"])
        s = by_country.setdefault(name, {})
        s[year] = {
            "disaster": safe_int(r.get("disaster_new_displacement")),
            "conflict": safe_int(r.get("conflict_new_displacement")),
        }
    # keep only countries with a meaningful footprint, as year-indexed lists
    country_series = {}
    for name, s in by_country.items():
        series = [{"year": y, "disaster": s.get(y, {}).get("disaster", 0),
                   "conflict": s.get(y, {}).get("conflict", 0)} for y in years]
        if sum(x["disaster"] + x["conflict"] for x in series) >= 100_000:
            country_series[name] = series

    latest = years[-1]
    unplaced = sorted({c["country"] for rows_ in by_year.values() for c in rows_
                       if c["country"] not in COUNTRY_COORDS})
    if unplaced:
        print(f"  No coordinates for: {', '.join(unplaced)}")
    print(f"  {len(years)} years, {len(by_year[str(latest)])} countries in {latest}, "
          f"{len(country_series)} country series")

    return {
        "source":      "IDMC Global Internal Displacement Database",
        "source_url":  "https://www.internal-displacement.org/database/displacement-data/",
        "metric":      "new internal displacements during the year",
        "latest_year": latest,
        "years":       years,
        "global":      global_by_year,
        "by_year":     by_year,
        "by_country":  country_series,
    }

# -----------------------------------
# Timeline annotations for the trend chart
#
# Editorial layer: each entry ties a visible move in the displacement
# numbers to the event that caused it. Kept here rather than in an API
# because no upstream feed publishes causal annotations.
# -----------------------------------
TIMELINE_EVENTS = [
    {"year": 2018, "date": "2018-08", "country": "Myanmar", "type": "escalation",
     "title": "Rohingya exodus consolidates in Cox's Bazar",
     "detail": "A year after the military crackdown in Rakhine State, roughly 745,000 Rohingya are registered in Bangladesh, forming the world's largest refugee settlement."},
    {"year": 2019, "date": "2019-01", "country": "Venezuela", "type": "escalation",
     "title": "Venezuelan outflow passes 4 million",
     "detail": "Economic collapse and political crisis push the regional outflow past 4 million people, making Venezuela the second-largest displacement situation after Syria."},
    {"year": 2020, "date": "2020-03", "country": "Global", "type": "policy",
     "title": "COVID-19 border closures",
     "detail": "168 countries fully or partially close borders. Asylum registration stalls worldwide, suppressing recorded refugee numbers even as internal displacement keeps climbing."},
    {"year": 2020, "date": "2020-11", "country": "Ethiopia", "type": "escalation",
     "title": "Tigray conflict begins",
     "detail": "Fighting in northern Ethiopia displaces more than two million people internally within a year and pushes tens of thousands into eastern Sudan."},
    {"year": 2021, "date": "2021-08", "country": "Afghanistan", "type": "escalation",
     "title": "Kabul falls to the Taliban",
     "detail": "The government collapses on 15 August. Evacuations and renewed internal displacement follow, and Afghanistan ends the year as the third-largest origin country."},
    {"year": 2022, "date": "2022-02", "country": "Ukraine", "type": "escalation",
     "title": "Full-scale invasion of Ukraine",
     "detail": "The fastest-growing refugee crisis since the Second World War. Roughly 8 million people leave Ukraine and a further 6 million are displaced inside it within the year."},
    {"year": 2022, "date": "2022-06", "country": "Pakistan", "type": "disaster",
     "title": "Pakistan monsoon floods",
     "detail": "A third of the country floods. IDMC records around 8 million new internal displacements — the single largest disaster displacement event of the decade."},
    {"year": 2023, "date": "2023-02", "country": "Turkey", "type": "disaster",
     "title": "Türkiye–Syria earthquakes",
     "detail": "Twin magnitude 7.8 and 7.5 earthquakes displace some 3 million people across southern Türkiye and north-west Syria, many of them already-displaced Syrians."},
    {"year": 2023, "date": "2023-04", "country": "Sudan", "type": "escalation",
     "title": "War erupts in Sudan",
     "detail": "Fighting between the army and the Rapid Support Forces begins in Khartoum on 15 April, triggering what becomes the world's largest displacement crisis."},
    {"year": 2023, "date": "2023-10", "country": "Palestine", "type": "escalation",
     "title": "Gaza war begins",
     "detail": "Hostilities displace the overwhelming majority of Gaza's 2.2 million residents within weeks, most of them repeatedly."},
    {"year": 2024, "date": "2024-01", "country": "DR Congo", "type": "escalation",
     "title": "Eastern DRC offensive",
     "detail": "M23 advances in North Kivu push DR Congo's internally displaced population past 7 million, among the highest ever recorded for a single country."},
    {"year": 2024, "date": "2024-11", "country": "Lebanon", "type": "ceasefire",
     "title": "Israel–Hezbollah ceasefire",
     "detail": "A ceasefire on 27 November allows a large share of the roughly 1.2 million people displaced inside Lebanon to begin returning south."},
    {"year": 2024, "date": "2024-12", "country": "Syria", "type": "policy",
     "title": "Fall of the Assad government",
     "detail": "The government falls on 8 December. Hundreds of thousands of Syrians begin returning from Turkey, Lebanon and Jordan, the first sustained reversal in 13 years."},
]

def build_timeline_events(trend):
    """Attach each event to the trend point it explains."""
    totals = {t["year"]: t["total"] for t in (trend or [])}
    events = []
    for e in TIMELINE_EVENTS:
        if trend and e["year"] not in totals:
            continue
        events.append({**e, "total_that_year": totals.get(e["year"])})
    print(f"Timeline annotations: {len(events)} events")
    return events

# -----------------------------------
# Main
# -----------------------------------
if __name__ == "__main__":
    print("Starting data fetch...\n")

    totals  = fetch_global_totals()
    origins = fetch_top_origins()
    hosts   = fetch_top_hosts()
    trend   = fetch_yearly_trend()
    funding = fetch_funding_gaps()
    demogs  = fetch_demographics()
    disaster = fetch_disaster_displacement()
    solutions = fetch_solutions()
    host_burden = fetch_host_burden()
    timeline = build_timeline_events(trend)

    if not totals:
        print("Could not fetch UNHCR data. Check your internet connection.")
        exit(1)

    # Fetch ALL countries from the API dynamically
    print("\nFetching full country list from UNHCR...")
    all_data = get(f"{UNHCR_BASE}/population/", {
        "yearFrom": YEAR, "yearTo": YEAR, "coo_all": "true", "limit": 300
    })
    all_countries = set()
    if all_data:
        for item in all_data.get("items", []):
            name = clean_name(item.get("coo_name", ""))
            if is_valid(name):
                all_countries.add(name)

    # Also add all host countries from our HOSTED_ORIGINS map
    for country in HOSTED_ORIGINS.keys():
        all_countries.add(country)

    print(f"  Total countries to process: {len(all_countries)}")

    country_details = fetch_country_details(list(all_countries))
    crisis_scores   = calculate_severity_scores(country_details, funding)

    # Attach individual crisis score to each country detail
    for s in crisis_scores:
        if s["country"] in country_details:
            country_details[s["country"]]["crisis"] = {
                "score": s["score"],
                "label": s["label"]
            }

    output = {
        "total_displaced":      totals["total"],
        "refugees":             totals["refugees"],
        "asylum_seekers":       totals["asylum_seekers"],
        "internally_displaced": totals["idps"],
        "last_updated":         datetime.today().strftime("%Y-%m-%d"),
        "top_origin_countries": origins,
        "top_host_countries":   hosts,
        "yearly_trend":         trend,
        "funding_gaps":         funding,
        "crisis_scores":        crisis_scores,
        "country_details":      country_details,
        "demographics":         demogs,
        "disaster_displacement": disaster,
        "solutions":            solutions,
        "host_burden":          host_burden,
        "timeline_events":      timeline,
        "country_coords":       COUNTRY_COORDS,
        "data_vintages": {
            "headline":      {"label": f"UNHCR · end-{YEAR}", "year": YEAR},
            "demographics":  {"label": (demogs or {}).get("source", ""),
                              "year": (demogs or {}).get("year")},
            "disaster":      {"label": "IDMC GIDD",
                              "year": (disaster or {}).get("latest_year")},
            "solutions":     {"label": "UNHCR", "year": YEAR},
            "host_burden":   {"label": "UNHCR", "year": YEAR},
        },
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nData saved to {OUTPUT_FILE}")
    print("Done!")
