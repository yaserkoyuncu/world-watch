#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
STATE=ROOT/"monitor_state.json"; OUT=ROOT/"auto_updates.json"; HEALTH=ROOT/"monitor_health.json"
NOW=datetime.now(timezone.utc); TODAY=NOW.date().isoformat(); YEAR=NOW.year
HEAD={"User-Agent":"Mozilla/5.0 (compatible; WorldWatch/2.0)","Accept-Language":"en-US,en;q=0.8"}
MONTHS={"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,"july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
        "jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,"aug":8,"sep":9,"sept":9,"oct":10,"nov":11,"dec":12}

# id|name|org|category|mode|url|regex
RAW=r"""
imf_weo|World Economic Outlook|IMF|Economy & Political Economy|intra|https://www.imf.org/en/Publications/WEO|\bWorld Economic Outlook\b
imf_gfsr|Global Financial Stability Report|IMF|Economy & Political Economy|intra|https://www.imf.org/en/Publications/GFSR|\bGlobal Financial Stability Report\b
imf_fiscal|Fiscal Monitor|IMF|Economy & Political Economy|intra|https://www.imf.org/en/Publications/FM|\bFiscal Monitor\b
worldbank_gep|Global Economic Prospects|World Bank|Economy & Political Economy|intra|https://www.worldbank.org/en/publication/global-economic-prospects|\bGlobal Economic Prospects\b
worldbank_wdr|World Development Report|World Bank|Economy & Political Economy|annual|https://www.worldbank.org/en/publication/wdr|\bWorld Development Report\b
oecd_eo|OECD Economic Outlook|OECD|Economy & Political Economy|intra|https://www.oecd.org/en/topics/economic-outlook.html|\b(?:OECD )?Economic Outlook\b
bis_aer|Annual Economic Report|BIS|Economy & Political Economy|annual|https://www.bis.org/list/annualreport/index.htm|\bAnnual Economic Report\b
unctad_tdr|Trade and Development Report|UNCTAD|Economy & Political Economy|annual|https://unctad.org/topic/macroeconomics/trade-development-report|\bTrade and Development Report\b
wto_gto|Global Trade Outlook and Statistics|WTO|Economy & Political Economy|intra|https://www.wto.org/english/res_e/statis_e/trade_outlook_e.htm|\bGlobal Trade Outlook and Statistics\b
ilo_est|Employment and Social Trends|ILO|Economy & Political Economy|annual|https://www.ilo.org/publications/flagship-reports|\bEmployment and Social Trends\b
wef_grr|Global Risks Report|World Economic Forum|Geopolitics & Security|annual|https://www.weforum.org/publications/series/global-risks-report/|\bGlobal Risks Report\b
msc_msr|Munich Security Report|Munich Security Conference|Geopolitics & Security|annual|https://securityconference.org/en/publications/munich-security-report/|\bMunich Security Report\b
sipri_yearbook|SIPRI Yearbook|SIPRI|Geopolitics & Security|annual|https://www.sipri.org/yearbook|\bSIPRI Yearbook\b
odni_ata|Annual Threat Assessment|ODNI|Geopolitics & Security|annual|https://www.odni.gov/index.php/newsroom/reports-publications|\bAnnual Threat Assessment\b
stanford_ai|AI Index|Stanford HAI|Technology & AI|annual|https://hai.stanford.edu/ai-index|\bAI Index\b
mck_tech|Technology Trends Outlook|McKinsey|Technology & AI|annual|https://www.mckinsey.com/capabilities/tech-and-ai/our-insights/the-top-trends-in-tech/|\bTechnology Trends Outlook\b|\btechnology trends.*20\d{2}\b
mck_ai|State of AI|McKinsey|Technology & AI|annual|https://www.mckinsey.com/capabilities/quantumblack/our-insights/the-state-of-ai/|\b(?:The )?State of AI\b
deloitte_tech|Tech Trends|Deloitte|Technology & AI|annual|https://www.deloitte.com/us/en/insights/topics/technology-management/tech-trends.html|\bTech Trends\b
kpmg_tech|Global Tech Report|KPMG|Technology & AI|annual|https://kpmg.com/uk/en/insights/technology/kpmg-global-tech-report.html|\bGlobal Tech Report\b
wipo_gii|Global Innovation Index|WIPO|Technology & AI|annual|https://www.wipo.int/en/web/global-innovation-index/|\bGlobal Innovation Index\b
itu_ff|Facts and Figures|ITU|Technology & AI|annual|https://www.itu.int/itu-d/reports/statistics/facts-figures/|\bFacts and Figures\b
wmo_climate|State of the Global Climate|WMO|Climate & Energy|annual|https://wmo.int/publication-series/state-of-global-climate|\bState of (?:the )?Global Climate\b
unep_egr|Emissions Gap Report|UNEP|Climate & Energy|annual|https://www.unep.org/resources/emissions-gap-report|\bEmissions Gap Report\b
unep_agr|Adaptation Gap Report|UNEP|Climate & Energy|annual|https://www.unep.org/resources/adaptation-gap-report|\bAdaptation Gap Report\b
iea_weo|World Energy Outlook|IEA|Climate & Energy|annual|https://www.iea.org/reports|\bWorld Energy Outlook\b
irena_weto|World Energy Transitions Outlook|IRENA|Climate & Energy|annual|https://www.irena.org/Energy-Transition/Outlook|\bWorld Energy Transitions Outlook\b
gcb|Global Carbon Budget|Global Carbon Project|Climate & Energy|annual|https://globalcarbonbudget.org/|\bGlobal Carbon Budget\b
un_wpp|World Population Prospects|UN DESA|Demography & Society|periodic|https://www.un.org/development/desa/pd/world-population-prospects|\bWorld Population Prospects\b
un_wup|World Urbanization Prospects|UN DESA|Demography & Society|periodic|https://www.un.org/development/desa/pd/world-urbanization-prospects|\bWorld Urbanization Prospects\b
iom_wmr|World Migration Report|IOM|Demography & Society|periodic|https://worldmigrationreport.iom.int/|\bWorld Migration Report\b
unhcr_gt|Global Trends|UNHCR|Demography & Society|annual|https://www.unhcr.org/global-trends|\bGlobal Trends\b
undp_hdr|Human Development Report|UNDP|Demography & Society|periodic|https://hdr.undp.org/|\bHuman Development Report\b
un_social|World Social Report|UN DESA|Demography & Society|periodic|https://social.desa.un.org/issues/world-social-report|\bWorld Social Report\b
happiness|World Happiness Report|Wellbeing Research Centre|Demography & Society|annual|https://www.worldhappiness.report/|\bWorld Happiness Report\b
who_whs|World Health Statistics|WHO|Health, Food & Education|annual|https://www.who.int/data/gho/publications/world-health-statistics|\bWorld Health Statistics\b
fao_sofi|State of Food Security and Nutrition in the World|FAO et al.|Health, Food & Education|annual|https://www.fao.org/publications/fao-flagship-publications/the-state-of-food-security-and-nutrition-in-the-world/en|\b(?:The )?State of Food Security and Nutrition in the World\b
unesco_gem|Global Education Monitoring Report|UNESCO|Health, Food & Education|annual|https://www.unesco.org/gem-report/en|\bGlobal Education Monitoring Report\b|\bGEM Report\b
undp_mpi|Global Multidimensional Poverty Index|UNDP / OPHI|Health, Food & Education|annual|https://hdr.undp.org/mpi|\b(?:Global )?Multidimensional Poverty Index\b|\bGlobal MPI\b
vdem|Democracy Report|V-Dem Institute|Democracy & Rights|annual|https://www.v-dem.net/publications/democracy-reports/|\bDemocracy Report\b
idea|Global State of Democracy|International IDEA|Democracy & Rights|annual|https://www.idea.int/gsod/|\bGlobal State of Democracy\b
freedom|Freedom in the World|Freedom House|Democracy & Rights|annual|https://freedomhouse.org/report/freedom-world|\bFreedom in the World\b
wjp|Rule of Law Index|World Justice Project|Democracy & Rights|annual|https://worldjusticeproject.org/rule-of-law-index/|\bRule of Law Index\b
cpi|Corruption Perceptions Index|Transparency International|Democracy & Rights|annual|https://www.transparency.org/en/cpi|\bCorruption Perceptions Index\b
rsf|World Press Freedom Index|Reporters Without Borders|Democracy & Rights|annual|https://rsf.org/en/index|\bWorld Press Freedom Index\b
"""
SERIES=[]
for line in RAW.strip().splitlines():
    i,n,o,c,m,u,r=line.split("|",6)
    SERIES.append(dict(id=i,name=n,org=o,category=c,mode=m,url=u,match=r))

DISCOVERY=[
("imf","IMF","Economy & Political Economy","https://www.imf.org/en/publications",r"\b(report|outlook|monitor|update|AI|global|financial|fiscal)\b"),
("wb","World Bank","Economy & Political Economy","https://www.worldbank.org/en/news/all",r"\b(report|prospects|outlook|poverty|development|economic|climate|digital|AI|trade)\b"),
("who","WHO","Health, Food & Education","https://www.who.int/news-room/releases",r"\b(global|report|statistics|health|disease|outbreak|mortality|vaccine)\b"),
("cset","Georgetown CSET","Technology & AI","https://cset.georgetown.edu/publications/",r"\b(AI|semiconductor|compute|technology|biotech|China|cyber|robot|quantum)\b"),
("icg","International Crisis Group","Geopolitics & Security","https://www.crisisgroup.org/latest-updates",r"\b(conflict|war|crisis|security|peace|ceasefire|election)\b"),
("cb","Carbon Brief","Climate & Energy","https://www.carbonbrief.org/",r"\b(climate|carbon|emissions|energy|warming|temperature|renewable|oil|gas|coal)\b"),
("quanta","Quanta Magazine","Science","https://www.quantamagazine.org/",r"\b(AI|physics|biology|mathematics|computer|quantum|algorithm|genome|cosmology)\b"),
]

def load(p,d):
    try:return json.loads(p.read_text(encoding="utf-8"))
    except:return d
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding="utf-8")
def clean(x):return re.sub(r"\s+"," ",x or "").strip()
def canon(base,href):
    u=urljoin(base,href); p=urlparse(u); q=p.query
    if q and any(x in q.lower() for x in ("utm_","fbclid","gclid")):q=""
    return p._replace(fragment="",query=q).geturl()
