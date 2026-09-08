# Python stack for the index module: MCP SDK, sqlite-vec, fastembed, hybrid ranking

Wayfinder research ticket. Context: `docs/design.md` sections "Index" and "Two
tools". Reference implementation read (read-only): memoria `src/memoria/index.py`
(`search` ~l.893, `search_semantic` ~l.1044, vec DDL ~l.330-345,
`_load_vec_extension` ~l.632), `src/memoria/embeddings.py`, `src/memoria/mcp/
server.py`, `pyproject.toml`. All versions and dates verified against the PyPI and
GitHub release APIs on 2026-09-08.

Memoria's pins for reference: `mcp>=2.1,<3` (extra), `sqlite-vec==0.1.9`,
`fastembed==0.8.0`, model `BAAI/bge-small-en-v1.5` (384-d).

---

## 1. Python MCP SDK

**Package and version.** `mcp` on PyPI, **2.2.0**, uploaded 2026-09-07; requires
Python >=3.10. The wire types moved into a separate distribution `mcp-types`
(also 2.2.0), but `from mcp.types import ...` remains a permanent alias. The 1.x
line is still maintained in parallel (1.30.0 on the same day) but `pip install
mcp` now resolves to 2.x.
- https://pypi.org/project/mcp/ (PyPI JSON: latest 2.2.0, 2026-09-07T16:06)
- https://github.com/modelcontextprotocol/python-sdk/releases (v2.0.0 2026-07-28,
  v2.1.0 2026-08-24, v2.1.1 2026-08-25, v2.0.1 2026-08-26, v2.2.0 2026-09-07)

**The 2.0 breaking rename (2026-07-28).** `FastMCP` became `MCPServer`;
everything under `mcp.server.fastmcp.*` moved to `mcp.server.mcpserver.*`. The
old path is gone, not deprecated (2.1.1/2.0.1 only add an import-time warning
pointing at the migration guide). `FastMCPError -> MCPServerError`,
`ctx.fastmcp -> ctx.mcp_server`, `McpError -> MCPError`. The unrelated
standalone `fastmcp` package (v3) keeps its own `FastMCP` class; do not confuse
the two.
- https://py.sdk.modelcontextprotocol.io/whats-new/
- https://py.sdk.modelcontextprotocol.io/migration/

**How tools are declared.** Still the decorator on the high-level server:

```python
from mcp.server import MCPServer            # or: from mcp.server.mcpserver import MCPServer, Context
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("knowledge-strata", instructions="...")

@mcp.tool(title="Search the archive", structured_output=False)
def search(query: str = "", from_: str | None = None, to: str | None = None,
           who: str | None = None, kind: str | None = None) -> str:
    """docstring becomes the tool description; type hints become the input schema"""
    ...

mcp.run(transport="stdio")
```

Parameters may be scalars, `Literal[...]`, `Annotated[T, Field(...)]`, Optional
with defaults, or pydantic models. `title=` and `annotations=ToolAnnotations(
read_only_hint=True, open_world_hint=False)` are available; both tools here are
read-only, so set them. A `ctx: Context` parameter is injected when declared
(`mcp.get_context()` is removed).
- https://py.sdk.modelcontextprotocol.io/servers/tools/

**Text vs structured content.** The return annotation decides:
- `str` (and other scalars/lists): by default the SDK emits the value **twice** -
  as `TextContent` in `content` and as `{"result": "..."}` in
  `structured_content`, with an advertised `outputSchema`. The doc says so
  verbatim: "A tool that returns a plain str produces the result twice".
- pydantic `BaseModel` / `TypedDict` / dataclass / `dict[str, ...]`: a real object
  schema plus `structured_content`, no wrapping.
- `@mcp.tool(structured_output=False)`: "No output_schema, no wrapping, no
  validation. structured_content is None" - plain text only.
- `structured_output=True`: forces a schema; incompatible return types fail at
  import time.
- Returning a `CallToolResult` yourself passes through untouched (for mixed
  text + structured, or `_meta`).
- https://py.sdk.modelcontextprotocol.io/servers/structured-output/

For `search`/`read`, which return kilobytes of text, `structured_output=False`
avoids shipping every reply twice over stdio. Nothing downstream needs the
`{"result": ...}` wrapper.

**Errors.** `ToolError` raised inside a tool -> `CallToolResult(is_error=True)`
that the model sees. `MCPError` -> a JSON-RPC protocol error the model never
sees. Any other exception -> `-32603` with a sanitised message (handler
exceptions are logged privately since 2.1.0). Memoria's use of `ToolError` for
"bad ref" is the right one.

