---
date: 2026-10-23
---

# A refused POST is answered after its body has been read

Windows resets the socket when the answer is sent and the body is still unread, so the client never sees the refusal.
