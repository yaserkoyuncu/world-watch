#!/usr/bin/env python3
"""
World Watch v6.1 — reliability patch

V6 architecture:
- Direct official-page fetch first.
- Layer 1: 44 edition-aware flagship report series.
- Layer 2: expanded high-signal discovery across 38 research/institutional sources.
- Free Jina Reader fallback for blocked / JS-heavy pages.
- IMF flagship series use the official IMF eLibrary to avoid IMF.org 403 blocks.
- Strict title/link-based edition extraction (avoids using unrelated page dates).
- Rejects announcements, concept notes, "towards..." pages, future editions and methodology pages.
- Nearby-line parsing captures dates separated from report titles.
- Release validation blocks WDR concept notes, WIPO save-the-date pages and GCB footer-year false positives.
- One-time migration baseline prevents false "NEW" alerts when correcting old state.
"""

from __future__ import annotations
import hashlib, json, re, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "monitor_state.json"
OUTPUT_FILE = ROOT / "auto_updates.json"
HEALTH_FILE = ROOT / "monitor_health.json"

ENGINE_VERSION = "6.1"
NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()
CURRENT_YEAR = NOW.year

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WorldWatchPersonalDashboard/6.1)",
    "Accept-Language": "en-US,en;q=0.8",
}
TIMEOUT = 22

MONTHS = {
    "january":1,"jan":1,"february":2,"feb":2,"march":3,"mar":3,"april":4,"apr":4,
    "may":5,"june":6,"jun":6,"july":7,"jul":7,"august":8,"aug":8,
    "september":9,"sep":9,"sept":9,"october":10,"oct":10,"november":11,"nov":11,
    "december":12,"dec":12,
}

