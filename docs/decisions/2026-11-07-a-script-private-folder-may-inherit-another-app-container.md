---
date: 2026-11-07
---

# A script's private folder may inherit another app container

Claude Desktop gives its own app container full control of the whole user profile, so every project there inherits that entry, and inside the script container Python's owner-only folder either failed or locked the container out of its own folder, so aicad could not start and every design script failed with "The script job could not be read". Inside the container, a private folder is now always made the plain way and inherits its parent. It may name SYSTEM, Administrators, the owner, the account, the script container, and any other app container that it only inherits, such as Claude Desktop's. Each such container is named once in the app console and in the run's notes, by SID and package name. An app container set on the folder itself, any user or group such as Users, Everyone, or Authenticated Users, and, for an offline script, aicad's network container are refused with a clear message. A script that cannot start now says why, without a path.