**Behaviour changes that matter here (no import error, so easy to miss).**
- Sync `def` tools now run on a worker thread (`anyio.to_thread.run_sync`), not
  the event loop. Memoria's connect-per-call `sqlite3` pattern is fine with
  that; a long-lived connection shared across calls would not be
  (`check_same_thread`). Keep connect-per-call.
- `ctx.info()/ctx.log()/ctx.debug()` are deprecated (SEP-2577,
  `MCPDeprecationWarning`); use the stdlib `logging` module.
- Streamable-HTTP/OAuth changes in 2.2.0 (redirect origin restriction, 30-min
  idle sessions, `validate_token_resource`) do not touch a stdio server.

**Is anything memoria relied on deprecated?** No. `from mcp.server import
MCPServer`, `from mcp.server.mcpserver.exceptions import ToolError`,
`@mcp.tool()` returning `str`, module-level repository state and `mcp.run()`
are all current in 2.2.0. The one thing to change is cosmetic: pass
`structured_output=False` (and `annotations`) on the text tools. Memoria's pin
`mcp>=2.1,<3` is correct for a fresh project.

---

## 2. sqlite-vec

**Version.** Stable **0.1.9** (PyPI and GitHub, 2026-03-31). Pre-releases
0.1.10-alpha.1..4 (2026-03-31 .. 2026-05-18) add approximate indexes
(`rescore`, experimental `ivf`, DiskANN), `INSERT OR REPLACE` support and
`ALTER TABLE RENAME`. Recent stable history: 0.1.7 (2026-03-17) real `DELETE`
with space reclamation and `<`/`<=`/`>`/`>=` constraints on `distance` in KNN
queries (for paging without a huge k); 0.1.8 (2026-03-30) npm packaging fix;
0.1.9 fixes DELETE on tables with metadata text columns > 12 chars.
- https://github.com/asg017/sqlite-vec/releases
- https://pypi.org/project/sqlite-vec/

Still pre-1.0; pin exactly, as memoria does. The ANN indexes are alpha and
irrelevant at this corpus size (flat brute-force over 10^4-10^5 384-d vectors is
milliseconds).

**Loading from Python.**

```python
import sqlite3, sqlite_vec
con = sqlite3.connect(path)
con.enable_load_extension(True)
sqlite_vec.load(con)
con.enable_load_extension(False)
con.execute("INSERT INTO v(rowid, embedding) VALUES (?, ?)",
            (1, sqlite_vec.serialize_float32(vec)))   # or np.float32 array directly
```

