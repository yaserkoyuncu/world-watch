# World Watch — self-updating GitHub Pages edition

This version checks public sources automatically and republishes itself four times per day.

## One-time GitHub setup
1. Create a **public** repository named `world-watch`.
2. Upload every file/folder from this ZIP to the repository root, including `.github`.
3. Go to **Settings → Pages**.
4. Under **Build and deployment → Source**, select **GitHub Actions**.
5. Open **Actions → Update and deploy World Watch → Run workflow** once.
6. The site will appear at `https://YOUR-USERNAME.github.io/world-watch/`.

## Automatic checks
The workflow runs at about 06:20, 12:20, 18:20 and 00:20 Türkiye time.

It monitors:
- IMF, World Bank, WHO
- CSET and WEF
- International Crisis Group
- Carbon Brief and Quanta
- IEA and WMO
- UNHCR and V-Dem
- plus dedicated flagship-series monitors for WEO, Global Economic Prospects,
  Stanford AI Index, V-Dem Democracy Report, Global Risks Report,
  Munich Security Report, State of the Global Climate, World Energy Outlook,
  Human Development Report and World Migration Report.

The first run creates a baseline so existing items do not flood the site.
After that, newly detected items appear automatically in **Updates**.

## Accuracy note
This is conservative rule-based monitoring, not human editorial judgment.
Automatically detected entries are labeled as such. If a publisher changes its
website structure, one monitor may fail temporarily; `monitor_health.json`
records the status.

Favorites, Read/To read status and Notes are stored locally in your browser.
