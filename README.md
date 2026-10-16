# Queue Watcher updates

This repo holds the update files for Queue Watcher (Mac and Windows). The app's **Update** button reads `version.json`, downloads the listed files, checks each file's SHA-256 fingerprint, and installs them.

Current version: **3.2**

Error pages no longer stop the watcher: it closes the tab, rests (2 min, longer if it keeps happening, max 30), opens a fresh tab and keeps watching.
