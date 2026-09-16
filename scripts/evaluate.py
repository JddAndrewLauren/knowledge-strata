"""Fixed invented retrieval cases; real embeddings require --real-model.

This measures retrieval and resolving citations, not generated answer quality.
"""
import argparse
import json
from pathlib import Path
import tempfile
import time

from strata.corpus import sources
from strata.embeddings import CachedEmbedder, FakeEmbedder, FastEmbedEmbedder
from strata.index import Index
from strata.ledger import Ledger
from strata.record import Record, Date

DOCUMENTS = {
    '2001-06-01-cutoff.txt': '2001-06-01\n\nPriya Venkataraman said the trade submission cutoff was eleven oclock.',
    '2001-06-02-disagreement.txt': '2001-06-02\n\nMarcus Idowu disputed the cutoff. He said trade submissions closed at noon.',
    'month.txt': 'June 2001\n\nThe desk planned a staffing review.',
    'undated.txt': 'Corinne kept the spare desk key in a blue envelope.',
    '2001-07-01-cutoff.txt': '2001-07-01\n\nThe trade submission cutoff changed to ten oclock.',
}
CASES = [
    ('exact', 'spare desk key', {}, {'undated.txt'}),
    ('paraphrase', 'Where was the backup office key stored?', {}, {'undated.txt'}),
    ('alias', 'cutoff', {'who': 'PV', 'kind': 'source'}, {'2001-06-01-cutoff.txt'}),
    ('partial-date', 'staffing review', {'from_': '2001-06-15', 'to': '2001-06-16'}, {'month.txt'}),
    ('unknown-date', 'spare desk key', {'from_': '2001-07'}, {'undated.txt'}),
    ('conflict', 'cutoff', {'to': '2001-06', 'kind': 'source'}, {'2001-06-01-cutoff.txt', '2001-06-02-disagreement.txt'}),
    ('date-cutoff', 'cutoff', {'from_': '2001-07', 'kind': 'source'}, {'2001-07-01-cutoff.txt'}),
]


def evaluate(real_model=False, model_cache=None):
    with tempfile.TemporaryDirectory(prefix='strata-evaluation-') as temporary:
        root = Path(temporary); corpus = root / 'sources'; corpus.mkdir()
        for name, text in DOCUMENTS.items():
            (corpus / name).write_text(text, encoding='utf-8')
        ledger = Ledger(root / 'ledger.db')
        report = sources.sync([corpus], ledger, cache_db=root / 'store.db')
        ids = {path.split('/', 1)[1]: value[0] for path, value in ledger.known_units().items()}
        model = FastEmbedEmbedder(cache_dir=model_cache) if real_model else FakeEmbedder()
        index = Index(root / 'index.db', ledger=ledger,
                      embedder=CachedEmbedder(model, root / 'store.db'), semantic=real_model)
        alias = Record(ref='notes/person/priya.md', kind='note', type='person',
                       date=Date('', 'unknown', 'day', ''), title='Priya Venkataraman',
                       paragraphs=('Priya uses the initials PV.',), aliases=('PV',))
        index.sync(list(report.records) + [alias])
        outcomes = []
        for name, query, scope, expected in CASES:
            start = time.perf_counter(); result = index.search(query=query, **scope)
            retrieved = [hit.ref.split(' ')[0] for hit in result.hits]
            wanted = {ids[path] for path in expected}
            correct = wanted <= set(retrieved)
            citations = all(index.read(hit.ref).body for hit in result.hits)
            outcomes.append({'case': name, 'evidence_recall': len(wanted & set(retrieved)) / len(wanted),
                             'top_1_expected': bool(retrieved and retrieved[0] in wanted),
                             'citations_resolve': bool(citations), 'passed': correct and bool(citations),
                             'seconds': round(time.perf_counter() - start, 4)})
        index.close(); ledger.close()
        return {'model': model.model_id, 'real_model': real_model, 'cases': outcomes,
                'limitation': 'Small invented retrieval set. Does not measure answer quality, abstention, or agent recovery.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--real-model', action='store_true')
    parser.add_argument('--model-cache')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = evaluate(args.real_model, args.model_cache)
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(text)
