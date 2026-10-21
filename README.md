# Guardian Monitor updates

This repo holds the update files for Guardian Monitor (Mac and Windows). The app's **Update** button reads `version.json`, downloads the listed files, checks each file's SHA-256 fingerprint, and installs them.

Current version: **5.3**

5.3: Catches Pokémon Center's Imperva 'Additional security check' (hCaptcha 'I am human') page and pings you right away to solve it. Checks you're still signed in every 15 minutes (not just once), using the page's own Sign In / Sign Out links, and pings you (or signs you in, if turned on) when you're signed out.
