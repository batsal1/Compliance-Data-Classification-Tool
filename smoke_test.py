"""Offline check of the whole pipeline: python smoke_test.py <file> [<file> ...]"""
import sys
from pathlib import Path

from core.ingest import load_document
from core.extract import extract_compliances, deduplicate
from core.graph import Ontology, build_instance_graph, imported_entities
from core import exporters as X

ONTO = Path('ontology/STARAM_Core_Modules_with_Compliance.owl')


def main(paths):
    onto = Ontology.from_path(str(ONTO)) if ONTO.exists() else None
    for p in paths:
        doc = load_document(Path(p).name, Path(p).read_bytes())
        rows, dupes = deduplicate(extract_compliances(doc))
        g = build_instance_graph(doc, rows, onto)
        imp = imported_entities(g, onto)
        missing = [i['CURIE'] for i in imp if i['In ontology'].startswith('NO')]
        prov = [{'Citation': x.citation, 'Level': x.level, 'Number': x.number, 'Heading': x.heading,
                 'Compliances': sum(1 for r in rows if r.provision_id == x.node_id),
                 'Text': (x.text or '')[:2000]} for x in doc.provisions()]
        xlsx = X.build_excel(doc, rows, imp, prov, {'provisions': len(doc.provisions()),
                                                    'counts': {'compliances': len(rows)}})
        out = Path(Path(p).stem + '_compliances.xlsx')
        out.write_bytes(xlsx)
        Path(Path(p).stem + '_graph.ttl').write_bytes(X.serialise_graph(g, 'turtle'))
        Path(Path(p).stem + '_compliances.json').write_bytes(X.build_json(doc, rows, imp, {'compliances': len(rows)}))
        print(f'{doc.title}: {len(doc.provisions())} provisions, {len(rows)} compliances, '
              f'{len(dupes)} duplicates dropped, {len(g)} triples, {len(imp)} ontology terms, '
              f'missing terms: {missing or "none"} → {out}')


if __name__ == '__main__':
    main(sys.argv[1:] or ['/mnt/user-data/uploads/Companies__Audit_And_Auditors__Rules__2014_json.json'])
