#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "monitor_state.json"
OUTPUT_FILE = ROOT / "auto_updates.json"
HEALTH_FILE = ROOT / "monitor_health.json"

NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()
CURRENT_YEAR = NOW.year
HEADERS = {
    "User-Agent": "WorldWatchPersonalDashboard/1.0",
    "Accept-Language": "en-US,en;q=0.8",
}
TIMEOUT = 25

MONITORS = [
    {"name":"IMF Publications","org":"IMF","category":"Economy & Political Economy","url":"https://www.imf.org/en/publications","include":r"\b(outlook|report|monitor|update|global|regional|AI|technology|finance|fiscal|economic)\b"},
    {"name":"World Bank News","org":"World Bank","category":"Economy & Political Economy","url":"https://www.worldbank.org/en/news/all","include":r"\b(report|prospects|outlook|global|poverty|development|economy|economic|climate|digital|AI|trade)\b"},
    {"name":"WHO News Releases","org":"WHO","category":"Health, Food & Education","url":"https://www.who.int/news-room/releases","include":r"\b(global|report|guideline|statistics|health|disease|outbreak|pandemic|mortality|cancer|dementia|vaccine)\b"},
    {"name":"CSET Publications","org":"Georgetown CSET","category":"Technology & AI","url":"https://cset.georgetown.edu/publications/","include":r"\b(AI|artificial intelligence|semiconductor|compute|technology|biotech|China|model|cyber|robot|quantum)\b"},
    {"name":"World Economic Forum Publications","org":"World Economic Forum","category":"Geopolitics & Security","url":"https://www.weforum.org/publications/","include":r"\b(report|risks|outlook|future|global|technology|AI|economy|climate|energy|jobs)\b"},
    {"name":"International Crisis Group","org":"International Crisis Group","category":"Geopolitics & Security","url":"https://www.crisisgroup.org/latest-updates","include":r"\b(conflict|war|crisis|security|peace|ceasefire|election|Iran|Ukraine|Gaza|Sudan|Syria|Taiwan|China|Russia)\b"},
    {"name":"Carbon Brief","org":"Carbon Brief","category":"Climate & Energy","url":"https://www.carbonbrief.org/","include":r"\b(climate|carbon|emissions|energy|warming|temperature|electricity|renewable|oil|gas|coal)\b"},
    {"name":"Quanta Magazine","org":"Quanta Magazine","category":"Science","url":"https://www.quantamagazine.org/","include":r"\b(AI|physics|biology|mathematics|computer|quantum|algorithm|genome|neural|cosmology|science)\b"},
    {"name":"IEA News","org":"International Energy Agency","category":"Climate & Energy","url":"https://www.iea.org/news","include":r"\b(report|outlook|energy|electricity|oil|gas|renewable|transition|emissions|AI|investment)\b"},
    {"name":"WMO News","org":"World Meteorological Organization","category":"Climate & Energy","url":"https://wmo.int/media/news","include":r"\b(climate|temperature|weather|report|record|ocean|ice|heat|El Niño|La Niña|greenhouse)\b"},
    {"name":"UNHCR News","org":"UNHCR","category":"Demography & Society","url":"https://www.unhcr.org/news","include":r"\b(refugee|displacement|displaced|asylum|global trends|stateless|migration|crisis)\b"},
    {"name":"V-Dem","org":"V-Dem Institute","category":"Democracy & Rights","url":"https://www.v-dem.net/","include":r"\b(democracy|autocra|report|dataset|election|freedom|rights)\b"},
]

FLAGSHIPS = [
    ("IMF World Economic Outlook","IMF","Economy & Political Economy","https://www.imf.org/en/Publications/WEO",r"World Economic Outlook"),
    ("World Bank Global Economic Prospects","World Bank","Economy & Political Economy","https://www.worldbank.org/en/publication/global-economic-prospects",r"Global Economic Prospects"),
    ("Stanford AI Index","Stanford HAI","Technology & AI","https://hai.stanford.edu/ai-index",r"AI Index"),
    ("V-Dem Democracy Report","V-Dem Institute","Democracy & Rights","https://www.v-dem.net/publications/democracy-reports/",r"Democracy Report"),
    ("WEF Global Risks Report","World Economic Forum","Geopolitics & Security","https://www.weforum.org/publications/series/global-risks-report/",r"Global Risks Report"),
    ("Munich Security Report","Munich Security Conference","Geopolitics & Security","https://securityconference.org/en/publications/munich-security-report/",r"Munich Security Report"),
    ("WMO State of the Global Climate","WMO","Climate & Energy","https://wmo.int/publication-series/state-of-global-climate",r"State of (the )?Global Climate"),
    ("IEA World Energy Outlook","IEA","Climate & Energy","https://www.iea.org/reports/world-energy-outlook-2025",r"World Energy Outlook"),
    ("UNDP Human Development Report","UNDP","Demography & Society","https://hdr.undp.org/",r"Human Development Report"),
    ("IOM World Migration Report","IOM","Demography & Society","https://worldmigrationreport.iom.int/",r"World Migration Report"),
]

BLOCK_TEXT = {"home","about","read more","learn more","view all","see all","news","publications","research","topics","events","subscribe","contact","previous","next","menu","search"}