def soup(url):
    r=requests.get(url,headers=HEAD,timeout=30,allow_redirects=True);r.raise_for_status()
    return BeautifulSoup(r.text,"html.parser")
def version(text,url,mode):
    blob=clean(text+" "+url)
    ys=[int(x) for x in re.findall(r"\b(20\d{2})\b",blob) if 2000<=int(x)<=YEAR+2]
    if not ys:return None
    y=max(ys); low=blob.lower(); ms=[n for k,n in MONTHS.items() if re.search(rf"\b{re.escape(k)}\.?\b",low)]
    return {"year":y,"month":max(ms) if (ms and mode=="intra") else 0}
def vt(v,mode):return (v["year"],v.get("month",0) if mode=="intra" else 0)
def label(v,mode):
    if mode=="intra" and v.get("month"):
        names=["","January","February","March","April","May","June","July","August","September","October","November","December"]
        return f"{names[v['month']]} {v['year']}"
    return str(v["year"])
def nodes(s,base):
    if s.title:
        t=clean(s.title.get_text(" ",strip=True))
        if t:yield t,base,8
    for h in s.find_all(["h1","h2","h3","h4"]):
        t=clean(h.get_text(" ",strip=True))
        if 8<=len(t)<=280:yield t,base,7
    for a in s.find_all("a",href=True):
        t=clean(a.get_text(" ",strip=True))
        if not 8<=len(t)<=260:continue
        u=canon(base,a["href"])
        par=a.find_parent(["article","li","div"]); ctx=clean(par.get_text(" ",strip=True))[:450] if par else ""
        yield clean(t+" "+ctx),u,5

