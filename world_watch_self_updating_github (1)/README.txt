WORLD WATCH — READY-TO-HOST WEBSITE

FILES
- index.html       Main website
- updates.json     Update feed loaded by the website
- README.txt       This file

EASIEST HOSTING: NETLIFY
1. Go to https://app.netlify.com/drop
2. Drag the whole 'world_watch_site' folder onto the page.
3. Netlify gives you a public URL.
4. You can change the generated site name in Netlify's site settings.

HOW THE UPDATE AREA WORKS
- The website requests updates.json every time it opens online.
- If updates.json contains a newer item, it appears automatically.
- The NEW badge is based on the date of the newest update you have marked as seen.
- Favorites, Read/To read status, notes, and seen-update state are stored in your browser (localStorage).
- This means personal status is device/browser-specific.

IMPORTANT LIMITATION
This is a static website. ChatGPT notifications cannot directly edit a Netlify site you own.
The scheduled World Watch check can notify you when a new flagship report appears.
When you want the website feed refreshed too, ask ChatGPT to update the World Watch site;
replace the hosted updates.json (or re-upload the updated site folder).

For truly automatic website updating without manual re-uploading, the next step is a GitHub Pages/Netlify setup with an automated updater or backend.