# id|name|org|category|mode|official landing URL|matching expression
RAW = r"""
imf_weo|World Economic Outlook|IMF|Economy & Political Economy|intra|https://www.imf.org/en/publications|\bWorld Economic Outlook\b
imf_gfsr|Global Financial Stability Report|IMF|Economy & Political Economy|intra|https://www.imf.org/en/publications|\bGlobal Financial Stability Report\b
imf_fiscal|Fiscal Monitor|IMF|Economy & Political Economy|intra|https://www.imf.org/en/publications|\bFiscal Monitor\b
worldbank_gep|Global Economic Prospects|World Bank|Economy & Political Economy|intra|https://www.worldbank.org/en/publication/global-economic-prospects|\bGlobal Economic Prospects\b
worldbank_wdr|World Development Report|World Bank|Economy & Political Economy|annual|https://www.worldbank.org/en/publication/wdr/wdr-archive|\bWorld Development Report\b
oecd_eo|OECD Economic Outlook|OECD|Economy & Political Economy|intra|https://www.oecd.org/en/topics/economic-outlook.html|\b(?:OECD )?Economic Outlook\b
bis_aer|Annual Economic Report|BIS|Economy & Political Economy|annual|https://www.bis.org/publ/arpdf/ar2026e.htm|\bAnnual Economic Report\b
unctad_tdr|Trade and Development Report|UNCTAD|Economy & Political Economy|annual|https://unctad.org/topic/macroeconomics/trade-development-report|\bTrade and Development Report\b
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
wipo_gii|Global Innovation Index|WIPO|Technology & AI|annual|https://www.wipo.int/publications/en/series/index.jsp?id=129&lang=en|\bGlobal Innovation Index\b
itu_ff|Facts and Figures|ITU|Technology & AI|annual|https://www.itu.int/itu-d/reports/statistics/facts-figures-2025/|\bFacts and Figures\b
wmo_climate|State of the Global Climate|WMO|Climate & Energy|annual|https://wmo.int/publication-series/state-of-global-climate/state-of-global-climate-2025|\bState of (?:the )?Global Climate\b
unep_egr|Emissions Gap Report|UNEP|Climate & Energy|annual|https://www.unep.org/resources/emissions-gap-report-2025|\bEmissions Gap Report\b
unep_agr|Adaptation Gap Report|UNEP|Climate & Energy|annual|https://www.unep.org/resources/adaptation-gap-report-2025|\bAdaptation Gap Report\b
iea_weo|World Energy Outlook|IEA|Climate & Energy|annual|https://www.iea.org/reports/world-energy-outlook-2025|\bWorld Energy Outlook\b
irena_weto|World Energy Transitions Outlook|IRENA|Climate & Energy|annual|https://www.irena.org/publications/2024/Nov/World-Energy-Transitions-Outlook-2024|\bWorld Energy Transitions Outlook\b
gcb|Global Carbon Budget|Global Carbon Project|Climate & Energy|annual|https://globalcarbonbudget.org/gcb-2025/|\bGlobal Carbon Budget\b
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


# Alternate OFFICIAL pages. V6 avoids generic search APIs and stays on official
# institutional domains (plus IMF's official eLibrary).
ALT_URLS = {
    "imf_weo": [
        "https://www.imf.org/en/research",
        "https://www.imf.org/en/publications/weo/issues/2026/07/08/world-economic-outlook-update-july-2026",
        "https://www.imf.org/en/publications/weo/issues/2026/04/14/world-economic-outlook-april-2026",
    ],
    "imf_gfsr": [
        "https://www.imf.org/en/publications/gfsr/issues/2026/04/14/global-financial-stability-report-april-2026",
    ],
    "imf_fiscal": [
        "https://www.imf.org/en/publications/fm/issues/2026/04/15/fiscal-monitor-april-2026",
    ],
    "worldbank_gep": [
        "https://www.worldbank.org/en/research",
        "https://www.worldbank.org/en/publication/global-economic-prospects",
    ],
    "worldbank_wdr": ["https://www.worldbank.org/en/publication/wdr2025"],
    "unctad_tdr": [
        "https://unctad.org/publication/trade-and-development-report-2025",
        "https://unctad.org/publications",
        "https://unctad.org/unctad-publications",
    ],
    "ilo_est": ["https://www.ilo.org/research-and-publications"],
    "odni_ata": [
        "https://www.dni.gov/index.php/newsroom/reports-publications",
        "https://www.dni.gov/index.php/newsroom/press-releases/press-releases-2026/4142-pr-03-26",
    ],
    "mck_tech": [
        "https://www.mckinsey.com/capabilities/mckinsey-digital/our-insights/the-top-trends-in-tech",
    ],
    "mck_ai": [
        "https://www.mckinsey.com/capabilities/quantumblack/our-insights/the-state-of-ai",
    ],
    "iea_weo": [
        "https://www.iea.org/reports",
        "https://www.iea.org/reports/world-energy-outlook-2025/overview-and-key-findings",
    ],
    "irena_weto": ["https://www.irena.org/Energy-Transition/Outlook"],
    "gcb": [
        "https://globalcarbonbudget.org/",
        "https://globalcarbonbudget.org/datahub/the-latest-gcb-data-2025/",
    ],
    "unhcr_gt": [
        "https://www.unhcr.org/media/global-trends-2025-report",
        "https://www.unhcr.org/global-trends",
    ],
    "wjp": [
        "https://worldjusticeproject.org/rule-of-law-index/global/2025/wjp-index",
        "https://worldjusticeproject.org/news/wjp-rule-law-index-2025-global-press-release",
    ],
}

# Reader-first avoids wasting ~20 seconds on hosts that consistently time out or
# block GitHub runners but are readable through the public text renderer.
READER_FIRST = {"imf_weo","imf_gfsr","imf_fiscal","mck_tech","mck_ai","unhcr_gt","cpi","wef_grr","happiness"}

# A report-specific official URL can establish a baseline even if HTML is blocked.
SELF_URL_SAFE = {
    "imf_weo","imf_gfsr","imf_fiscal","bis_aer","unctad_tdr","wto_gto","ilo_est","wef_grr","msc_msr","sipri_yearbook",
    "odni_ata","stanford_ai","itu_ff","wmo_climate","unep_egr","unep_agr",
    "iea_weo","irena_weto","gcb","un_wpp","un_wup","iom_wmr","undp_hdr",
    "happiness","who_whs","undp_mpi","idea","freedom","cpi","rsf","wjp"
}

_RELEASE_CACHE = {}

# These series are frequently published in the following calendar year. Their
# edition year must be explicitly tied to the report name or to a report-specific URL.
STRICT_EDITION_YEAR = {"wmo_climate", "unhcr_gt", "un_social", "idea", "cpi"}

# IMF issue URL paths are stable enough to identify series even when link text is
# merely "Latest Issue".
IMF_ISSUE_HINTS = {
    "imf_weo": "/publications/weo/issues/",
    "imf_gfsr": "/publications/gfsr/issues/",
    "imf_fiscal": "/publications/fm/issues/",
}

# Shared Jina Reader cache/rate limiter. The public reader was returning HTTP 429
# when Layer 2 launched many requests at once.
_JINA_CACHE = {}
_JINA_LOCK = threading.Lock()
_JINA_LAST_CALL = 0.0
_JINA_MIN_INTERVAL = 1.8

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
    # Economy / political economy
    {"id":"imf","org":"IMF","category":"Economy & Political Economy","url":"https://www.imf.org/en/publications","reader_first":True,"include":r"\b(econom|fiscal|financial|trade|debt|inflation|growth|AI|climate|development)\b","mode":"institutional"},
    {"id":"wb","org":"World Bank","category":"Economy & Political Economy","url":"https://www.worldbank.org/en/research","include":r"\b(development|poverty|econom|trade|debt|climate|digital|AI|labor|migration|education|health|governance)\b","mode":"institutional"},
    {"id":"oecd","org":"OECD","category":"Economy & Political Economy","url":"https://www.oecd.org/en/publications.html","include":r"\b(econom|employment|tax|trade|AI|digital|climate|education|health|migration|governance|finance|productivity)\b","mode":"institutional"},
    {"id":"bis","org":"BIS","category":"Economy & Political Economy","url":"https://www.bis.org/","include":r"\b(financial|monetary|bank|credit|markets|payments|crypto|CBDC|AI|econom)\b","mode":"institutional"},
    {"id":"unctad","org":"UNCTAD","category":"Economy & Political Economy","url":"https://unctad.org/publications","include":r"\b(trade|investment|finance|debt|development|technology|AI|digital|shipping|commodit)\b","mode":"institutional"},
    {"id":"wto","org":"WTO","category":"Economy & Political Economy","url":"https://www.wto.org/english/res_e/publications_e/publications_e.htm","include":r"\b(trade|tariff|services|goods|supply chain|digital|AI|development)\b","mode":"institutional"},
    {"id":"ilo","org":"ILO","category":"Economy & Political Economy","url":"https://www.ilo.org/research-and-publications","include":r"\b(employment|labou?r|work|wage|skills|AI|migration|social protection|productivity)\b","mode":"institutional"},
    {"id":"wef","org":"World Economic Forum","category":"Economy & Political Economy","url":"https://www.weforum.org/publications/","include":r"\b(global|econom|risk|technology|AI|jobs|energy|climate|trade|cyber|future)\b","mode":"institutional"},

    # Geopolitics / security
    {"id":"msc","org":"Munich Security Conference","category":"Geopolitics & Security","url":"https://securityconference.org/en/publications/","include":r"\b(security|conflict|war|geopolit|defen[cs]e|Europe|China|Russia|Middle East)\b","mode":"institutional"},
    {"id":"sipri","org":"SIPRI","category":"Geopolitics & Security","url":"https://www.sipri.org/publications","include":r"\b(arms|military|peace|conflict|nuclear|security|defen[cs]e|weapons|sanctions)\b","mode":"institutional"},
    {"id":"odni","org":"ODNI","category":"Geopolitics & Security","url":"https://www.dni.gov/index.php/newsroom/reports-publications","include":r"\b(threat|intelligence|security|cyber|China|Russia|Iran|terror|technology)\b","mode":"institutional"},
    {"id":"icg","org":"International Crisis Group","category":"Geopolitics & Security","url":"https://www.crisisgroup.org/latest-updates","include":r"\b(conflict|war|crisis|security|peace|ceasefire|election|Gaza|Ukraine|Sudan|Syria|Iran)\b","mode":"analysis"},

    # Technology / AI
    {"id":"stanford_hai","org":"Stanford HAI","category":"Technology & AI","url":"https://hai.stanford.edu/research","include":r"\b(AI|artificial intelligence|model|compute|governance|robot|automation|foundation model)\b","mode":"institutional"},
    {"id":"cset","org":"Georgetown CSET","category":"Technology & AI","url":"https://cset.georgetown.edu/publications/","include":r"\b(AI|artificial intelligence|semiconductor|compute|technology|biotech|China|cyber|robot|quantum)\b","mode":"institutional"},
    {"id":"mck","org":"McKinsey","category":"Technology & AI","url":"https://www.mckinsey.com/capabilities/tech-and-ai/our-insights","include":r"\b(AI|artificial intelligence|technology|digital|automation|quantum|cyber|semiconductor|robot)\b","mode":"institutional","reader_first":True},
    {"id":"deloitte","org":"Deloitte","category":"Technology & AI","url":"https://www.deloitte.com/us/en/services/consulting/collections/technology-insights.html","include":r"\b(AI|technology|digital|cyber|cloud|quantum|automation|data|semiconductor)\b","mode":"institutional"},
    {"id":"kpmg","org":"KPMG","category":"Technology & AI","url":"https://kpmg.com/xx/en/our-insights/ai-and-technology.html","include":r"\b(AI|technology|digital|cyber|cloud|data|automation|quantum)\b","mode":"institutional"},
    {"id":"wipo","org":"WIPO","category":"Technology & AI","url":"https://www.wipo.int/publications/en/","include":r"\b(innovation|patent|technology|AI|intellectual property|creative|digital)\b","mode":"institutional"},
    {"id":"itu","org":"ITU","category":"Technology & AI","url":"https://www.itu.int/itu-d/reports/statistics/","include":r"\b(digital|internet|connectivity|ICT|AI|broadband|mobile|statistics)\b","mode":"institutional"},

    # Climate / energy
    {"id":"wmo","org":"WMO","category":"Climate & Energy","url":"https://wmo.int/resources/publications","include":r"\b(climate|weather|temperature|greenhouse|ocean|cryosphere|water|extreme)\b","mode":"institutional"},
    {"id":"unep","org":"UNEP","category":"Climate & Energy","url":"https://www.unep.org/resources","include":r"\b(climate|emission|adaptation|environment|pollution|nature|biodiversity|energy|methane)\b","mode":"institutional"},
    {"id":"iea","org":"IEA","category":"Climate & Energy","url":"https://www.iea.org/reports","include":r"\b(energy|oil|gas|coal|electricity|renewable|nuclear|hydrogen|emission|critical mineral)\b","mode":"institutional"},
    {"id":"irena","org":"IRENA","category":"Climate & Energy","url":"https://www.irena.org/Publications","include":r"\b(renewable|energy transition|solar|wind|hydrogen|power|climate|electrification)\b","mode":"institutional"},
    {"id":"carbonbrief","org":"Carbon Brief","category":"Climate & Energy","url":"https://www.carbonbrief.org/","include":r"\b(climate|carbon|emission|energy|warming|temperature|renewable|oil|gas|coal)\b","mode":"analysis"},

    # Demography / migration / society / development
    {"id":"undesa_pop","org":"UN DESA Population Division","category":"Demography & Society","url":"https://www.un.org/development/desa/pd/content/new-publications","include":r"\b(population|fertility|mortality|migration|urbanization|ageing|household|demographic)\b","mode":"institutional"},
    {"id":"iom","org":"IOM","category":"Demography & Society","url":"https://publications.iom.int/","include":r"\b(migration|migrant|displacement|mobility|diaspora|remittance|border)\b","mode":"institutional"},
    {"id":"unhcr","org":"UNHCR","category":"Demography & Society","url":"https://www.unhcr.org/publications","include":r"\b(refugee|displacement|asylum|stateless|forced displacement|migration)\b","mode":"institutional","reader_first":True},
    {"id":"undp","org":"UNDP","category":"Demography & Society","url":"https://www.undp.org/publications","include":r"\b(human development|poverty|inequality|governance|climate|digital|AI|gender|development)\b","mode":"institutional"},

    # Health / food / education
    {"id":"who","org":"WHO","category":"Health, Food & Education","url":"https://www.who.int/publications/i","include":r"\b(health|disease|mortality|vaccine|pandemic|nutrition|mental health|antimicrobial|tuberculosis|malaria)\b","mode":"institutional"},
    {"id":"fao","org":"FAO","category":"Health, Food & Education","url":"https://www.fao.org/publications/home/en","include":r"\b(food|agriculture|nutrition|hunger|fisher|forest|commodity|climate|rural)\b","mode":"institutional"},
    {"id":"unesco","org":"UNESCO","category":"Health, Food & Education","url":"https://www.unesco.org/en/publications","include":r"\b(education|science|culture|AI|digital|literacy|teacher|learning|media)\b","mode":"institutional"},

    # Democracy / rights / governance
    {"id":"vdem","org":"V-Dem Institute","category":"Democracy & Rights","url":"https://www.v-dem.net/publications/","include":r"\b(democracy|autocra|election|polarization|rights|freedom|governance)\b","mode":"institutional"},
    {"id":"idea","org":"International IDEA","category":"Democracy & Rights","url":"https://www.idea.int/publications","include":r"\b(democracy|election|constitution|political participation|representation|governance)\b","mode":"institutional"},
    {"id":"freedom","org":"Freedom House","category":"Democracy & Rights","url":"https://freedomhouse.org/reports","include":r"\b(freedom|democracy|internet|rights|authoritarian|transnational repression)\b","mode":"institutional"},
    {"id":"wjp","org":"World Justice Project","category":"Democracy & Rights","url":"https://worldjusticeproject.org/our-work/research-and-data","include":r"\b(rule of law|justice|governance|rights|corruption|security)\b","mode":"institutional"},
    {"id":"ti","org":"Transparency International","category":"Democracy & Rights","url":"https://www.transparency.org/en/publications","include":r"\b(corruption|integrity|transparency|bribery|governance|accountability)\b","mode":"institutional","reader_first":True},
    {"id":"rsf","org":"Reporters Without Borders","category":"Democracy & Rights","url":"https://rsf.org/en/reports","include":r"\b(press|journalist|media|freedom|censorship|information)\b","mode":"institutional"},

    # Science / high-quality analytical source
    {"id":"quanta","org":"Quanta Magazine","category":"Science","url":"https://www.quantamagazine.org/","include":r"\b(AI|physics|biology|mathematics|computer|quantum|algorithm|genome|cosmology|neuroscience)\b","mode":"analysis"},
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
    global _JINA_LAST_CALL
    if url in _JINA_CACHE:
        cached = _JINA_CACHE[url]
        if isinstance(cached, Exception):
            raise cached
        return cached

    ju = "https://r.jina.ai/" + url
    last_error = None

    # Serialize public Reader calls. Direct institutional requests can still run
    # concurrently, but Jina is intentionally gentle to avoid 429 rate limits.
    with _JINA_LOCK:
        if url in _JINA_CACHE:
            cached = _JINA_CACHE[url]
            if isinstance(cached, Exception):
                raise cached
            return cached

        for attempt in range(3):
            wait = _JINA_MIN_INTERVAL - (time.monotonic() - _JINA_LAST_CALL)
            if wait > 0:
                time.sleep(wait)

            try:
                r = requests.get(
                    ju,
                    headers={"Accept":"text/plain","User-Agent":HEADERS["User-Agent"]},
                    timeout=max(TIMEOUT, 30),
                )
                _JINA_LAST_CALL = time.monotonic()

                if r.status_code == 429:
                    last_error = requests.HTTPError(
                        f"429 Too Many Requests for url: {ju}", response=r
                    )
                    time.sleep(4 * (attempt + 1))
                    continue

                r.raise_for_status()
                _JINA_CACHE[url] = r.text
                return r.text
            except Exception as e:
                last_error = e
                _JINA_LAST_CALL = time.monotonic()
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))

        _JINA_CACHE[url] = last_error or RuntimeError("Jina Reader failed")
        raise _JINA_CACHE[url]


def _url_year(url):
    ys=[int(x) for x in re.findall(r"(?<!\d)(20\d{2})(?!\d)", url or "")]
    ys=[y for y in ys if 2000 <= y <= CURRENT_YEAR]
    return max(ys) if ys else None

def _url_year_month(url):
    """Parse common /YYYY/MM/ report URL structure."""
    m = re.search(r"/(20\d{2})/(0?[1-9]|1[0-2])(?:/|$)", (url or "").lower())
    if not m:
        return None
    y, mon = int(m.group(1)), int(m.group(2))
    if 2000 <= y <= CURRENT_YEAR:
        return (y, mon)
    return None

def _strict_edition_year(spec, text, url):
    """Require the edition year to be attached to the series name, not a nearby
    publication date. Prefer a report-specific URL year, then a year immediately
    following the report name."""
    if spec["id"] not in STRICT_EDITION_YEAR:
        return None

    blob = clean(text or "")
    low = (url or "").lower()
    uy = _url_year(url)

    # A report-specific URL is strong evidence and beats page publication dates.
    slugs = {
        "wmo_climate": ("state-of-global-climate",),
        "unhcr_gt": ("global-trends-",),
        "un_social": ("world-social-report-", "wsr"),
        "idea": ("global-state-of-democracy-",),
        "cpi": ("cpi/", "corruption-perceptions-index-"),
    }
    if uy is not None and any(s in low for s in slugs.get(spec["id"], ())):
        return uy

    rx = re.compile(spec["match"], re.I)
    best = None
    for hit in rx.finditer(blob):
        # Edition naming normally follows the series: "Global Trends 2025".
        after = blob[hit.end():min(len(blob), hit.end()+70)]
        for ym in re.finditer(r"\b(20\d{2})\b", after):
            y = int(ym.group(1))
            if 2000 <= y <= CURRENT_YEAR:
                key = (ym.start(), -y)
                if best is None or key < best[0]:
                    best = (key, y)

        # Fallback for formats such as "2025 Global State of Democracy".
        before = blob[max(0, hit.start()-45):hit.start()]
        for ym in re.finditer(r"\b(20\d{2})\b", before):
            y = int(ym.group(1))
            if 2000 <= y <= CURRENT_YEAR:
                distance = len(before) - ym.end()
                key = (100 + distance, -y)  # following year always preferred
                if best is None or key < best[0]:
                    best = (key, y)

    return best[1] if best else None

def _nearest_year_to_series(spec, text):
    rx=re.compile(spec["match"], re.I)
    years=list(re.finditer(r"\b(20\d{2})\b", text or ""))
    years=[m for m in years if 2000 <= int(m.group(1)) <= CURRENT_YEAR]
    if not years:
        return None, None
    hits=list(rx.finditer(text or ""))
    if not hits:
        return None, None
    best=None
    for h in hits:
        hc=(h.start()+h.end())/2
        for y in years:
            yc=(y.start()+y.end())/2
            dist=abs(yc-hc)
            if dist > 170:
                continue
            # Prefer a year following the series name when distances are similar.
            after_bonus=0 if y.start() >= h.end() else 18
            key=(dist+after_bonus, -int(y.group(1)))
            if best is None or key < best[0]:
                best=(key,int(y.group(1)),y.start())
    if best:
        return best[1], best[2]
    return None, None

def version_from(spec, text, url):
    """Extract REPORT EDITION, not page publication year.

    The main rule is proximity: a year closest to the series name beats unrelated
    dates in navigation, news cards or publication metadata. This fixes cases such
    as WMO 'State of the Global Climate 2025' published in 2026, UNHCR Global
    Trends 2025 published in 2026, and CPI 2025 released in 2026.
    """
    blob=clean(text or "")
    if not blob and not url:
        return None

    strict_y = _strict_edition_year(spec, blob, url)
    if spec["id"] in STRICT_EDITION_YEAR:
        y, pos = strict_y, None
    else:
        y,pos=_nearest_year_to_series(spec, blob)

    # If the candidate itself is a report-specific URL, its year is useful when
    # the title contains no year. We do NOT let a URL year override an explicit
    # year next to the series name (important for FAO SOFI pages).
    uy=_url_year(url)
    if y is None and uy is not None:
        y=uy
        pos=None

    if y is None:
        return None

    month=0
    if spec["mode"]=="intra":
        # Find month near the chosen edition year / report-series phrase.
        low=blob.lower()
        if pos is not None:
            window=low[max(0,pos-90):min(len(low),pos+90)]
        else:
            window=low
        ms=[n for k,n in MONTHS.items() if re.search(rf"\b{re.escape(k)}\.?\b",window)]
        month=max(ms) if ms else 0
        # IMF and similar issue URLs often encode the month numerically.
        if not month:
            ym = _url_year_month(url)
            if ym and ym[0] == y:
                month = ym[1]

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
        # Strict: use link text itself, not parent-card dates. IMF issue pages
        # also expose a stable "Latest Issue" link whose URL identifies the series.
        hint = IMF_ISSUE_HINTS.get(spec["id"])
        if rx.search(t) or rx.search(u) or (hint and hint in u.lower()):
            label_text = t if rx.search(t) else f"{spec['name']} {t}"
            out.append((label_text,u,7,"direct-link"))
    return out

def candidates_from_markdown(spec, text, source_kind, source_url=None):
    """Parse markdown while keeping nearby lines together so report date/edition
    can be on the line before or after the series name."""
    rx = re.compile(spec["match"], re.I)
    out = []
    base_url = source_url or spec["url"]

    # Markdown links.
    for label_text, url in re.findall(r"\[([^\]]{2,300})\]\((https?://[^)\s]+)", text):
        t = clean(label_text)
        hint = IMF_ISSUE_HINTS.get(spec["id"])
        if rx.search(t) or rx.search(url) or (hint and hint in url.lower()):
            label_out = t if rx.search(t) else f"{spec['name']} {t}"
            out.append((label_out, url, 8, source_kind + "-link"))

    # Line windows: report name and date are often rendered on separate lines.
    lines = [clean(re.sub(r"^#+\s*", "", x)) for x in text.splitlines()]
    for i, line in enumerate(lines):
        if not line or not rx.search(line):
            continue
        start = max(0, i - 2)
        end = min(len(lines), i + 4)
        block = clean(" ".join(x for x in lines[start:end] if x))
        if 4 <= len(block) <= 900:
            out.append((block, base_url, 7, source_kind + "-block"))
        if 4 <= len(line) <= 300:
            out.append((line, base_url, 6, source_kind + "-line"))
    return out

def bad_near_series(spec, text, url):
    if BAD_PHRASES.search(url or ""):
        return True
    rx=re.compile(spec["match"], re.I)
    m=rx.search(text or "")
    if not m:
        return False
    near=(text or "")[max(0,m.start()-120):min(len(text or ""),m.end()+180)]
    return bool(BAD_PHRASES.search(near))

def release_validation(spec, text, url, v):
    sid=spec["id"]

    if sid=="gcb":
        if not (re.search(rf"\b(?:Global Carbon Budget|GCB)\s*{v[0]}\b", text, re.I)
                or re.search(rf"/gcb-{v[0]}/?", url, re.I)):
            return False

    if sid=="worldbank_wdr" and v[0] > 2025:
        key=("wdr",url)
        page=_RELEASE_CACHE.get(key)
        if page is None:
            page=""
            try: page=jina_reader(url)
            except Exception:
                try:
                    s,_=direct_html(url); page=clean(s.get_text(" ",strip=True))
                except Exception: pass
            _RELEASE_CACHE[key]=page
        low=page.lower()
        if any(x in low for x in ("concept note","will investigate","background papers","meet the team")):
            return False
        if not any(x in low for x in ("download report","complete report","download full report","final report")):
            return False

    if sid=="wipo_gii":
        near=text.lower()
        if any(x in near for x in ("save the date","will be released","forthcoming","launch on")):
            return False

    # These series are labelled by the data/report year, even when released the
    # next calendar year. Require an explicit series+edition relationship.
    if sid in {"wmo_climate","unhcr_gt","cpi"}:
        y,_=_nearest_year_to_series(spec,text)
        uy=_url_year(url)
        if y != v[0] and uy != v[0]:
            return False

    return True

def choose_candidate(spec, candidates):
    floor=FLOORS.get(spec["id"],(2000,0))
    good=[]
    for text,url,score,method in candidates:
        if bad_near_series(spec,text,url): continue
        if not allowed_domain(spec,url): continue
        v=version_from(spec,text,url)
        if not v: continue
        cmpv=(v[0],v[1] if spec["mode"]=="intra" else 0)
        floorv=(floor[0],floor[1] if spec["mode"]=="intra" else 0)
        if cmpv < floorv: continue
        if not release_validation(spec, text, url, v): continue

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
    errors=[]; candidates=[]
    urls=list(dict.fromkeys([spec["url"]]+ALT_URLS.get(spec["id"],[])))

    for page_url in urls:
        if spec["id"] in SELF_URL_SAFE:
            candidates.append((spec["name"]+" "+page_url,page_url,5,"official-url"))

        if spec["id"] in READER_FIRST:
            try:
                txt=jina_reader(page_url)
                candidates.extend(candidates_from_markdown(spec,txt,"reader",page_url))
            except Exception as e:
                errors.append("reader "+page_url+": "+str(e)[:120])
            best=choose_candidate(spec,candidates)
            if best and best.get("score",0)>=10:
                return best,errors

        try:
            s,final=direct_html(page_url)
            candidates.extend(candidates_from_html(spec,s,final))
        except Exception as e:
            errors.append("direct "+page_url+": "+str(e)[:120])

        if spec["id"] not in READER_FIRST:
            try:
                txt=jina_reader(page_url)
                candidates.extend(candidates_from_markdown(spec,txt,"reader",page_url))
            except Exception as e:
                errors.append("reader "+page_url+": "+str(e)[:120])

        best=choose_candidate(spec,candidates)
        if best and best.get("score",0)>=10:
            return best,errors

    return choose_candidate(spec,candidates),errors

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


DISCOVERY_ALT_URLS = {
    "wb": ["https://www.worldbank.org/en/about/unit/unit-dec/research/publications"],
    "bis": ["https://www.bis.org/index.htm"],
    "unctad": ["https://unctad.org/unctad-publications"],
    "deloitte": [
        "https://www.deloitte.com/us/en/insights/research-centers/center-for-technology-media-telecommunications.html"
    ],
    "kpmg": ["https://kpmg.com/xx/en/our-insights.html"],
    "wmo": ["https://wmo.int/resources/publications?page=0"],
    "undesa_pop": [
        "https://www.un.org/development/desa/pd/content/publications",
        "https://www.un.org/development/desa/pd/"
    ],
}

DISCOVERY_EXCLUDE=re.compile(
    r"\b(webinar|podcast|video|event|job|vacanc|speech|press release|statement|newsletter|"
    r"call for|invitation|registration|course|conference|working paper|technical note|"
    r"methodology|data appendix|chapter|executive summary)\b",re.I)
DISCOVERY_SIGNAL=re.compile(
    r"\b(report|outlook|index|assessment|review|survey|yearbook|statistics|prospects|"
    r"trends|estimates|monitor|state of|flagship|study|analysis|policy brief|research report|"
    r"global|world|annual|strategy|forecast|update)\b",re.I)

def discovery_score(spec,title,context,url):
    blob=clean(title+" "+context)
    if DISCOVERY_EXCLUDE.search(title):
        return -99
    rx=re.compile(spec["include"],re.I)
    topic=bool(rx.search(blob))
    if not topic:
        return -99
    score=3
    if DISCOVERY_SIGNAL.search(blob): score+=3
    if re.search(rf"\b(?:{CURRENT_YEAR}|{CURRENT_YEAR-1})\b",blob): score+=2
    if len(title)>=40: score+=1
    if re.search(r"/(publication|publications|report|reports|research|analysis|brief|insight)",url,re.I): score+=1
    if spec.get("mode")=="analysis":
        # Analytical sources are intentionally stricter to avoid becoming a news feed.
        if not re.search(r"\b(analysis|explainer|report|study|mapped|interactive|investigation|research|index)\b",blob,re.I):
            score-=2
    return score

def discovery_from_html(spec,soup,base):
    out=[]; seen=set(); base_domain=urlparse(base).netloc.replace("www.","")
    for a in soup.find_all("a",href=True):
        title=clean(a.get_text(" ",strip=True))
        if not (20<=len(title)<=260): continue
        url=canonical(base,a["href"])
        dom=urlparse(url).netloc.replace("www.","")
        # IOM publications use publications.iom.int; otherwise stay on host family.
        if spec["id"]!="iom" and dom and base_domain and not (dom==base_domain or dom.endswith("."+base_domain) or base_domain.endswith("."+dom)):
            continue
        parent=a.find_parent(["article","li","div"])
        context=clean(parent.get_text(" ",strip=True))[:700] if parent else ""
        score=discovery_score(spec,title,context,url)
        if score<6 or url in seen: continue
        seen.add(url); out.append((score,title,url))
    out.sort(reverse=True)
    return out[:80]

def discovery_from_reader(spec,text,base):
    out=[]; seen=set(); lines=text.splitlines()
    link_re=re.compile(r"\[([^\]]{20,260})\]\((https?://[^)\s]+)")
    for i,line in enumerate(lines):
        for m in link_re.finditer(line):
            title=clean(m.group(1)); url=m.group(2)
            context=clean(" ".join(lines[max(0,i-2):min(len(lines),i+3)]))[:700]
            score=discovery_score(spec,title,context,url)
            if score<6 or url in seen: continue
            seen.add(url); out.append((score,title,url))
    out.sort(reverse=True)
    return out[:80]

def is_flagship_duplicate(org,title):
    for sp in SERIES:
        if sp["org"]==org or org.lower() in sp["org"].lower() or sp["org"].lower() in org.lower():
            if re.search(sp["match"],title,re.I):
                return True
    return False

def resolve_discovery(spec):
    errors=[]; candidates=[]
    reader_first=bool(spec.get("reader_first"))
    urls=list(dict.fromkeys([spec["url"]] + DISCOVERY_ALT_URLS.get(spec["id"], [])))

    for page_url in urls:
        if reader_first and not candidates:
            try:
                txt=jina_reader(page_url)
                candidates=discovery_from_reader(spec,txt,page_url)
            except Exception as e:
                errors.append("reader "+page_url+": "+str(e)[:140])

        if not candidates:
            try:
                s,final=direct_html(page_url)
                candidates=discovery_from_html(spec,s,final)
            except Exception as e:
                errors.append("direct "+page_url+": "+str(e)[:140])

        if not candidates and not reader_first:
            try:
                txt=jina_reader(page_url)
                candidates=discovery_from_reader(spec,txt,page_url)
            except Exception as e:
                errors.append("reader "+page_url+": "+str(e)[:140])

        if candidates:
            break

    return candidates,errors

def scan_discovery(state,migration):
    new=[]; health=[]; resolved={}
    # Parallelize Layer 2 only. This keeps 38 sources practical without hammering
    # any one institution; each source itself is fetched at most twice.
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs={ex.submit(resolve_discovery,sp):sp for sp in DISCOVERY}
        for fut in as_completed(futs):
            sp=futs[fut]
            try: resolved[sp["id"]]=fut.result()
            except Exception as e: resolved[sp["id"]]=([], [str(e)[:180]])

    for sp in DISCOVERY:
        key="discovery:"+sp["id"]
        candidates,errors=resolved.get(sp["id"],([],[]))
        current=[(t,u) for _,t,u in candidates[:60]]
        current_urls={u for _,u in current}
        old=set(state.get(key,[]))
        fresh=[(t,u) for t,u in current if u not in old and not is_flagship_duplicate(sp["org"],t)]

        suppressed=0
        # A page redesign can suddenly expose dozens of old links. Do not flood.
        if old and not migration and len(fresh)<=8:
            for t,u in fresh[:4]:
                new.append({
                    "id":hashlib.sha1(u.encode()).hexdigest()[:14],"date":TODAY,
                    "category":sp["category"],"source":sp["org"],"title":t,
                    "summary":"New high-signal publication or analysis detected by World Watch's expanded institutional discovery layer.",
                    "url":u,"type":"Important new report" if sp.get("mode")!="analysis" else "Significant analysis",
                    "automatic":True,"confidence":"medium","discovery_source":sp["id"]
                })
        elif old and not migration and len(fresh)>8:
            suppressed=len(fresh)

        # Accumulate, do not replace, so page rotation cannot make an old link new later.
        state[key]=list(dict.fromkeys(list(old)+sorted(current_urls)))[-1200:]
        health.append({
            "monitor":sp["org"]+" discovery","kind":"discovery","ok":bool(candidates),
            "candidates":len(candidates),"new_candidates":len(fresh),"suppressed":suppressed,
            "warnings":errors
        })
    return new,health

def main():
    state=load_json(STATE_FILE,{})
    migration = state.get("_engine_version") != ENGINE_VERSION
    if migration:
        # Re-baseline silently because earlier engines contained known false positives
        # and expands Layer 2; first V6.1 run silently re-baselines corrected edition rules and discovery URLs.
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
        "discovery_source_count":len(DISCOVERY),
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
    print(f"World Watch v6.1: {ok1}/{len(SERIES)} flagship series resolved; "
          f"{ok2}/{len(DISCOVERY)} discovery monitors resolved; "
          f"{len(flagship_new)} newer flagship edition(s); {len(discovery_new)} discovery item(s); "
          f"migration_baseline={migration}")

if __name__=="__main__":
    main()
