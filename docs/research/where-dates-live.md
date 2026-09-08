# Where dates live

Research for the dating module (`docs/design.md`, "Dating"). The question: for
each raw-unit kind a personal archive is likely to contain, where does a
reliable date live, how do we read it in Python, and how much should we trust
it?

Context from memoria (`memoria/src/memoria/normalize.py`, read-only): the
plain-text, docx and pdf converters hardcode `recorded_date=""` with
`date_confidence="unresolved"`; the email converter stores the verbatim
`Date` header string when `parsedate_to_datetime` accepts it and otherwise
leaves the date empty. None of them consult document metadata, path patterns
or body text. Everything below is what those converters leave on the table.

Confidence vocabulary is the Record interface's: `exact` (the source states
its own date in machine-readable form and we trust it), `inferred` (derived
from a convention or heuristic that could be wrong), `unknown` (nothing
produced; no invented date).

## The table

| Kind | Where the date lives | How to read it in Python | Reliability and known failure modes | Suggested confidence |
|---|---|---|---|---|
| **docx** | `docProps/core.xml` inside the zip: `dcterms:created`, `dcterms:modified` (W3CDTF, e.g. `2013-11-04T21:15:03Z`). Also `cp:lastPrinted`, `cp:revision`, and `app.xml` `TotalTime`. | `docx.Document(f).core_properties.created` / `.modified` (naive datetime, UTC, or `None`); or `zipfile` + `xml.etree` on `docProps/core.xml` with namespace `http://purl.org/dc/terms/`. MarkItDown does **not** expose these: its `DocxConverter.convert` goes docx -> mammoth HTML -> markdown and never opens `core.xml`. | `created` is set when Word first opens a new document, not when it is saved; documents made from a `.dotx` or from an old file via "Save As" / copy-edit-rename carry the *original's* `created` forward, so a diary kept as one Word file per year can have every entry sharing one `created` from the first entry. `modified` is the last save, which for an archive migrated between machines or re-saved in bulk is the migration date. Word moderators call these dates "basically meaningless" as an audit trail; `created == modified` or `modified < created` are common. Files written by LibreOffice, Google Docs export, Pages export or scripts may omit both or stamp export time. | `inferred` at best, and only when no path/body date exists. Prefer `created`; if `created` and `modified` are years apart, or `created` predates a path date, distrust `created`. Never `exact`. |
| **pdf** | Info dictionary `/CreationDate`, `/ModDate` as `D:YYYYMMDDHHmmSSOHH'mm'` (offset optional, `Z` allowed); XMP packet (`xmp:CreateDate`, `xmp:ModifyDate`, `xmp:MetadataDate`, `dc:date`) as ISO 8601. | `pypdf.PdfReader(f).metadata.creation_date` / `.modification_date` (datetime or `None`; the `_raw` variants return the string). `parse_iso8824_date` tries `D:%Y` through `D:%Y%m%d%H%M%S%z`, normalises `Z` and `'` quoting, and **raises `ValueError("Can not convert date: ...")`** on anything else, so wrap it. XMP: `reader.xmp_metadata.xmp_create_date` (UTC datetime) or `.dc_date`. `pdfplumber.open(f).metadata` returns the raw Info dict (`CreationDate` string) with no parsing; memoria already opens the file with pdfplumber so the string is one attribute away. | Born-digital PDFs (Word "Save as PDF", LaTeX, Pages): `CreationDate` is the export moment, which is often the authoring date but is the *conversion* date for a diary typeset years later. Scanned PDFs: `CreationDate` is the scan date and the producer is the scanner or Acrobat; the paper's date is only in the image (OCR, out of scope). Printing to PDF or scanning erases upstream metadata entirely. Wrong device clocks, tools that reset `ModDate == CreationDate`, and offsets in two different time zones (edited on another machine) are all documented forensic pitfalls. Many PDFs have no Info dates at all. | `inferred` for born-digital when producer is a word processor and the date is plausible; `inferred` weak or `unknown` for scans (producer/creator names a scanner, or the page has no text layer). Never `exact`. |
| **email (.eml, mbox)** | `Date` header (RFC 5322 `Mon, 4 Nov 2013 21:15:03 -0500`). Fallback: the date after the last `;` of each `Received` header (the topmost is the last hop, closest to delivery). Gmail Takeout mbox also has the `From ` envelope line with a ctime-style timestamp (`mailbox.mboxMessage.get_from()`), plus `X-Gmail-Labels`, `X-GM-THRID`. | `email.utils.parsedate_to_datetime(msg["Date"])`. Verified in this session: accepts obsolete two-digit years (`13` -> 2013) and named zones (`EST`, `GMT`, `PST`); returns a **naive** datetime for `-0000`, unknown zone names (`XYZ`), or no zone at all; **raises `ValueError`** on empty/whitespace-only headers and on non-RFC text like `November 4, 2013` or `4/11/2013`; Python 3.9.20+/3.12+ default to strict parsing. `Received`: `msg.get_all("Received")`, `r.rsplit(";", 1)[1].strip()`, same parser. | `Date` is set by the sending client, so it is wrong when the sender's clock is wrong (1970 and 2036 are the classic values), missing on drafts and on some list/system mail, and occasionally in a local format from broken clients. `Received` is stamped by servers and is usually within minutes of `Date`; but a message that was forwarded, imported or re-uploaded to Gmail can carry a `Received` from the import. Sent mail and drafts often have no `Received` at all. Time zone matters: a `-0500` message at 23:30 is the next day in UTC; keep the local date the sender saw, not the UTC date. | `exact` when `Date` parses (store the local date). `inferred` from the oldest `Received`, or from the mbox `From ` line, when `Date` is missing or unparseable. `unknown` otherwise. |
| **Outlook .msg** | MAPI properties: `PidTagClientSubmitTime` (0x0039, the sent time, from which clients generate `Date`), `PidTagMessageDeliveryTime` (0x0E06), `PidTagCreationTime` (0x3007), `PidTagLastModificationTime` (0x3008); plus `PidTagTransportMessageHeaders` (0x007D) carrying the original `Date:` header when present. | `extract_msg.openMsg(path)`: `.date` returns `props.date` (only `00390040`, ClientSubmitTime) **and only if `isSent`**, else `None`; `.receivedTime` = `0E060040`; `.parsedDate` = `email.utils.parsedate(header["Date"])` from the transport headers; `.header` gives the RFC headers for the `Received` fallback. | Drafts and unsent items have no submit time, so `.date` is `None` by design. Items copied between stores keep submit time but `CreationTime`/`LastModificationTime` reflect the copy. Times are UTC datetimes; convert to the account's local zone for the calendar date. | `exact` from `.date` or a parseable transport `Date`; `inferred` from `receivedTime`; `unknown` for drafts. |
| **Outlook .pst / .ost** | Same MAPI properties, inside the PST. | `pypff` (libpff Python bindings): `message.client_submit_time`, `.delivery_time`, `.creation_time`, `.modification_time` (datetime or `None`), `.transport_headers` (string, parse with `email`). Alternative: `libratom` wraps pypff; or convert the PST to mbox/eml with `readpst` (libpst) first and use the email path. | pip's old `pypff` (20161119) could not read message times; use a 2019+ build. PST needs a C extension build on Windows; conversion to eml via `readpst` may be the more portable route and then everything above applies. | As for .msg. |
| **Google Takeout: Gmail** | mbox as above; Takeout adds nothing date-related beyond the `From ` line. | `mailbox.mbox(path)`. | Chat logs in old Takeouts had their date only in the `From ` line (no `Date` header). | As for email. |
| **Google Takeout: Keep** | One `.json` per note: `createdTimestampUsec`, `userEditedTimestampUsec` (microseconds since epoch, UTC), alongside `title`, `textContent`, `isTrashed`, etc. A sibling `.html` renders the same note with a human date. | `json.load`; `datetime.fromtimestamp(v / 1_000_000, tz=timezone.utc)`. | Solid; the JSON is generated from Keep's own record. Notes imported into Keep from elsewhere get the import time as `created`. Time zone of the local date is unknown (UTC only). | `exact` (created), with the caveat that it is the note's creation, not necessarily what it describes. |
| **Apple Notes** | Notes.app's native export is PDF only (no note dates; the PDF `CreationDate` is the export moment). Third-party exporters carry `created` / `modified` from the Notes database or Shortcuts: e.g. `apple-notes-export` writes YAML front matter `created: "Tuesday, January 15, 2025 at 10:30:00 AM"` (locale long form, no zone); `Exporter.app` preserves creation/modification dates; Kylmakalle's Shortcuts exporter puts `$creation_date` in the *filename*. The raw store is `~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite` (`ZICCLOUDSYNCINGOBJECT.ZCREATIONDATE1` as Core Data seconds since 2001-01-01), readable with `sqlite3` or the `apple_cloud_notes_parser` tool. | Front matter: `python-frontmatter` then `dateutil.parser.parse(value)` (long English forms parse cleanly; set `dayfirst` per locale). Filename: regex. NoteStore: `datetime(2001,1,1,tzinfo=UTC) + timedelta(seconds=v)`. | Whichever exporter was used decides what survives; many early exporters wrote none. Exports to iCloud.com or PDF lose dates. Notes moved between accounts or restored from backup can have their creation date reset (a known complaint). Front-matter dates are in the exporting Mac's locale and zone, so `01/02/2025` is ambiguous. | `exact` from NoteStore or from an ISO front-matter value; `inferred` from a locale-formatted front-matter or filename date. |
| **Day One** | JSON export: each entry has `creationDate` and `modifiedDate` as ISO 8601 UTC (`2023-09-06T19:09:08Z`) plus a `timeZone` IANA name (`America/Chicago`), `creationDevice`, and often `location`. Markdown/txt export: a header block per entry starting `\tDate:\t<Month d, yyyy at h:mm:ss PM ZZZ>` followed by the body; one file per entry or per journal depending on options. | `json.load`; `datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(ZoneInfo(entry["timeZone"]))` and take `.date()`. Markdown: regex on the `Date:` line, `dateutil.parser.parse`. | The UTC value alone gives the wrong calendar day for evening entries; the `timeZone` conversion is mandatory. When a user back-dates an entry (common for memoir-style entries about the past), Day One's forum records that older exports put the *edit* time in `creationDate` and did not expose the user-set date; a later export fixed it, so treat `creationDate` as the entry date but expect a few anomalies. Photos carry their own EXIF dates. | `exact` from JSON (after zone conversion). `inferred` from the markdown `Date:` line (locale text). |
| **Evernote ENEX** | XML, one `<note>` per note: `<created>` and `<updated>` in compact ISO `20130104T163823Z` (UTC, both optional per the DTD); `<note-attributes>` may carry `<subject-date>` (the user-set "subject date", same format), `<source>`, `<source-url>`, `<source-application>`, `<author>`. | `xml.etree.ElementTree.iterparse` (files can be hundreds of MB; stream); `datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)`. | Reliable for notes typed in Evernote. Web clips and imports (from email, from the Evernote scanner, from other apps) get the clip/import time; the original's date is only in the content. `subject-date` is rarely populated but when present it *is* the "about" date and should win over `created`. All UTC: convert to the user's zone for the local day. Very old exports (ENEX v1/v2) lack `note-attributes`. | `exact` from `subject-date` if present, else `created`. |
| **Obsidian / markdown journals** | Filename is the primary convention: daily notes default to `YYYY-MM-DD.md`; folder layouts like `2023/January/2023-Jan-01.md` or `Journal/2013/2013-11-04.md` come from the Daily Notes and Periodic Notes plugins' `date format`. Front matter: no standard key; `date:`, `created:`, `created_at:`, `day:`, `journal-date:`, `published:` all occur, as bare YAML dates, ISO timestamps, or quoted locale strings. Templater/Dataview users often write `created: 2023-01-01T09:14:00`. The Daily Notes plugin recognises a `date` property and links it to the daily note. | `python-frontmatter` (PyYAML) turns unquoted `2013-11-04` into a `datetime.date` and `2013-11-04T09:14:00` into `datetime.datetime`; quoted strings stay strings and need `dateutil` / `datetime.fromisoformat`. Filename: regex for ISO first, then month-name forms. | Front-matter keys are user-chosen; a vault may mix several. PyYAML's date coercion silently fails for `2013-11-4` (stays string) and misreads `04/11/2013`. Templates copied without re-running Templater leave literal `{{date}}`. Files re-saved by a sync client keep front matter intact, which is why front matter beats mtime. | `exact` for ISO front-matter `date`/`created` and for an ISO filename (`2013-11-04`); `inferred` for month-name or numeric-ambiguous forms and for year/month folders (which only give a floor). |
| **Scrivener** | `<project>.scriv/<project>.scrivx` (XML): each `<BinderItem UUID=... Type="Text" Created="2025-03-14 22:15:17 -0600" Modified="2025-03-14 22:15:23 -0600">` with `<Title>`. Document text lives in `Files/Data/<UUID>/content.rtf` (RTF, no metadata of its own; `synopsis.txt`, `notes.rtf` alongside). `Files/writing.history` has per-day word counts. | `xml.etree` over the `.scrivx`, map `UUID` -> `Created`; `datetime.strptime(v, "%Y-%m-%d %H:%M:%S %z")`. RTF via `striprtf` for text. Scrivener 2 used `binder.scrivx` with numeric `ID`s and folders `Files/Docs/<n>.rtf`. | `Created` is when the binder item was made in Scrivener, not when the prose was written; imported manuscripts get the import moment for every item. Reliable for entries drafted directly in Scrivener. Offsets are the author's local zone, so the local day is right. | `inferred` (creation of the container, not the text). `exact` only for a per-collection rule saying this project *is* a journal kept in Scrivener. |
| **Word diary files with the date as the first line** | The first non-empty paragraph, or the first heading, is the date in the author's own words: `Monday, November 4, 2013`, `Nov 4 2013`, `4th November 2013`, `11/4/13`, sometimes with time or place. Core properties are usually the year file's or template's. | Read paragraphs via the existing converter; test paragraph 1 (and any heading in the first 3) with a constrained recogniser: strict regexes for the common shapes, then `dateutil.parser.parse(line, fuzzy=False, dayfirst=<collection setting>)` only when the line is short (say <= 40 chars) and contains a 4-digit year or a month name plus day. | The first line is also the title, a salutation, or `Chapter 3`. Entries within one file run on: only the first entry's date is on line 1; the rest are interior headings (this is the `<!-- page -->`-style split problem, out of the dating module's scope but worth flagging). Two-digit years and `11/4` vs `4/11` are the traps. | `exact` when the line is nothing but a date containing a 4-digit year and an unambiguous month (name, or day > 12, or collection `date_order` set); `inferred` when the year is two-digit or the order ambiguous. |
| **Plain text / markdown body dates** | Same as above: first line, first heading, or a `Date:` / `date:` / `Written:` label in the first few lines; Day One and Evernote HTML exports also lead with a date line. | As above; `dateparser.search_dates` only as a last resort and never on the whole body. | See "Body dates" below. | `exact` / `inferred` / `unknown` per the rules there. |
| **Filenames and folders** | `2013-11-04-journal.md`, `20131104_notes.txt`, `2013-11-04 21.15.03.jpg`-style, `2013-11.md`, `Nov 4 2013.docx`, `4 Nov 2013.docx`, `11-04-2013.doc`, `04.11.2013.doc`, `2013/November/...`, `2013/11/...`, `Journal 2013.docx`, `IMG_20131104_...`. | `re` over the path components from leaf to root; see "Path patterns" below. | `dd-mm` vs `mm-dd` is undecidable for days <= 12 without a collection setting or corroboration; `2013-11` might be a month or an ID; 8-digit runs collide with phone numbers and IDs (validate month/day ranges and year in 1900..2100); year-only folders give a floor, not a date; export tools stamp export time in names (`Notes Export 2024-01-15/`). | ISO with 4-digit year and valid ranges: `exact`; month-name forms and 8-digit runs: `inferred`; numeric ambiguous forms: `inferred` only with collection `date_order`, else `unknown` for the day (keep year-month as inferred); year/month folders: `inferred` at month or year granularity. |
| **Filesystem mtime/ctime** | `stat` results. | `os.stat(path).st_mtime`. | Destroyed by every copy, sync, unzip and Takeout download; only meaningful for files that have never left the machine. | `unknown` (do not use as a date; useful only as a tie-break or a sanity ceiling). |

## Notes per kind

### docx

`python-docx`: `Document(path).core_properties.created` returns a naive
`datetime` (UTC) or `None`; the library never sets these itself except when it
has to add a `core.xml` that was missing, in which case it writes default
`modified`/`revision` values that are pure noise. Reading `docProps/core.xml`
directly is a `zipfile` + `ElementTree` job and avoids importing python-docx:

    ns = {"dcterms": "http://purl.org/dc/terms/"}
    root = ET.fromstring(zf.read("docProps/core.xml"))
    created = root.findtext("dcterms:created", namespaces=ns)   # "2013-11-04T21:15:03Z" or None

MarkItDown's `DocxConverter.convert` calls mammoth for HTML then
`HtmlConverter.convert_string`; `DocumentConverterResult` has `markdown` and a
`title` (from the HTML `<title>`, which mammoth does not emit for docx), so
nothing about dates reaches memoria's converter today. The dating module
must open the zip itself.

How often does `created` reflect the template rather than authoring? Word
sets `dcterms:created` when a new document is opened, and it is preserved by
in-place saves and by "Save As" from an existing document (Word's own dialog
distinguishes "Content created" from the file-system date for this reason).
So a diary kept by duplicating last year's file, or by opening a `.dotx`
that was itself saved as a document, inherits an old `created`; conversely a
paper journal transcribed in 2020 has `created` = 2020. Microsoft's own Q&A
moderators describe these fields as unreliable and easily manipulated. There
is no published frequency; the heuristic that works is: trust `created`
only when no path or body date contradicts it, and when `created` and
`modified` are within a plausible authoring window (days to months, not
years).

### pdf

pypdf's `parse_iso8824_date` (in `pypdf/_utils.py`) accepts truncated forms
down to `D:2013`, normalises `Z` and the `'` quoting in offsets, and raises
`ValueError` on anything else; `metadata` itself can be `None` for PDFs
without an Info dictionary. XMP dates (`reader.xmp_metadata.xmp_create_date`)
are ISO and independently written by Acrobat/InDesign; when Info and XMP
disagree, forensic practice is to treat the XMP `CreateDate` as the original
authoring and the Info `ModDate`/XMP `MetadataDate` as later touches.

pdfplumber exposes `pdf.metadata` as the raw Info dict (strings), so the
existing converter could hand `metadata.get("CreationDate")` and the
`Producer`/`Creator` strings to the dating module without a second parse of
the file. `Creator`/`Producer` is the scan-vs-born-digital tell: scanner
vendors (Canon, Epson, Fujitsu ScanSnap, "Adobe Acrobat ... Paper Capture")
mean the date is the scan date; "Microsoft Word", "Pages", "LaTeX", "Google
Docs" mean it is the export date. A page with no text layer is also a scan.

### email

Verified against Python 3 stdlib in this session (see table). Points that
matter for the module:

- `parsedate_to_datetime` accepts more than RFC 5322 (two-digit years, `EST`
  and friends, missing seconds, ctime layout) and rejects human forms; the
  naive result for `-0000`/unknown zone is a real timestamp with an unknown
  local day, so record the date as given and mark it `exact` anyway - the
  sender's clock produced it.
- The `Date` header is the sender's local time; the calendar date to store is
  that local date, not the UTC one. Do not `.astimezone(UTC)` before taking
  `.date()`.
- Fallback order: `Date` -> earliest `Received` (bottom of the list, closest
  to the sender) -> mbox `From ` line (`mailbox.mboxMessage.get_from()`, ctime
  format, parse with `datetime.strptime(s.split(" ", 1)[1], "%a %b %d %H:%M:%S %z %Y")`
  or `email.utils.parsedate_to_datetime` after reshuffling).
- `.msg`: `extract_msg` `.date` is `ClientSubmitTime` gated on `isSent`;
  drafts give `None`, and the transport headers (`msg.header["Date"]`) are the
  next stop, then `msg.receivedTime`. `.pst`: `pypff` message attributes
  `client_submit_time`, `delivery_time`, `creation_time`, `modification_time`;
  or convert to eml with `readpst` and reuse the email path.

### Personal-archive exports

- **Google Keep**: `createdTimestampUsec` is authoritative and machine-clean;
  the calendar day needs a zone, which the export does not carry (use the
  collection's home zone).
- **Day One**: JSON `creationDate` (UTC) + `timeZone` is the pairing; the
  markdown/txt export has a per-entry `Date:` line in locale text. Older
  exports did not expose user-changed entry dates.
- **Evernote**: `created`/`updated` compact ISO UTC; `subject-date` is the
  user's own "this is about" date and should win when present.
- **Apple Notes**: nothing native survives except through a third-party
  exporter or the SQLite store; front-matter dates are locale strings.
- **Scrivener**: `Created` attribute on `BinderItem`, local offset included;
  it dates the container, so `inferred`.

### Path patterns

Patterns worth matching, most to least trustworthy, tried on the filename
first and then each parent folder up to the collection root:

1. ISO date `(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)` and its `_`/`.`/no-separator
   variants (`20131104`), with range validation (year 1900-2100, month 1-12,
   day valid for the month). Also `2013-11-04T2115` / `2013-11-04 21.15.03`.
2. Month-name forms in either order: `Nov 4 2013`, `4 Nov 2013`,
   `November 4, 2013`, `4th November 2013`, `2013 November 4`. Month names
   are unambiguous; the day/year are recoverable by size.
3. Numeric with a 4-digit year: `11-04-2013`, `04.11.2013`, `2013.11.04`.
   Requires `date_order` from the collection when both fields <= 12.
4. Two-digit year forms (`11-04-13`, `041113`): only under a collection rule;
   otherwise skip.
5. Folder-only granularity: `2013/November/`, `2013/11/`, `2013-11/`,
   `2013/`. Emit the first of the period with `inferred` and record the
   granularity in `date_text` (`2013-11`, `2013`); never promote to a day.

Traps: `dd-mm` vs `mm-dd` for day <= 12; 8-digit runs that are IDs or phone
numbers (validate ranges and require a delimiter or a known suffix such as
`IMG_`); export-tool folders stamped with export dates (`Takeout/…`,
`Notes Export 2024-01-15`), which are why the collection root should stop the
walk; dates that appear twice in a path and disagree (leaf wins, folder gives
the ceiling/floor check).

### Body dates

A first-line date is recognised, never searched for. The rule set that avoids
inventing one:

- Look only at the first non-empty paragraph, a leading heading, or a labelled
  line (`Date:`, `date:`, `Written:`, Day One's `\tDate:\t`) within the first
  five lines.
- Candidate must be short (roughly <= 40 characters after stripping heading
  markers and weekday names) and must contain a 4-digit year, or a month
  name with a day number.
- Match with explicit regexes for the month-name, ISO and numeric shapes
  before touching a fuzzy parser. Then `dateutil.parser.parse(text, fuzzy=False,
  dayfirst=collection.day_first, default=<sentinel>)` and reject the result
  when it equals the sentinel's fields (dateutil fills missing components
  from `default`, which is how "November" becomes a full date).
- `dateutil` traps: `fuzzy=True` will pull "4" and "2013" out of "4 people
  came in 2013"; `parse("11-6-19")` is 2019-11-06 by default; bare numbers
  parse as days/years; `fuzzy_with_tokens` raises on some inputs.
- `dateparser` traps: it returns a *full* date for "November" or "Monday" by
  filling from today (`PREFER_DAY_OF_MONTH`, `PREFER_DATES_FROM` govern the
  fill), `search_dates` flags "today", "now", numbers and month-like words
  across the body, and its `no-spaces-time` parser "can produce false
  positives frequently" per its own docs. If used at all: `settings={
  "STRICT_PARSING": True, "REQUIRE_PARTS": ["day", "month", "year"],
  "DATE_ORDER": collection.date_order, "PARSERS": ["absolute-time"]}` and
  only on the candidate line, never on the body. Its multi-language support
  is the one reason to prefer it over `dateutil` for non-English journals.
- Confidence: `exact` when the whole candidate line is the date (plus a
  weekday or time) with an unambiguous day/month and a 4-digit year;
  `inferred` when the date is embedded in a longer heading, the year is
  two-digit, or `date_order` had to decide it; `unknown` if no candidate.

## Implications for the dating module

Strategy order, each an internal seam tested through the one interface:

1. **Transport / native record metadata that names the entry itself.**
   Email `Date` (then `Received`, then mbox `From `); `.msg`
   `ClientSubmitTime` or transport `Date`; Keep `createdTimestampUsec`;
   Day One `creationDate` + `timeZone`; Evernote `subject-date` then
   `created`; ISO front matter `date`/`created`. Yield `exact`. Store the
   sender's/author's local calendar date, and keep the verbatim string in
   `date_text`.
2. **Path patterns**, filename first, then folders up to the collection
   root. ISO with 4-digit year yields `exact`; month-name and validated
   8-digit forms yield `inferred`; numeric-ambiguous forms yield `inferred`
   only with a collection `date_order`; folder-only patterns yield
   `inferred` at month/year granularity with the granularity visible in
   `date_text`. A path date that contradicts a step-1 date should not
   override it but should be logged.
3. **Body first line / heading / labelled line**, constrained as above.
   `exact` when the line is nothing but an unambiguous date with a 4-digit
   year; otherwise `inferred`.
4. **Document metadata** (docx `dcterms:created`, pdf `CreationDate`/XMP
   `CreateDate`, Scrivener `Created`). Yield `inferred`, never `exact`, and
   only when steps 1-3 produced nothing; downgrade further or drop when the
   pdf producer is a scanner or the page has no text layer, or when docx
   `created` and `modified` are years apart. This is below path and body
   because a diary's own words about its date beat a Word template's
   birthday.
5. **Per-collection rule from config**: `date_order` (dmy/mdy), home time
   zone, a fixed date or year for an undated collection, or "trust docx
   created here". Applied as a parameter to steps 2-4 rather than as a
   final fallback, except for the fixed-date case, which yields `inferred`
   at the granularity given.
6. Nothing produced: empty date, `unknown`. Filesystem mtime is never a
   date source.

Two further consequences for the design:

- The converters should pass through, not interpret, the metadata they
  already touch: pdfplumber's `pdf.metadata` dict and the docx zip are open
  in the converter anyway, so the raw unit handed to the dating module should
  carry `converter_metadata` (Info dict strings, `core.xml` text, email
  headers) rather than the module reopening bytes. This keeps "converters do
  not touch date fields" true while avoiding a second parse.
- `date_text` should carry granularity and origin for `inferred` dates
  (`"2013-11 (folder)"`, `"Nov 4 2013 (filename)"`, `"docx created
  2013-11-04T21:15Z"`), because the digest agent's honesty about scope
  depends on being able to say "dated by folder to November 2013" rather
  than presenting an inferred first-of-month as a fact.

## Sources

- python-docx core properties: https://python-docx.readthedocs.io/en/latest/api/document.html and https://python-docx.readthedocs.io/en/latest/dev/analysis/features/coreprops.html
- MarkItDown DocxConverter source: https://github.com/microsoft/markitdown/blob/main/packages/markitdown/src/markitdown/converters/_docx_converter.py
- Word created/modified semantics: https://learn.microsoft.com/en-us/answers/questions/5104322/ms-word-creation-date-and-modified-date-properties and https://learn.microsoft.com/en-us/answers/questions/4321702/word-document-creation-date-changing
- pypdf metadata: https://pypdf.readthedocs.io/en/stable/user/metadata.html ; `parse_iso8824_date` in https://github.com/py-pdf/pypdf/blob/main/pypdf/_utils.py ; `DocumentInformation` in https://github.com/py-pdf/pypdf/blob/main/pypdf/_doc_common.py
- PDF metadata forensics: https://pdfa.org/wp-content/uploads/2025/10/0-2-15_30-CherieEkholm-PDF_Forensics_and_the_Metadata_conundrum.pdf ; https://htpbe.tech/blog/pdf-metadata-fields-complete-reference ; https://forensicdiscovery.expert/blog/metadata-matters-the-story-behind-every-pdf/
- email.utils: https://docs.python.org/3/library/email.utils.html ; edge cases https://github.com/python/cpython/issues/126845 ; whitespace-only header crash https://github.com/python/cpython/issues/74866
- Received-line parsing: https://www.mailneo.co/blog/parsing-email-headers-received-lines
- extract-msg `date`, `receivedTime`, `parsedDate`: https://msg-extractor.readthedocs.io/en/latest/_modules/extract_msg/msg_classes/message_base.html and https://msg-extractor.readthedocs.io/en/latest/_modules/extract_msg/properties/properties_store.html ; PidTagClientSubmitTime https://learn.microsoft.com/en-us/openspecs/exchange_server_protocols/ms-oxcmail/5c270fef-d4ed-46b1-ab8e-1af230806cf1
- pypff message attributes: https://github.com/libyal/libpff/blob/main/pypff/pypff_message.c ; version caveat https://sylvaindurand.org/outlook-email-analytics-with-python/
- Gmail Takeout mbox: https://www.cloudmailin.com/blog/extracting-emails-from-gmail-with-google-takeout-and-mbox ; https://gist.github.com/benwattsjones/060ad83efd2b3afc8b229d41f9b246c4
- Google Keep JSON sample: https://github.com/usememos/memos/issues/4425
- Day One JSON dates: https://forums.dayoneapp.com/forums/topic/date-in-json-export/ ; export guide https://dayoneapp.com/guides/tips-and-tutorials/exporting-entries/
- Evernote ENEX: https://evernote.com/blog/how-evernotes-xml-export-format-works ; DTD http://xml.evernote.com/pub/evernote-export4.dtd
- Apple Notes exporters: https://github.com/adamgoth/apple-notes-export ; https://github.com/Kylmakalle/apple-notes-exporter ; https://apps.apple.com/us/app/exporter/id1099120373
- Obsidian daily notes: https://help.obsidian.md/Plugins/Daily+notes (mirror https://huggingface.co/spaces/anpigon/obsidian-qa-bot/raw/main/docs/obsidian-help/Plugins/Daily%20notes.md) ; https://dannb.org/blog/2022/obsidian-daily-note-template/
- Scrivener package layout and `.scrivx` sample: https://github.com/donnfelker/scrivener-skills/blob/main/skills/scrivener-format/references/package-layout.md ; https://preservation.tylerthorsted.com/2025/03/21/scrivener/
- dateparser settings: https://dateparser.readthedocs.io/en/latest/settings.html
- dateutil parser: https://dateutil.readthedocs.io/en/stable/parser.html ; fuzzy issues https://github.com/dateutil/dateutil/issues/95 , https://github.com/dateutil/dateutil/issues/138