def latest(spec):
    s=soup(spec["url"]); rx=re.compile(spec["match"],re.I); cand=[]
    for text,url,w in nodes(s,spec["url"]):
        if not rx.search(text):continue
        # Avoid UNCTAD "Foresights" being mistaken for TDR.
        if spec["id"]=="unctad_tdr" and re.search(r"\bForesights?\b",text,re.I):continue
        v=version(text,url,spec["mode"])
        if not v:continue
        score=w+(2 if str(v["year"]) in url else 0)
        cand.append((vt(v,spec["mode"]),score,len(text),v,clean(text)[:300],url))
    if not cand:return None
    cand.sort(reverse=True)
    _,_,_,v,title,url=cand[0]
    return {"version":v,"title":title,"url":url}

def scan_series(state):
    new=[]; health=[]
    for sp in SERIES:
        key="series:"+sp["id"]
        try:
            cur=latest(sp)
            if not cur:
                health.append({"monitor":sp["name"],"kind":"series","ok":False,"error":"No edition-bearing match"})
                continue
            old=state.get(key)
            if isinstance(old,dict) and old.get("version") and vt(cur["version"],sp["mode"])>vt(old["version"],sp["mode"]):
                typ="Major flagship update" if sp["mode"]=="intra" and cur["version"]["year"]==old["version"]["year"] else "New flagship edition"
                nl=label(cur["version"],sp["mode"]); ol=label(old["version"],sp["mode"])
                new.append({"id":hashlib.sha1((sp["id"]+nl+cur["url"]).encode()).hexdigest()[:14],
                    "date":TODAY,"category":sp["category"],"source":sp["org"],
                    "title":f"{sp['name']} — {nl}",
                    "summary":f"Edition-aware monitor detected a newer {sp['name']}: {nl}, replacing {ol}.",
                    "url":cur["url"],"type":typ,"automatic":True,"series_id":sp["id"],
                    "edition":nl,"previous_edition":ol,"confidence":"high"})
            state[key]={"version":cur["version"],"edition":label(cur["version"],sp["mode"]),"title":cur["title"],"url":cur["url"],"checked":NOW.isoformat()}
            health.append({"monitor":sp["name"],"kind":"series","ok":True,"latest":state[key]["edition"],"url":cur["url"]})
        except Exception as e:
            health.append({"monitor":sp["name"],"kind":"series","ok":False,"error":str(e)[:240]})
    return new,health

