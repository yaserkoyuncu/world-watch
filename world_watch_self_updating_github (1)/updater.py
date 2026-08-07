#!/usr/bin/env python3
"""
World Watch v4 — reliability-focused flagship monitor

Changes from v3:
- Direct official-page fetch first.
- Free Jina Reader fallback for blocked / JS-heavy pages.
- Free Jina Search fallback for broken generic URLs or pages without edition text.
- Strict title/link-based edition extraction (avoids using unrelated page dates).
- Rejects announcements, concept notes, "towards..." pages, future editions and methodology pages.
- Known-current floors stop obvious regressions such as ITU 2021 or WDR 2015.
- One-time migration baseline prevents false "NEW" alerts when correcting old state.
"""

from __future__ import annotations
import hashlib, json, re, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "monitor_state.json"
OUTPUT_FILE = ROOT / "auto_updates.json"
HEALTH_FILE = ROOT / "monitor_health.json"

ENGINE_VERSION = "4.0"
NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()
CURRENT_YEAR = NOW.year

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WorldWatchPersonalDashboard/4.0)",
    "Accept-Language": "en-US,en;q=0.8",
}
TIMEOUT = 35

MONTHS = {
    "january":1,"jan":1,"february":2,"feb":2,"march":3,"mar":3,"april":4,"apr":4,
    "may":5,"june":6,"jun":6,"july":7,"jul":7,"august":8,"aug":8,
    "september":9,"sep":9,"sept":9,"october":10,"oct":10,"november":11,"nov":11,
    "december":12,"dec":12,
}

