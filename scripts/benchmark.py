"""Generated archive benchmark. No proprietary inputs or model download by default.

Run each size in a fresh process so peak RSS is attributable to that run.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time

from strata.corpus import sources
from strata.embeddings import CachedEmbedder, FakeEmbedder, FastEmbedEmbedder
from strata.index import Index
from strata.ledger import Ledger


def run(count, real_model=False, model_cache=None):
    def timed(function):
        start = time.perf_counter(); result = function()
        return result, round(time.perf_counter() - start, 4)
    with tempfile.TemporaryDirectory(prefix='strata-benchmark-') as temporary:
        root = Path(temporary); corpus = root / 'corpus'; corpus.mkdir()
        for number in range(count):
            text = f'2001-06-01\n\nRecord {number}: the desk recorded its ordinary daily operations.'
            if number % 100 == 0:
                text += ' The cutoff was eleven oclock.'
            (corpus / f'{number:06d}.txt').write_text(text, encoding='utf-8')
        ledger = Ledger(root / 'ledger.db')
        model = FastEmbedEmbedder(cache_dir=model_cache) if real_model else FakeEmbedder()
        index = Index(root / 'index.db', ledger=ledger,
                      embedder=CachedEmbedder(model, root / 'store.db'))
        def refresh():
            report = sources.sync([corpus], ledger, cache_db=root / 'store.db')
            index.sync(list(report.records))
            return report
        _, cold = timed(refresh)
        _, unchanged = timed(refresh)
        (corpus / '000000.txt').write_text('2001-06-01\n\nThe cutoff changed to noon.', encoding='utf-8')
        _, edit = timed(refresh)
        _, search = timed(lambda: index.search(query='cutoff', kind='source'))
        def enumerate_all():
            page = index.search(kind='source'); seen = set(); pages = 0
            while True:
                pages += 1
                for hit in page.hits:
                    assert hit.ref not in seen
                    seen.add(hit.ref)
                if not page.continuation:
                    break
                page = index.search(cursor=page.continuation)
            assert len(seen) == count
            return pages
        pages, enumeration = timed(enumerate_all)
        index.close(); ledger.close()
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            peak_mb = peak / (1024 * 1024 if sys.platform == 'darwin' else 1024)
        except ImportError:
            peak_mb = None
        return {'records': count, 'model': model.model_id, 'real_model': real_model,
                'python': platform.python_version(), 'platform': platform.platform(),
                'cpu_count': os.cpu_count(), 'cold_seconds': cold, 'unchanged_seconds': unchanged,
                'one_edit_seconds': edit, 'search_after_refresh_seconds': search,
                'browse_enumeration_seconds': enumeration, 'browse_pages': pages,
                'peak_rss_mb': round(peak_mb, 2) if peak_mb is not None else None,
                'index_bytes': (root / 'index.db').stat().st_size}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--records', type=int, default=1000)
    parser.add_argument('--real-model', action='store_true')
    parser.add_argument('--model-cache')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.records <= 0:
        parser.error('--records must be positive')
    result = run(args.records, args.real_model, args.model_cache)
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(text)
