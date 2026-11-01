# Knowledge parser fixtures

Parser tests build small PDF and OOXML documents in memory so malformed XML,
oversized metadata, encrypted files, and high-compression inputs remain
reproducible without checking in potentially hazardous document payloads.
Keep fixture generation deterministic and bounded; do not add real customer
documents or secrets here.
