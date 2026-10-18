# VirusTotal is asked about hashes, never sent files

The optional VirusTotal hook looks up each uploaded file's SHA-256, which OpenClear already
computes, and acts on a known-malicious verdict. It never uploads a file. Files uploaded to
VirusTotal are shared with its security partners and searchable by its subscribers, and
OpenClear's uploads are check images, payment files and customers' account data.

The cost is that a file VirusTotal has never seen gets no verdict from it. That's what the
ClamAV hook, and the bank's own malware detection (a CUEC), are for. Adding an "upload unknown
files" option later would send customer data to third parties, so it needs this decision
reopened first.
