import hashlib

digest = hashlib.md5(b"data").hexdigest()
legacy = hashlib.new("sha1", b"data").hexdigest()