def scan_discovery(state):
    new=[]; health=[]
    for mid,org,cat,url,pat in DISCOVERY:
        key="discovery:"+mid
        try:
            s=soup(url); rx=re.compile(pat,re.I); cur=[]
            domain=urlparse(url).netloc.replace("www.","")
            for a in s.find_all("a",href=True):
                t=clean(a.get_text(" ",strip=True))
                if not 24<=len(t)<=220 or not rx.search(t):continue
                u=canon(url,a["href"]); d=urlparse(u).netloc.replace("www.","")
                if d and domain and not (d==domain or d.endswith("."+domain) or domain.endswith("."+d)):continue
                if re.search(r"\b(report|outlook|index|assessment|monitor|statistics|update|launch)\b",t,re.I):cur.append((t,u))
            seen_now={u for _,u in cur[:60]}; old=set(state.get(key,[]))
            if old:
                for t,u in cur[:60]:
                    if u not in old:
                        new.append({"id":hashlib.sha1(u.encode()).hexdigest()[:14],"date":TODAY,"category":cat,"source":org,
                            "title":t,"summary":"New high-signal institutional publication detected. This is discovery monitoring, not a confirmed new flagship edition.",
                            "url":u,"type":"New publication","automatic":True,"confidence":"medium"})
            state[key]=list(dict.fromkeys(list(old)+sorted(seen_now)))[-800:]
            health.append({"monitor":org+" discovery","kind":"discovery","ok":True,"candidates":len(seen_now)})
        except Exception as e:
            health.append({"monitor":org+" discovery","kind":"discovery","ok":False,"error":str(e)[:240]})
    return new,health

def main():
    state=load(STATE,{})
    old=load(OUT,{"updates":[]}).get("updates",[])
    fn,h1=scan_series(state); dn,h2=scan_discovery(state)
    by={}
    for x in fn+dn+old:
        k=x.get("series_id") or x.get("url") or x.get("id")
        if k not in by:by[k]=x
    items=list(by.values());items.sort(key=lambda x:(x.get("date",""),1 if "flagship" in x.get("type","").lower() else 0),reverse=True)
    ok1=sum(x.get("ok",False) for x in h1);ok2=sum(x.get("ok",False) for x in h2)
    payload={"last_checked":NOW.isoformat().replace("+00:00","Z"),"monitor_count":len(SERIES)+len(DISCOVERY),
             "flagship_series_count":len(SERIES),"new_items_this_run":len(fn)+len(dn),
             "new_flagship_editions_this_run":len(fn),"updates":items[:160]}
    health={"last_checked":payload["last_checked"],"series":{"ok":ok1,"failed":len(h1)-ok1,"total":len(SERIES)},
            "discovery":{"ok":ok2,"failed":len(h2)-ok2,"total":len(DISCOVERY)},"monitors":h1+h2}
    save(STATE,state);save(OUT,payload);save(HEALTH,health)
    print(f"Edition-aware World Watch: {ok1}/{len(SERIES)} flagship series resolved; {ok2}/{len(DISCOVERY)} discovery monitors resolved; {len(fn)} newer flagship edition(s); {len(dn)} discovery item(s).")

if __name__=="__main__":main()