# id|name|org|category|mode|official landing URL|matching expression
RAW = r"""
imf_weo|World Economic Outlook|IMF|Economy & Political Economy|intra|https://www.imf.org/en/Publications/WEO|\bWorld Economic Outlook\b
imf_gfsr|Global Financial Stability Report|IMF|Economy & Political Economy|intra|https://www.imf.org/en/Publications/GFSR|\bGlobal Financial Stability Report\b
imf_fiscal|Fiscal Monitor|IMF|Economy & Political Economy|intra|https://www.imf.org/en/Publications/FM|\bFiscal Monitor\b
worldbank_gep|Global Economic Prospects|World Bank|Economy & Political Economy|intra|https://www.worldbank.org/en/publication/global-economic-prospects|\bGlobal Economic Prospects\b
worldbank_wdr|World Development Report|World Bank|Economy & Political Economy|annual|https://www.worldbank.org/en/publication/wdr/wdr-archive|\bWorld Development Report\b
oecd_eo|OECD Economic Outlook|OECD|Economy & Political Economy|intra|https://www.oecd.org/en/topics/economic-outlook.html|\b(?:OECD )?Economic Outlook\b
bis_aer|Annual Economic Report|BIS|Economy & Political Economy|annual|https://www.bis.org/publ/arpdf/ar2026e.htm|\bAnnual Economic Report\b
unctad_tdr|Trade and Development Report|UNCTAD|Economy & Political Economy|annual|https://unctad.org/en/Pages/Publications/TradeandDevelopmentReport.aspx|\bTrade and Development Report\b
wto_gto|Global Trade Outlook and Statistics|WTO|Economy & Political Economy|intra|https://www.wto.org/english/res_e/publications_e/gtos0326_e.htm|\bGlobal Trade Outlook and Statistics\b
ilo_est|Employment and Social Trends|ILO|Economy & Political Economy|intra|https://www.ilo.org/publications/flagship-reports/employment-and-social-trends-2026|\bEmployment and Social Trends\b
wef_grr|Global Risks Report|World Economic Forum|Geopolitics & Security|annual|https://www.weforum.org/publications/global-risks-report-2026/|\bGlobal Risks Report\b
msc_msr|Munich Security Report|Munich Security Conference|Geopolitics & Security|annual|https://securityconference.org/en/publications/munich-security-report/2026/|\bMunich Security Report\b
sipri_yearbook|SIPRI Yearbook|SIPRI|Geopolitics & Security|annual|https://www.sipri.org/yearbook/2026|\bSIPRI Yearbook\b
odni_ata|Annual Threat Assessment|ODNI|Geopolitics & Security|annual|https://www.odni.gov/index.php/newsroom/reports-publications/reports-publications-2026/4141-2026-annual-threat-assessment|\bAnnual Threat Assessment\b
stanford_ai|AI Index|Stanford HAI|Technology & AI|annual|https://hai.stanford.edu/ai-index/2026-ai-index-report|\bAI Index\b
mck_tech|Technology Trends Outlook|McKinsey|Technology & AI|annual|https://www.mckinsey.com/capabilities/tech-and-ai/our-insights/the-top-trends-in-tech/|\bTechnology Trends Outlook\b|\btechnology trends.*20\d{2}\b
mck_ai|State of AI|McKinsey|Technology & AI|annual|https://www.mckinsey.com/capabilities/quantumblack/our-insights/the-state-of-ai/|\b(?:The )?State of AI\b
deloitte_tech|Tech Trends|Deloitte|Technology & AI|annual|https://www.deloitte.com/us/en/insights/topics/technology-management/tech-trends.html|\bTech Trends\b
kpmg_tech|Global Tech Report|KPMG|Technology & AI|annual|https://kpmg.com/uk/en/insights/technology/kpmg-global-tech-report.html|\bGlobal Tech Report\b
wipo_gii|Global Innovation Index|WIPO|Technology & AI|annual|https://www.wipo.int/en/web/global-innovation-index/index|\bGlobal Innovation Index\b
itu_ff|Facts and Figures|ITU|Technology & AI|annual|https://www.itu.int/itu-d/reports/statistics/facts-figures-2025/|\bFacts and Figures\b
wmo_climate|State of the Global Climate|WMO|Climate & Energy|annual|https://wmo.int/publication-series/state-of-global-climate/state-of-global-climate-2025|\bState of (?:the )?Global Climate\b
unep_egr|Emissions Gap Report|UNEP|Climate & Energy|annual|https://www.unep.org/resources/emissions-gap-report-2025|\bEmissions Gap Report\b
unep_agr|Adaptation Gap Report|UNEP|Climate & Energy|annual|https://www.unep.org/resources/adaptation-gap-report-2025|\bAdaptation Gap Report\b
iea_weo|World Energy Outlook|IEA|Climate & Energy|annual|https://www.iea.org/reports/world-energy-outlook-2025|\bWorld Energy Outlook\b
irena_weto|World Energy Transitions Outlook|IRENA|Climate & Energy|annual|https://www.irena.org/publications/2024/Nov/World-Energy-Transitions-Outlook-2024|\bWorld Energy Transitions Outlook\b
gcb|Global Carbon Budget|Global Carbon Project|Climate & Energy|annual|https://globalcarbonbudget.org/|\bGlobal Carbon Budget\b
un_wpp|World Population Prospects|UN DESA|Demography & Society|periodic|https://www.un.org/development/desa/pd/world-population-prospects-2024|\bWorld Population Prospects\b
un_wup|World Urbanization Prospects|UN DESA|Demography & Society|periodic|https://www.un.org/development/desa/pd/world-urbanization-prospects-2025|\bWorld Urbanization Prospects\b
iom_wmr|World Migration Report|IOM|Demography & Society|periodic|https://worldmigrationreport.iom.int/msite/wmr-2026-interactive/|\bWorld Migration Report\b
unhcr_gt|Global Trends|UNHCR|Demography & Society|annual|https://www.unhcr.org/au/global-trends|\bGlobal Trends\b
undp_hdr|Human Development Report|UNDP|Demography & Society|periodic|https://hdr.undp.org/content/human-development-report-2025|\bHuman Development Report\b
un_social|World Social Report|UN DESA|Demography & Society|periodic|https://social.desa.un.org/issues/world-social-report|\bWorld Social Report\b
happiness|World Happiness Report|Wellbeing Research Centre|Demography & Society|annual|https://www.worldhappiness.report/ed/2026/|\bWorld Happiness Report\b
who_whs|World Health Statistics|WHO|Health, Food & Education|annual|https://www.who.int/publications/i/item/9789240122482|\bWorld Health Statistics\b
fao_sofi|State of Food Security and Nutrition in the World|FAO et al.|Health, Food & Education|annual|https://www.fao.org/publications/fao-flagship-publications/the-state-of-food-security-and-nutrition-in-the-world/2025/en|\b(?:The )?State of Food Security and Nutrition in the World\b
unesco_gem|Global Education Monitoring Report|UNESCO|Health, Food & Education|annual|https://www.unesco.org/gem-report/en/publication/equity-and-access|\bGlobal Education Monitoring Report\b|\bGEM Report\b
undp_mpi|Global Multidimensional Poverty Index|UNDP / OPHI|Health, Food & Education|annual|https://www.undp.org/turkiye/publications/2025-global-multidimensional-poverty-index-mpi|\b(?:Global )?Multidimensional Poverty Index\b|\bGlobal MPI\b
vdem|Democracy Report|V-Dem Institute|Democracy & Rights|annual|https://www.v-dem.net/publications/democracy-reports/|\bDemocracy Report\b
idea|Global State of Democracy|International IDEA|Democracy & Rights|annual|https://www.idea.int/publications/catalogue/global-state-of-democracy-2025-democracy-on-the-move|\bGlobal State of Democracy\b
freedom|Freedom in the World|Freedom House|Democracy & Rights|annual|https://freedomhouse.org/report/freedom-world/2026/growing-shadow-autocracy|\bFreedom in the World\b
wjp|Rule of Law Index|World Justice Project|Democracy & Rights|annual|https://worldjusticeproject.org/rule-of-law-index/|\bRule of Law Index\b
cpi|Corruption Perceptions Index|Transparency International|Democracy & Rights|annual|https://www.transparency.org/en/cpi/2025|\bCorruption Perceptions Index\b
rsf|World Press Freedom Index|Reporters Without Borders|Democracy & Rights|annual|https://rsf.org/en/2026-rsf-index-press-freedom-25-year-low|\bWorld Press Freedom Index\b|\b2026 RSF Index\b
"""

