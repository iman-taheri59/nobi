# Transfer package for nobi 2.2.1

This directory is a text-only transfer package for the 2.2.1 source tree. It is meant to be carried inside the whole-workspace patch, then extracted on the server. It does not contain live data, env files, databases, paper state, private keys, `.venv`, or `node_modules`.

Files:

```text
nobi-2.2.1-src.tar.gz.b64      # base64-encoded source archive
nobi-2.2.1-src.tar.gz.sha256   # checksum of the decoded archive
SOURCE_VERSION.txt             # expected VERSION
SOURCE_MANIFEST.sha256         # checksums of the extracted source files
```

Expected decoded archive SHA-256:

```text
244d3dced28994ab4ba371907f014cedbbf4e2751a2cb0a1b0e50881deb4c796
```

## Server extraction

Use a fresh directory. Do not extract this onto `/opt/nobi`.

```bash
mkdir -p /root/nobi-transfer-2.2.1-v2
cd /root/nobi-transfer-2.2.1-v2
git init -q
```

First confirm the downloaded workspace patch actually contains this package:

```bash
grep -n 'deploy/transfer-2.2.1/nobi-2.2.1-src.tar.gz.b64' /root/nobi-workspace.patch | head
```

If that prints nothing, stop: the patch is old or incomplete. Download a fresh whole-workspace patch and upload it again.

Then extract only this directory:

```bash
git apply --include='*deploy/transfer-2.2.1/*' --check /root/nobi-workspace.patch
git apply --include='*deploy/transfer-2.2.1/*' /root/nobi-workspace.patch
test -f deploy/transfer-2.2.1/nobi-2.2.1-src.tar.gz.b64
```

Decode and verify:

```bash
base64 -d deploy/transfer-2.2.1/nobi-2.2.1-src.tar.gz.b64 > /root/nobi-2.2.1-src.tar.gz
cd /root
sha256sum -c /root/nobi-transfer-2.2.1-v2/deploy/transfer-2.2.1/nobi-2.2.1-src.tar.gz.sha256
```

Expected:

```text
nobi-2.2.1-src.tar.gz: OK
```

Extract to a staging directory:

```bash
mkdir -p /root/nobi-src-2.2.1
tar -xzf /root/nobi-2.2.1-src.tar.gz -C /root/nobi-src-2.2.1
cat /root/nobi-src-2.2.1/VERSION
```

Expected:

```text
2.2.1
```

Optional full source manifest check:

```bash
cd /root/nobi-src-2.2.1
sha256sum -c /root/nobi-transfer-2.2.1-v2/deploy/transfer-2.2.1/SOURCE_MANIFEST.sha256
```

After that, continue with the upgrade runbook. Do not enable Live/Auto and do not send real orders.
