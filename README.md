# Guardian Monitor updates

This repo holds the update files for Guardian Monitor (Mac and Windows). The app's **Update** button reads `version.json`, downloads the listed files, checks each file's SHA-256 fingerprint, and installs them.

Current version: **5.7**

5.7: After an update installs and the app restarts, your phone now gets a "✅ Updated to X" message with the current status, so you know it went through.

5.6: Windows gets your updated watcher_app (installs aiohttp and websockets by itself on first start if they're missing), with the debug Chrome button kept. Mac is unchanged except the version number.

5.5: The debug Chrome button now also checks for websockets and aiohttp and pip installs whichever is missing, then starts Chrome (port 9222, its own profile folder), closing and restarting it if it's stuck. Your phone gets one reply saying what it did. The button is sent to your phone again once.

5.4: Temporary: sends your phone a one-time "Launch debug Chrome" button. Tapping it (or sending "debugchrome") opens Chrome with --remote-debugging-port=9222 and its own profile folder (--user-data-dir=C:\QueueWatcher\checker-chrome-pkc), since Chrome 136+ ignores the debugging port on the default profile.

5.3: Catches Pokémon Center's Imperva 'Additional security check' (hCaptcha 'I am human') page and pings you right away to solve it. Checks you're still signed in every 15 minutes (not just once), using the page's own Sign In / Sign Out links, and pings you (or signs you in, if turned on) when you're signed out.