SERIES = []
for line in RAW.strip().splitlines():
    sid,name,org,cat,mode,url,match = line.split("|",6)
    SERIES.append(dict(id=sid,name=name,org=org,category=cat,mode=mode,url=url,match=match))

# Known-current minimums, verified when this engine was built (Aug 2026).
# These do not create alerts. They only prevent accepting obviously old/wrong results.
FLOORS = {
    "imf_weo":(2026,7),"imf_gfsr":(2026,4),"imf_fiscal":(2026,4),
    "worldbank_gep":(2026,6),"worldbank_wdr":(2025,0),"oecd_eo":(2026,6),
    "bis_aer":(2026,0),"unctad_tdr":(2025,0),"wto_gto":(2026,3),"ilo_est":(2026,5),
    "wef_grr":(2026,0),"msc_msr":(2026,0),"sipri_yearbook":(2026,0),"odni_ata":(2026,0),
    "stanford_ai":(2026,0),"mck_tech":(2025,0),"mck_ai":(2025,0),"deloitte_tech":(2026,0),
    "kpmg_tech":(2026,0),"wipo_gii":(2025,0),"itu_ff":(2025,0),
    "wmo_climate":(2025,0),"unep_egr":(2025,0),"unep_agr":(2025,0),
    "iea_weo":(2025,0),"irena_weto":(2024,0),"gcb":(2025,0),
    "un_wpp":(2024,0),"un_wup":(2025,0),"iom_wmr":(2026,0),"unhcr_gt":(2025,0),
    "undp_hdr":(2025,0),"un_social":(2025,0),"happiness":(2026,0),
    "who_whs":(2026,0),"fao_sofi":(2026,0),"unesco_gem":(2026,0),"undp_mpi":(2025,0),
    "vdem":(2026,0),"idea":(2025,0),"freedom":(2026,0),"wjp":(2025,0),
    "cpi":(2025,0),"rsf":(2026,0),
}

BAD_PHRASES = re.compile(
    r"\b(towards?|upcoming|coming soon|save the date|concept note|background paper|"
    r"methodology|codebook|technical procedures|consultation|call for|webinar|"
    r"launch event|provisional agenda|draft|team page|working paper)\b", re.I
)

# External domains permitted only when they are the organization itself / official regional mirror.
DOMAIN_ALIASES = {
    "unhcr_gt":{"unhcr.org"},
    "undp_mpi":{"undp.org","hdr.undp.org"},
    "gcb":{"globalcarbonbudget.org"},
}

