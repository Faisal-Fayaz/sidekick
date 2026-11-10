import hashlib

digest = hashlib.sha256(b"data").hexdigest()
cache_key = hashlib.md5(b"data", usedforsecurity=False).hexdigest()