def load_json(path, default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return default

def save_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def clean_text(s):
    return re.sub(r"\s+"," ",s or "").strip()

def canonical_url(base, href):
    u=urljoin(base,href)
    p=urlparse(u)
    q=p.query
    if q and any(x in q.lower() for x in ("utm_","fbclid","gclid")): q=""
    return p._replace(fragment="",query=q).geturl()

def get_soup(url):
    r=requests.get(url,headers=HEADERS,timeout=TIMEOUT,allow_redirects=True)
    r.raise_for_status()
    return BeautifulSoup(r.text,"html.parser")

def extract_candidates(soup, base_url, include_re, limit=60):
    inc=re.compile(include_re,re.I)
    out=[]; seen=set()
    base_domain=urlparse(base_url).netloc.replace("www.","")
    for a in soup.find_all("a",href=True):
        text=clean_text(a.get_text(" ",strip=True))
        if not (22 <= len(text) <= 230) or text.lower() in BLOCK_TEXT: continue
        href=canonical_url(base_url,a["href"])
        if href.startswith(("mailto:","javascript:","tel:")): continue
        dom=urlparse(href).netloc.replace("www.","")
        if base_domain and dom and not (dom==base_domain or dom.endswith("."+base_domain) or base_domain.endswith("."+dom)): continue
        if href in seen or not inc.search(text): continue
        score=0
        if re.search(r"\b(report|outlook|index|assessment|monitor|statistics|dataset|update|global|launch|release)\b",text,re.I): score+=3
        if str(CURRENT_YEAR) in text or str(CURRENT_YEAR+1) in text: score+=3
        if len(text)>=45: score+=1
        seen.add(href); out.append({"title":text,"url":href,"score":score})
    out.sort(key=lambda x:(-x["score"],x["title"]))
    return out[:limit]

def detect_page_updates(state):
    new=[]; health=[]
    for mon in MONITORS:
        key="page:"+mon["name"]
        try:
            soup=get_soup(mon["url"])
            candidates=extract_candidates(soup,mon["url"],mon["include"])
            current={x["url"] for x in candidates}
            old=set(state.get(key,[]))
            if old:
                for c in candidates:
                    if c["url"] not in old:
                        new.append({
                            "id":hashlib.sha1(c["url"].encode()).hexdigest()[:14],
                            "date":TODAY,"category":mon["category"],"source":mon["org"],
                            "title":c["title"],
                            "summary":f"Automatically detected as a new high-signal item on {mon['name']}.",
                            "url":c["url"],"type":"Auto-detected","automatic":True
                        })
            state[key]=list(dict.fromkeys(list(old)+list(current)))[-500:]
            health.append({"monitor":mon["name"],"ok":True,"candidates":len(candidates)})
        except Exception as e:
            health.append({"monitor":mon["name"],"ok":False,"error":str(e)[:180]})
    return new,health

def detect_flagships(state):
    new=[]; health=[]
    for name,org,category,url,pattern in FLAGSHIPS:
        key="flagship:"+name
        try:
            soup=get_soup(url); rx=re.compile(pattern,re.I); matches=[]
            for a in soup.find_all("a",href=True):
                text=clean_text(a.get_text(" ",strip=True))
                if not text or not rx.search(text): continue
                href=canonical_url(url,a["href"])
                if href.startswith(("mailto:","javascript:")): continue
                matches.append((text,href))
            title=clean_text(soup.title.get_text(" ",strip=True) if soup.title else "")
            if rx.search(title): matches.append((title,url))
            uniq=[]; seen=set()
            for text,href in matches:
                sig=text+"|"+href
                if sig not in seen: seen.add(sig); uniq.append((text,href))
            uniq.sort(key=lambda p:(0 if (str(CURRENT_YEAR) in p[0] or str(CURRENT_YEAR+1) in p[0]) else 1,-len(p[0])))
            if uniq:
                text,href=uniq[0]
                signature=hashlib.sha1((text+"|"+href).encode()).hexdigest()
                old=state.get(key)
                if old and signature!=old:
                    new.append({
                        "id":hashlib.sha1(("flagship|"+href+"|"+text).encode()).hexdigest()[:14],
                        "date":TODAY,"category":category,"source":org,"title":text,
                        "summary":f"World Watch detected a change in the {name} series. Check whether this is a new edition or substantive update.",
                        "url":href,"type":"Flagship monitor","automatic":True
                    })
                state[key]=signature
                health.append({"monitor":name,"ok":True,"match":text[:120]})
            else:
                health.append({"monitor":name,"ok":False,"error":"No matching series title found"})
        except Exception as e:
            health.append({"monitor":name,"ok":False,"error":str(e)[:180]})
    return new,health

def main():
    state=load_json(STATE_FILE,{})
    old_output=load_json(OUTPUT_FILE,{"updates":[]})
    existing=old_output.get("updates",[])
    page_new,h1=detect_page_updates(state)
    flagship_new,h2=detect_flagships(state)
    by_url={}
    for item in page_new+flagship_new+existing:
        by_url.setdefault(item["url"],item)
    items=list(by_url.values())
    items.sort(key=lambda x:(x.get("date",""),x.get("id","")),reverse=True)
    items=items[:120]
    payload={"last_checked":NOW.isoformat().replace("+00:00","Z"),"monitor_count":len(MONITORS)+len(FLAGSHIPS),"new_items_this_run":len(page_new)+len(flagship_new),"updates":items}
    health={"last_checked":payload["last_checked"],"ok":sum(1 for x in h1+h2 if x.get("ok")),"failed":sum(1 for x in h1+h2 if not x.get("ok")),"monitors":h1+h2}
    save_json(STATE_FILE,state); save_json(OUTPUT_FILE,payload); save_json(HEALTH_FILE,health)
    print(f"Checked {payload['monitor_count']} monitors; {payload['new_items_this_run']} new item(s); {health['failed']} failures.")

if __name__=="__main__":
    main()