DISCOVERY = [
    ("imf","IMF","Economy & Political Economy","https://www.imf.org/en/publications",r"\b(report|outlook|monitor|update|AI|global|financial|fiscal)\b"),
    ("wb","World Bank","Economy & Political Economy","https://www.worldbank.org/en/news/all",r"\b(report|prospects|outlook|poverty|development|economic|climate|digital|AI|trade)\b"),
    ("who","WHO","Health, Food & Education","https://www.who.int/news-room/releases",r"\b(global|report|statistics|health|disease|outbreak|mortality|vaccine)\b"),
    ("cset","Georgetown CSET","Technology & AI","https://cset.georgetown.edu/publications/",r"\b(AI|semiconductor|compute|technology|biotech|China|cyber|robot|quantum)\b"),
    ("icg","International Crisis Group","Geopolitics & Security","https://www.crisisgroup.org/latest-updates",r"\b(conflict|war|crisis|security|peace|ceasefire|election)\b"),
    ("cb","Carbon Brief","Climate & Energy","https://www.carbonbrief.org/",r"\b(climate|carbon|emissions|energy|warming|temperature|renewable|oil|gas|coal)\b"),
    ("quanta","Quanta Magazine","Science","https://www.quantamagazine.org/",r"\b(AI|physics|biology|mathematics|computer|quantum|algorithm|genome|cosmology)\b"),
]

def load_json(p, default):
    try: return json.loads(p.read_text(encoding="utf-8"))
    except Exception: return default

