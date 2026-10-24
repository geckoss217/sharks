#!/usr/bin/env python3
"""keywords_decode.py <file.b64> <expected_sha256> <out.tsv>
Decodes a base64 zlib blob from the Zapier gaql_export_tsv action, verifies sha256,
writes the TSV. Exit 0 + 'OK rows=N' on success, exit 1 + 'MISMATCH' otherwise."""
import sys, base64, zlib, hashlib
src, sha, out = sys.argv[1:4]
raw = open(src).read().strip().replace("\n", "").replace(" ", "")
import re
def b64(x):
    try:
        return base64.b64decode(x)
    except Exception:
        # blob arrived as several separately padded base64 chunks
        return b"".join(base64.b64decode(c) for c in re.findall(r"[A-Za-z0-9+/]+={0,2}", x))
try:
    t = zlib.decompress(b64(raw))
except Exception as e:
    print("MISMATCH decode-error", e); sys.exit(1)
h = hashlib.sha256(t).hexdigest()
if h != sha.strip():
    print("MISMATCH sha", h); sys.exit(1)
open(out, "wb").write(t)
print("OK rows=%d bytes=%d" % (t.count(b"\n") - 1, len(t)))