Caveats: macOS system Python lacks `enable_load_extension` (`AttributeError`);
some Linux distro builds raise `sqlite3.NotSupportedError`. Memoria's
`VectorExtensionUnavailable` degrade-gracefully path (#153) is worth keeping.
Check `sqlite3.sqlite_version`: `LIMIT n` as the k-form needs SQLite >= 3.41
(use `k = ?` otherwise); `rowid in (...)` and metadata `in (...)` need >= 3.38
(`sqlite3_vtab_in`); `FULL OUTER JOIN` (for the RRF SQL below) needs >= 3.39.
- https://alexgarcia.xyz/sqlite-vec/python.html

**KNN constraints - what vec0's planner actually accepts.** From
`vec0BestIndex` in `sqlite-vec.c` (main, read 2026-09-08), the only query plans
are: (1) KNN, (2) point lookup by rowid `=`, (3) full scan. A KNN plan is chosen
when there is a `MATCH` on the vector column, and it then requires:
- exactly one of `k = ?` or `LIMIT ?` ("Only LIMIT or 'k =?' can be provided,
  not both"; missing both -> "A LIMIT or 'k = ?' constraint is required on vec0
  knn queries.");
- at most one `ORDER BY distance`;
- optionally **one** `rowid in (...)` constraint ("only 1 'rowid in (..)'
  operator is allowed") - the declared primary-key column stands in for rowid,
  so memoria's `anchor IN (SELECT anchor FROM paragraphs WHERE ...)` **is this
  first-class plan**, not a hack;
- optionally `=` constraints on partition-key columns;
- optionally `=`, `!=`, `<`, `<=`, `>`, `>=`, `BETWEEN`, and `in (...)` (INTEGER
  or TEXT only) on metadata columns; `IS NULL`, `LIKE`, `GLOB`, `REGEXP` and
  scalar functions "will result in an error or incorrect results";
- optionally `<`/`<=`/`>`/`>=` on `distance` (0.1.7+).

**Why a JOIN fails.** When the vec0 table is joined, SQLite does not hand the
outer `LIMIT` to the virtual table, so the KNN plan sees no k and errors (issue
#96, still open). The maintainer's statement in #196 (2025-02-07): "sqlite-vec
is limited by SQLite's extension capabilities for supporting JOIN filters on our
virtual table. SQLite's query planner is complicated and we don't have full
access to all possible query plans from a virtual table, so 100% JOIN support
may not be possible." Documented shape: KNN in a CTE with `k = ?`, then join the
CTE back to the source table.
- https://github.com/asg017/sqlite-vec/issues/96
- https://github.com/asg017/sqlite-vec/issues/196
- https://alexgarcia.xyz/sqlite-vec/features/knn.html

**Do metadata columns / partition keys let us filter inside the KNN now?** Yes,
since 0.1.6 (Nov 2024), and they remain the documented answer in 0.1.9:

```sql
CREATE VIRTUAL TABLE paragraph_vectors USING vec0(
  anchor TEXT PRIMARY KEY,
  month  INTEGER PARTITION KEY,      -- e.g. 202603; shards the index
  day    INTEGER,                    -- metadata: 20260314, filterable with BETWEEN
  kind   TEXT,                       -- metadata: = / in (...)
  +src   TEXT,                       -- auxiliary: returned, never filtered
  embedding FLOAT[384] distance_metric=cosine
);
SELECT anchor, distance FROM paragraph_vectors
 WHERE embedding MATCH ? AND k = ?
   AND month = 202603 AND day BETWEEN ? AND ? AND kind IN ('journal','email');
```

Limits: <= 16 metadata columns, <= 4 partition keys (the docs advise caution
beyond one), metadata NULLs unsupported, partition keys should hold "100's or
1000's of vectors" each or the sharding hurts. A partition-key `=` restricts the
scan to that shard (the blog reports ~3x on a 5-of-30-years query); metadata
constraints are evaluated inside the scan; `rowid in (...)` also runs inside the
scan but the maintainer notes a large IN-list "would currently be slow!" relative
to metadata columns. For a `who` filter (aliases expanded from a note into a set
of records) the natural form is still a `rowid in (SELECT ...)` over the
paragraphs table; date and kind can move into metadata/partition columns if it
is ever measured slow. Either way, all filters run before k is applied, so k
hits come back filtered - no post-filter starvation.
- https://alexgarcia.xyz/blog/2024/sqlite-vec-metadata-release/index.html
- https://alexgarcia.xyz/sqlite-vec/features/vec0.html

**Upsert.** `INSERT OR REPLACE` is only in the 0.1.10 alphas. On 0.1.9,
`sync` must `DELETE ... WHERE anchor = ?` then `INSERT` inside one transaction.
DELETE is real since 0.1.7 (memoria never needed it: it dropped and rebuilt).

**Cosine vs L2.** bge embeddings are L2-normalised, so `distance_metric=cosine`
and the default L2 rank identically; declare cosine anyway so the choice of
model later cannot silently change the semantics.

---

## 3. fastembed and the small English embedder

**Version.** **0.8.0** (PyPI 2026-03-23); requires Python >=3.10 (3.9 dropped);
auto-detects CUDA; onnxruntime/pillow pins fixed for Python 3.14; honours
`HF_HUB_OFFLINE`. 0.7.4 (2025-12-05) stopped making network calls for cached
models and added `token_count`. Memoria's exact pin `fastembed==0.8.0` is the
current release.
- https://pypi.org/project/fastembed/
- https://github.com/qdrant/fastembed/releases

**Dense English models in the 0.8.0 registry that are small enough for CPU**
(from `fastembed/text/onnx_embedding.py`, `pooled_embedding.py`,
`pooled_normalized_embedding.py` on main; sizes are the ONNX artefact fastembed
downloads):

| model | dim | on disk | ctx | prefix? | notes |
|---|---|---|---|---|---|
| `BAAI/bge-small-en-v1.5` | 384 | 0.067 GB (int8 `qdrant/bge-small-en-v1.5-onnx-q`) | 512 | optional | "not so necessary" |
| `snowflake/snowflake-arctic-embed-xs` | 384 | 0.09 GB | 512 | **required** on queries | |
| `snowflake/snowflake-arctic-embed-s` | 384 | 0.13 GB | 512 | **required** on queries | |
| `sentence-transformers/all-MiniLM-L6-v2` | 384 | 0.09 GB | 256 | no | 2022-era |
| `jinaai/jina-embeddings-v2-small-en` | 512 | 0.12 GB | 8192 | no | |
| `nomic-ai/nomic-embed-text-v1.5-Q` | 768 | 0.13 GB | 8192 | **required** (`search_query:`/`search_document:`) | |
| `BAAI/bge-base-en-v1.5` | 768 | 0.21 GB | 512 | optional | 4x the compute of small |
| `minishlab/potion-retrieval-32M` | 512 | 0.129 GB | - | no | static (model2vec), fastest, weakest |

Not in the registry: IBM `granite-embedding-small-english-r2`,
`mxbai-embed-xsmall-v1`, EmbeddingGemma. fastembed 0.8 can register a custom
ONNX model via `TextEmbedding.add_custom_model(model, pooling, normalization,
sources=ModelSource(hf=...), dim, model_file="onnx/model.onnx")`, but it needs an
ONNX file in the HF repo (mxbai-xsmall has one; granite-small-r2 does **not** -
`/api/models/...` lists no `onnx/` siblings, so it would need a local export).
- https://qdrant.github.io/fastembed/examples/Supported_Models/
- https://raw.githubusercontent.com/qdrant/fastembed/main/fastembed/text/onnx_embedding.py
- https://raw.githubusercontent.com/qdrant/fastembed/main/fastembed/text/text_embedding.py

**fastembed does not add query prefixes for you.** `query_embed` in
`text_embedding_base.py` simply calls `embed`; there is no
"Represent this sentence..." or "search_query:" string anywhere in the text
embedding classes. If a model wants a prefix, the caller must prepend it.
Memoria embeds queries with no prefix, which is acceptable for bge v1.5 and
wrong for arctic/nomic.

**Benchmarks (retrieval, NDCG@10).** Two scales are in circulation; do not mix
them.

MTEB(eng) v1 retrieval, 15 BEIR sets (model cards):
- `snowflake-arctic-embed-s` 51.98 (33M) - requires prefix
- `bge-small-en-v1.5` 51.68 (33M) - optional prefix
- `snowflake-arctic-embed-xs` 50.15 (22M) - requires prefix
- `e5-small-v2` 49.04 (33M)
- `mxbai-embed-xsmall-v1` 42.80 (24M; vs 41.56 for MiniLM on the same run)
- `all-MiniLM-L6-v2` 41.95 (22M)

MTEB v2 English Retrieval (10 tasks) plus long-document sets, from the granite
card (IBM's run, same harness for both rows):

| model | params | dim | MTEB-v2 Retrieval | BEIR(15) | MLDR (long docs) | LongEmbed | speed docs/s (H100) |
|---|---|---|---|---|---|---|---|
| `bge-small-en-v1.5` | 33M | 384 | **53.9** | - | 31.4 | 32.1 | 138 |
| `granite-embedding-small-english-r2` | 47M | 384 | **53.9** | 50.9 | 40.1 | 61.9 | 199 |
| `e5-small-v2` | 33M | 384 | 48.5 | - | 29.9 | 40.7 | 138 |

Sources:
- https://huggingface.co/BAAI/bge-small-en-v1.5 (MTEB avg 62.17, retrieval
  51.68; the instruction "Represent this sentence for searching relevant
  passages: " is optional in v1.5 with "slight degradation" without it on
  short-query -> passage retrieval)
- https://huggingface.co/Snowflake/snowflake-arctic-embed-s (51.98; prefix on
  queries only)
- https://huggingface.co/Snowflake/snowflake-arctic-embed-xs (50.15)
- https://huggingface.co/ibm-granite/granite-embedding-small-english-r2 (47M,
  384-d, 8192 ctx, Apache-2.0, released 2025-08-15, no MS MARCO in training, no
  prefix documented)
- https://www.mixedbread.com/blog/mxbai-embed-xsmall-v1 (per-dataset table)

**Verdict for personal prose at paragraph granularity.** Nothing small
*dominates* bge-small-en-v1.5. arctic-embed-s is +0.3 on the v1 scale at 2x the
download and a mandatory query prefix; granite-small-r2 ties it exactly on
MTEB-v2 retrieval and only pulls ahead on long-document sets (MLDR +8.7,
LongEmbed +29.8), which matter if you embed whole emails or day-entries, not if
you embed paragraphs as memoria does. MiniLM/mxbai-xsmall are ~10 points behind.
bge-small is also the most downloaded (65M) and is the one fastembed ships as a
67 MB int8 ONNX. Keep it. Two cheap improvements over memoria: (a) prepend the
bge query instruction on the query side only - passages are unaffected, so this
can be flipped later without a rebuild; (b) record model name + dims in the
index header so a model change forces a rebuild rather than mixing spaces.
If paragraph-level embedding later proves too fine (whole-record semantics
wanted), granite-small-r2 with an 8192 context is the candidate to revisit, at
the cost of a local ONNX export and `add_custom_model`.

---

## 4. Hybrid ranking

**Reciprocal rank fusion.** Cormack, Clarke and Buettcher, "Reciprocal Rank
Fusion outperforms Condorcet and individual Rank Learning Methods", SIGIR 2009:

    RRFscore(d) = sum over rankers r of  1 / (k + rank_r(d)),   k = 60

k = 60 was fixed in a pilot study; it damps the effect of a single ranker
placing an outlier at rank 1. Elasticsearch's implementation defaults to
`rank_constant = 60` and adds `rank_window_size`: the top-N from each retriever
that participate in fusion before truncation to the requested size - i.e.
"top-N from each then fuse" is the standard shape, not an approximation of it.
Weights per ranker are a common extension (`w_r / (k + rank)`); with two rankers
and unknown query intent, equal weights are the defensible default.
- https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf
- https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion

**Why RRF and not score normalisation.** FTS5 `rank` is bm25 multiplied by -1
(lower is better, `ORDER BY rank`), vec0 `distance` is cosine/L2 (lower is
better) - two unrelated scales. Simon Willison's summary of the sqlite-vec
approach: the scores "are meaningless in comparison to each other", so RRF uses
positions only and sidesteps normalisation.
- https://www.sqlite.org/fts5.html (bm25 sign convention, `rank` column,
  `snippet()` takes five args, max 64 tokens)
- https://simonwillison.net/2024/Oct/4/hybrid-full-text-search-and-vector-search-with-sqlite/

**SQLite-native fusion in one statement.** Alex Garcia's pattern (Oct 2024),
adapted to memoria's table names; the query vector is computed in Python first
(fastembed), then one SQL statement does both searches and the fusion:

```sql
WITH vec AS (
  SELECT anchor, row_number() OVER (ORDER BY distance) AS rn
    FROM paragraph_vectors
   WHERE embedding MATCH :qvec AND k = :n
     AND anchor IN (SELECT anchor FROM paragraphs WHERE <filters>)   -- or metadata cols
),
fts AS (
  SELECT records.anchor, row_number() OVER (ORDER BY rank) AS rn
    FROM records JOIN paragraphs USING (anchor)
   WHERE records MATCH :q AND <filters>
   LIMIT :n
),
fused AS (
  SELECT COALESCE(fts.anchor, vec.anchor) AS anchor,
         COALESCE(1.0/(:k + fts.rn), 0) * :w_fts
       + COALESCE(1.0/(:k + vec.rn), 0) * :w_vec AS score
    FROM fts FULL OUTER JOIN vec ON vec.anchor = fts.anchor
)
SELECT p.anchor, p.src_id, p.date, p.kind, fused.score
  FROM fused JOIN paragraphs p USING (anchor)
 ORDER BY fused.score DESC;
```

Parameters in the original: `k` (top-N per branch) 10, `rrf_k` 60, weights 1.0.
The KNN branch must stay in its own CTE with `k = ?` (section 2). `FULL OUTER
JOIN` needs SQLite >= 3.39 (2022); the same can be written as two LEFT JOINs
unioned if an older library turns up. Doing the fusion in Python over two result
lists is equally fine at this scale and easier to unit-test; the SQL form's
advantage is a single round-trip and no ordering bugs like memoria's
"re-apply KNN order after the lookup" comment (`index.py` ~l.1120).
- https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html

**Choosing N.** FTS returns an exact, possibly large match set; KNN returns
exactly k. A paragraph that is a strong lexical match but outside the vector
top-N still surfaces via the FTS branch (and vice versa); with N = 20-50 per
branch and a 60-constant, anything in both top lists floats to the top. For a
timeline browse (empty query) skip both rankers and order by date.

---

## 5. What this changes about the index module's interface

The two-tool surface (`search(query, from, to, who, kind)`, `read(ref)`) and
the header-then-hits shape survive unchanged. Five things inside the seam do
move, and one header field needs a definition:

1. **"total hits" and "counts by month" must come from the lexical/filter side,
   not the fused list.** KNN cannot say how many paragraphs are "about" a query;
   it returns k. Define: total = number of paragraphs matching the FTS query
   under the filters (exact, one `COUNT(*)`/`GROUP BY month` over the joined
   FTS+paragraphs rows); counts by month come from the same grouping; for an
   empty query, both come from the date-filtered `paragraphs` table. The fused
   ranking then orders the hits actually returned. This keeps the header
   deterministic (the design's requirement) and cheap. State this in
   `design.md`'s header description so the skill never reads "total" as a
   semantic count.
2. **One filter predicate, two placements.** The predicate builder over
   `paragraphs` (memoria's `filter_predicate`) serves the FTS branch as a JOIN
   and the vector branch as `anchor IN (SELECT ...)`. That is a supported vec0
   plan, so no schema gymnastics are needed on day one; if a profile ever shows
   the IN-list slow, promote `month` to a partition key and `day`/`kind` to
   metadata columns (section 2) - a change confined to the vec DDL and the vec
   branch's WHERE clause, invisible to callers. `who` stays a subquery (alias
   expansion is a text predicate vec0 cannot express).
3. **`sync` needs delete-then-insert on vec0.** No `INSERT OR REPLACE` on
   0.1.9; wrap `DELETE`+`INSERT` per changed anchor in the same transaction as
   the FTS/paragraphs writes. Memoria's rebuild-from-empty path has no such code
   to reuse; write it fresh (small).
4. **Embed queries with the bge instruction prefix; embed passages bare.**
   Query-side only, so it is reversible without touching the index. Store the
   embedder id (`bge-small-en-v1.5`, 384, prefix policy) in an index metadata
   row; `sync` refuses to mix and triggers rebuild on mismatch.
5. **Tools: `structured_output=False`, `annotations=ToolAnnotations(
   read_only_hint=True, open_world_hint=False)`.** Return `str`. Raise `ToolError`
   for a bad ref. Sync `def` is fine (runs on a worker thread; connect per call).
   Use stdlib `logging`, not `ctx.info`.
6. **Runtime checks at connect.** `sqlite3.sqlite_version >= 3.38` for
   `rowid in (...)` (3.39 if the FULL OUTER JOIN form is used); extension loading
   available (else degrade to FTS-only with a header line saying so, as memoria's
   `VectorExtensionUnavailable` does). Use `k = ?` not `LIMIT` in the KNN branch
   so 3.38-3.40 libraries also work.

Nothing here requires a third tool, a write tool, or a change to `read(ref)`.

---

## Recommendation

- **Pins:** `mcp>=2.2,<3` (or `>=2.1,<3` as memoria; both fine),
  `sqlite-vec==0.1.9`, `fastembed==0.8.0`, model `BAAI/bge-small-en-v1.5`
  (384-d, 67 MB int8 ONNX, CPU). Do not adopt the sqlite-vec 0.1.10 alphas; the
  ANN indexes solve a problem this corpus does not have.
- **Keep bge-small-en-v1.5.** It ties the best 2025 small English model
  (granite-small-r2) on MTEB-v2 retrieval at paragraph granularity, needs no
  mandatory prefix, and is the one fastembed ships pre-quantised. Add the
  optional query instruction on the query side. Revisit granite-small-r2 only if
  whole-record (long-context) embedding becomes the design.
- **Vector filtering:** reuse memoria's `anchor IN (SELECT ...)` - it is the
  `rowid in (...)` KNN plan sqlite-vec documents - behind the same predicate
  builder the FTS branch uses. Metadata/partition columns are the measured-slow
  upgrade, not the starting point.
- **Fusion:** RRF with k = 60, equal weights, top-N = 20-50 from each branch,
  computed either in one SQL statement (CTE + FULL OUTER JOIN, SQLite >= 3.39)
  or in Python over two lists; prefer whichever is easier to test. Header totals
  and month counts come from the FTS/filter side; the fused order applies only
  to the returned hits.
- **MCP:** `MCPServer` + `@mcp.tool(structured_output=False, annotations=...)`
  returning `str`; `ToolError` for bad input; stdlib logging; connect-per-call
  because sync tools now run on worker threads. Nothing memoria used is
  deprecated.
- **Interface impact:** none to the two tools' signatures; define "total hits"
  as the exact filtered lexical count in `design.md`, and add delete-then-insert
  upsert plus an embedder-identity row to `sync`.