def save_json(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def clean(s):
    return re.sub(r"\s+"," ",s or "").strip()

def canonical(base, href):
    u = urljoin(base, href)
    p = urlparse(u)
    q = p.query
    if q and any(x in q.lower() for x in ("utm_","fbclid","gclid")): q=""
    return p._replace(fragment="",query=q).geturl()

def direct_html(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return BeautifulSoup(r.text,"html.parser"), r.url

def jina_reader(url):
    ju = "https://r.jina.ai/" + url
    r = requests.get(ju, headers={"Accept":"text/plain","User-Agent":HEADERS["User-Agent"]}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def jina_search(query):
    su = "https://s.jina.ai/" + quote(query, safe="")
    r = requests.get(su, headers={"Accept":"text/plain","User-Agent":HEADERS["User-Agent"]}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def version_from(text, url, mode):
    blob = clean(f"{text} {url}")
    if BAD_PHRASES.search(blob):
        return None
    years = [int(y) for y in re.findall(r"\b(20\d{2})\b", blob)]
    years = [y for y in years if 2000 <= y <= CURRENT_YEAR]  # never accept future-edition announcements
    if not years: return None
    y = max(years)
    month = 0
    if mode == "intra":
        low = blob.lower()
        ms = [n for k,n in MONTHS.items() if re.search(rf"\b{re.escape(k)}\.?\b", low)]
        month = max(ms) if ms else 0
    return (y,month)

def label(v, mode):
    y,m=v
    if mode=="intra" and m:
        names=["","January","February","March","April","May","June","July","August","September","October","November","December"]
        return f"{names[m]} {y}"
    return str(y)

def allowed_domain(spec, url):
    dom = urlparse(url).netloc.lower().replace("www.","")
    official = urlparse(spec["url"]).netloc.lower().replace("www.","")
    if spec["id"] in DOMAIN_ALIASES:
        return any(dom == d or dom.endswith("."+d) for d in DOMAIN_ALIASES[spec["id"]])
    return (dom == official or dom.endswith("."+official) or official.endswith("."+dom))

def candidates_from_html(spec, soup, page_url):
    rx = re.compile(spec["match"],re.I)
    out=[]
    if soup.title:
        t=clean(soup.title.get_text(" ",strip=True))
        if rx.search(t): out.append((t,page_url,9,"direct-title"))
    for h in soup.find_all(["h1","h2","h3","h4"]):
        t=clean(h.get_text(" ",strip=True))
        if rx.search(t): out.append((t,page_url,8,"direct-heading"))
    for a in soup.find_all("a",href=True):
        t=clean(a.get_text(" ",strip=True))
        if not t: continue
        u=canonical(page_url,a["href"])
        # Strict: use link text itself, not parent-card dates.
        if rx.search(t) or rx.search(u):
            out.append((t,u,7,"direct-link"))
    return out

def candidates_from_markdown(spec, text, source_kind):
    rx=re.compile(spec["match"],re.I); out=[]
    # Markdown links
    for label_text,url in re.findall(r"\[([^\]]{2,300})\]\((https?://[^)\s]+)",text):
        t=clean(label_text)
        if rx.search(t) or rx.search(url):
            out.append((t,url,7,source_kind+"-link"))
    # Heading / title-like lines. URL is the official landing page.
    for line in text.splitlines():
        t=clean(re.sub(r"^#+\s*","",line))
        if 4 <= len(t) <= 300 and rx.search(t):
            out.append((t,spec["url"],6,source_kind+"-line"))
    return out

def choose_candidate(spec, candidates):
    floor=FLOORS.get(spec["id"],(2000,0))
    good=[]
    for text,url,score,method in candidates:
        if BAD_PHRASES.search(text): continue
        if not allowed_domain(spec,url): continue
        v=version_from(text,url,spec["mode"])
        if not v: continue
        cmpv=(v[0],v[1] if spec["mode"]=="intra" else 0)
        floorv=(floor[0],floor[1] if spec["mode"]=="intra" else 0)
        if cmpv < floorv: continue

        # Strong preference for a URL carrying the edition year and report-like pages.
        bonus=0
        if str(v[0]) in url: bonus+=3
        if re.search(r"(report|outlook|index|yearbook|statistics|world-|global-)",url,re.I): bonus+=1
        if re.search(spec["match"],text,re.I): bonus+=2
        good.append((cmpv,score+bonus,len(text),text,url,method,v))

    if not good: return None
    good.sort(reverse=True)
    _,score,_,text,url,method,v=good[0]
    return {"title":text,"url":url,"method":method,"version":v,"score":score}

def resolve_series(spec):
    errors=[]
    candidates=[]

    # 1) Direct official page.
    try:
        soup,final_url=direct_html(spec["url"])
        candidates.extend(candidates_from_html(spec,soup,final_url))
    except Exception as e:
        errors.append("direct: "+str(e)[:150])

    best=choose_candidate(spec,candidates)
    if best: return best,errors

    # 2) Jina Reader fallback (free basic use): especially useful for 403/JS pages.
    try:
        text=jina_reader(spec["url"])
        candidates.extend(candidates_from_markdown(spec,text,"reader"))
    except Exception as e:
        errors.append("reader: "+str(e)[:150])

    best=choose_candidate(spec,candidates)
    if best: return best,errors

    # 3) Search fallback restricted to the official domain and exact series.
    # We search current and previous year because some series are labelled by data year
    # but published the following year (e.g. UNHCR Global Trends, WMO climate report).
    domain=urlparse(spec["url"]).netloc.replace("www.","")
    query=f'site:{domain} "{spec["name"]}" {CURRENT_YEAR} OR {CURRENT_YEAR-1}'
    try:
        text=jina_search(query)
        candidates.extend(candidates_from_markdown(spec,text,"search"))
    except Exception as e:
        errors.append("search: "+str(e)[:150])

    best=choose_candidate(spec,candidates)
    return best,errors

def scan_series(state, migration):
    new=[]; health=[]
    for spec in SERIES:
        key="series:"+spec["id"]
        try:
            cur,errors=resolve_series(spec)
            if not cur:
                health.append({"monitor":spec["name"],"kind":"series","ok":False,
                               "error":" | ".join(errors)[:500] or "No valid edition match"})
                continue
            cur_v=cur["version"]; old=state.get(key)
            if (not migration and isinstance(old,dict) and old.get("version")):
                ov=tuple(old["version"])
                cv=(cur_v[0],cur_v[1] if spec["mode"]=="intra" else 0)
                ov_cmp=(ov[0],ov[1] if spec["mode"]=="intra" else 0)
                if cv > ov_cmp:
                    typ="Major flagship update" if spec["mode"]=="intra" and cv[0]==ov_cmp[0] else "New flagship edition"
                    nl=label(cur_v,spec["mode"]); ol=label(ov,spec["mode"])
                    new.append({
                        "id":hashlib.sha1((spec["id"]+nl+cur["url"]).encode()).hexdigest()[:14],
                        "date":TODAY,"category":spec["category"],"source":spec["org"],
                        "title":f"{spec['name']} — {nl}",
                        "summary":f"Edition-aware monitor detected a newer {spec['name']}: {nl}, replacing {ol}.",
                        "url":cur["url"],"type":typ,"automatic":True,"series_id":spec["id"],
                        "edition":nl,"previous_edition":ol,"confidence":"high"
                    })
            state[key]={"version":[cur_v[0],cur_v[1]],"edition":label(cur_v,spec["mode"]),
                        "title":cur["title"],"url":cur["url"],"method":cur["method"],
                        "checked":NOW.isoformat().replace("+00:00","Z")}
            health.append({"monitor":spec["name"],"kind":"series","ok":True,
                           "latest":state[key]["edition"],"url":cur["url"],
                           "method":cur["method"],"warnings":errors})
        except Exception as e:
            health.append({"monitor":spec["name"],"kind":"series","ok":False,"error":str(e)[:500]})
        time.sleep(0.15)
    return new,health

def discovery_links_from_html(soup,base,rx):
    out=[]
    domain=urlparse(base).netloc.replace("www.","")
    for a in soup.find_all("a",href=True):
        t=clean(a.get_text(" ",strip=True))
        if not (24<=len(t)<=230) or not rx.search(t): continue
        u=canonical(base,a["href"]); d=urlparse(u).netloc.replace("www.","")
        if d and domain and not (d==domain or d.endswith("."+domain) or domain.endswith("."+d)): continue
        if re.search(r"\b(report|outlook|index|assessment|monitor|statistics|update|launch)\b",t,re.I):
            out.append((t,u))
    return out[:60]

def scan_discovery(state,migration):
    new=[]; health=[]
    for mid,org,cat,url,pattern in DISCOVERY:
        key="discovery:"+mid; links=[]; errors=[]; rx=re.compile(pattern,re.I)
        try:
            s,final=direct_html(url); links=discovery_links_from_html(s,final,rx)
        except Exception as e:
            errors.append("direct: "+str(e)[:140])
        if not links:
            try:
                txt=jina_reader(url)
                for t,u in re.findall(r"\[([^\]]{20,240})\]\((https?://[^)\s]+)",txt):
                    if rx.search(t) and re.search(r"\b(report|outlook|index|assessment|monitor|statistics|update|launch)\b",t,re.I):
                        links.append((clean(t),u))
            except Exception as e:
                errors.append("reader: "+str(e)[:140])
        current={u for _,u in links[:60]}; old=set(state.get(key,[]))
        if old and not migration:
            for t,u in links[:60]:
                if u not in old:
                    new.append({"id":hashlib.sha1(u.encode()).hexdigest()[:14],"date":TODAY,
                                "category":cat,"source":org,"title":t,
                                "summary":"New high-signal institutional publication detected. This is discovery monitoring, not a confirmed new flagship edition.",
                                "url":u,"type":"New publication","automatic":True,"confidence":"medium"})
        state[key]=list(dict.fromkeys(list(old)+sorted(current)))[-800:]
        health.append({"monitor":org+" discovery","kind":"discovery","ok":bool(links),
                       "candidates":len(links),"warnings":errors})
    return new,health

def main():
    state=load_json(STATE_FILE,{})
    migration = state.get("_engine_version") != ENGINE_VERSION
    if migration:
        # Re-baseline silently because v3 contained known false positives
        # (e.g. ITU 2021, WDR 2015, WMO publication-year confusion).
        state["_engine_version"]=ENGINE_VERSION

    old_updates=load_json(OUTPUT_FILE,{"updates":[]}).get("updates",[])
    flagship_new,h1=scan_series(state,migration)
    discovery_new,h2=scan_discovery(state,migration)

    by={}
    for x in flagship_new+discovery_new+old_updates:
        k=x.get("series_id") or x.get("url") or x.get("id")
        if k not in by: by[k]=x
    items=list(by.values())
    items.sort(key=lambda x:(x.get("date",""),1 if "flagship" in x.get("type","").lower() else 0),reverse=True)

    ok1=sum(bool(x.get("ok")) for x in h1); ok2=sum(bool(x.get("ok")) for x in h2)
    payload={
        "last_checked":NOW.isoformat().replace("+00:00","Z"),
        "engine_version":ENGINE_VERSION,
        "monitor_count":len(SERIES)+len(DISCOVERY),
        "flagship_series_count":len(SERIES),
        "new_items_this_run":len(flagship_new)+len(discovery_new),
        "new_flagship_editions_this_run":len(flagship_new),
        "migration_baseline":migration,
        "updates":items[:160],
    }
    health={
        "last_checked":payload["last_checked"],"engine_version":ENGINE_VERSION,
        "migration_baseline":migration,
        "series":{"ok":ok1,"failed":len(h1)-ok1,"total":len(SERIES)},
        "discovery":{"ok":ok2,"failed":len(h2)-ok2,"total":len(DISCOVERY)},
        "monitors":h1+h2,
    }
    save_json(STATE_FILE,state); save_json(OUTPUT_FILE,payload); save_json(HEALTH_FILE,health)
    print(f"World Watch v4: {ok1}/{len(SERIES)} flagship series resolved; "
          f"{ok2}/{len(DISCOVERY)} discovery monitors resolved; "
          f"{len(flagship_new)} newer flagship edition(s); {len(discovery_new)} discovery item(s); "
          f"migration_baseline={migration}")

if __name__=="__main__":
    main()
