"""STARAM Compliance Extractor — Streamlit application.

Upload legislation or a contract, extract obligations, classify them, map them onto the STARAM
ontology, explore the knowledge graph and export Excel / JSON / RDF.

Run:  streamlit run app.py
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from core.ingest import load_document                                   # noqa: E402
from core.extract import extract_compliances, deduplicate, Compliance   # noqa: E402
from core import graph as G                                             # noqa: E402
from core import exporters as X                                         # noqa: E402
from core import llm                                                    # noqa: E402

st.set_page_config(page_title='STARAM Compliance Extractor', page_icon='⚖️', layout='wide')

DEFAULT_ONTOLOGY = os.environ.get('STARAM_ONTOLOGY', 'ontology/STARAM_Core_Modules_with_Compliance.owl')

CSS = """
<style>
section.main > div {padding-top: 1rem;}
div[data-testid="stMetricValue"] {font-size: 1.4rem;}
.small {font-size: 0.82rem; color: #64748b;}
.pill {display:inline-block;padding:2px 8px;border-radius:10px;font-size:0.75rem;margin-right:4px;}
.HIGH {background:#fdecea;color:#b3261e;} .MEDIUM {background:#fef3e2;color:#a15c07;} .LOW {background:#e8f5e9;color:#1b5e20;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------- cached loaders

@st.cache_resource(show_spinner='Loading ontology…')
def load_ontology_from_path(path: str):
    return G.Ontology.from_path(path)


@st.cache_resource(show_spinner='Loading ontology…')
def load_ontology_from_bytes(data: bytes, name: str):
    return G.Ontology.from_bytes(data, name)


@st.cache_data(show_spinner='Reading document…')
def cached_document(name: str, data: bytes):
    return load_document(name, data)


@st.cache_data(show_spinner='Extracting obligations…')
def cached_extract(name: str, data: bytes, include_permissions: bool, min_conf: float):
    doc = load_document(name, data)
    rows = extract_compliances(doc, min_confidence=min_conf, include_permissions=include_permissions)
    rows, dupes = deduplicate(rows)
    return doc, rows, dupes


def risk_pill(value: str) -> str:
    return f'<span class="pill {value}">{value}</span>'


# --------------------------------------------------------------------------- sidebar

st.sidebar.title('⚖️ STARAM Compliance')
st.sidebar.caption('Legislation & contract → compliances → knowledge graph')

uploaded = st.sidebar.file_uploader(
    'Upload legislation or contract', type=['pdf', 'docx', 'json', 'txt', 'md'], accept_multiple_files=True)

st.sidebar.markdown('---')
onto_file = st.sidebar.file_uploader('Ontology (OWL / TTL / JSON-LD)', type=['owl', 'rdf', 'xml', 'ttl', 'jsonld', 'json'])
onto_path = st.sidebar.text_input('…or ontology path', value=DEFAULT_ONTOLOGY)

ontology = None
onto_error = ''
try:
    if onto_file is not None:
        ontology = load_ontology_from_bytes(onto_file.getvalue(), onto_file.name)
    elif onto_path and Path(onto_path).exists():
        ontology = load_ontology_from_path(onto_path)
    else:
        onto_error = 'No ontology loaded — entity explorer and IRI mapping are disabled.'
except Exception as exc:
    onto_error = f'Ontology could not be parsed: {exc}'

if ontology is not None:
    st.sidebar.success(f'Ontology: {len(ontology.g):,} triples · {len(ontology._index):,} entities')
else:
    st.sidebar.warning(onto_error)

st.sidebar.markdown('---')
st.sidebar.subheader('Extraction settings')
include_permissions = st.sidebar.checkbox('Include permissive provisions ("may")', value=False)
min_conf = st.sidebar.slider('Minimum confidence', 0.0, 1.0, 0.0, 0.05)
use_llm = st.sidebar.checkbox('Refine with Claude (optional)', value=False,
                              help='Uses ANTHROPIC_API_KEY. Risk is always recomputed locally.')
api_key = ''
if use_llm:
    api_key = st.sidebar.text_input('Anthropic API key', type='password',
                                    value=os.environ.get('ANTHROPIC_API_KEY', ''))

if not uploaded:
    st.title('STARAM Compliance Extractor')
    st.markdown("""
Upload one or more documents on the left to begin. The application will:

1. **Parse** the document into a provision tree (Parts, Rules/Sections, sub-rules, clauses, provisos, forms).
2. **Extract** every actionable obligation, with the actor, action, trigger, deadline, form, evidence and penalty.
3. **Classify** each obligation into the repository's controlled vocabularies and run the deterministic risk engine.
4. **Map** everything onto the STARAM ontology, showing each imported entity and its IRI.
5. **Export** Excel, JSON and RDF (Turtle / RDF-XML / JSON-LD), and let you explore any entity of the graph.

Supported inputs: PDF, DOCX, JSON (Akoma-Ntoso style), TXT, MD — legislation or contracts.
""")
    if ontology is not None:
        st.subheader('Ontology loaded')
        c1, c2, c3 = st.columns(3)
        kinds = Counter(e.kind for e in ontology._index.values())
        c1.metric('Triples', f'{len(ontology.g):,}')
        c2.metric('Classes', f'{kinds.get("Class", 0):,}')
        c3.metric('Properties', f'{kinds.get("ObjectProperty", 0) + kinds.get("DatatypeProperty", 0):,}')
    st.stop()

names = [f.name for f in uploaded]
choice = st.sidebar.selectbox('Active document', names)
active = next(f for f in uploaded if f.name == choice)

doc, rows, dupes = cached_extract(active.name, active.getvalue(), include_permissions, min_conf)

if use_llm and api_key:
    if st.sidebar.button('Run Claude refinement'):
        bar = st.sidebar.progress(0.0)
        rows = llm.enrich(rows, api_key=api_key, progress=lambda p: bar.progress(p))
        st.session_state['llm_rows'] = rows
        st.sidebar.success('Refinement complete')
if 'llm_rows' in st.session_state and use_llm:
    rows = st.session_state['llm_rows']

igraph = G.build_instance_graph(doc, rows, ontology)
imported = G.imported_entities(igraph, ontology)

# --------------------------------------------------------------------------- header

st.title(doc.title or active.name)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric('Provisions', len(doc.provisions()))
c2.metric('Compliances', len(rows))
c3.metric('High risk', sum(1 for r in rows if r.risk == 'HIGH'))
c4.metric('Flagged', sum(1 for r in rows if r.review_flag))
c5.metric('Graph triples', f'{len(igraph):,}')

tabs = st.tabs(['📄 Document', '📋 Compliance table', '🧭 Classification', '🕸️ Knowledge graph',
                '🔎 Entity explorer', '⬇️ Exports'])

# --------------------------------------------------------------------------- 1. document

with tabs[0]:
    left, right = st.columns([1, 2])
    with left:
        st.subheader('Detected metadata')
        st.table(pd.DataFrame([
            ('Document type', doc.doc_type), ('Instrument kind', doc.instrument_kind),
            ('Parent Act', doc.parent_act or '—'), ('Year', doc.year or '—'),
            ('Number', doc.number or '—'), ('Commencement', doc.commencement or '—'),
            ('Pages', doc.pages or '—'), ('Source file', doc.source_file)],
            columns=['Field', 'Value']).set_index('Field'))
        if doc.language_note:
            st.caption(doc.language_note)
        levels = Counter(p.level for p in doc.provisions())
        st.subheader('Provision structure')
        st.dataframe(pd.DataFrame(sorted(levels.items(), key=lambda x: -x[1]), columns=['Level', 'Count']),
                     use_container_width=True, hide_index=True)
    with right:
        st.subheader('Provision browser')
        units = [p for p in doc.provisions() if p.level in ('rule', 'section', 'clause', 'schedule')]
        by_cite = {f'{p.citation or p.node_id} — {p.heading[:60]}': p for p in units}
        if by_cite:
            pick = st.selectbox('Provision', list(by_cite))
            p = by_cite[pick]
            st.markdown(f'**{p.citation}** · {p.heading}')
            st.text_area('Text', p.full_text()[:6000], height=260, label_visibility='collapsed')
            linked = [r for r in rows if r.provision_id == p.node_id or (r.citation or '').startswith(p.citation or '§')]
            st.caption(f'{len(linked)} compliance(s) extracted from this provision and its sub-provisions')
            for r in linked[:12]:
                st.markdown(f'- {risk_pill(r.risk)} **{r.compliance_name}** — {r.actor} · {r.citation}',
                            unsafe_allow_html=True)
        else:
            st.info('No numbered units detected; see the raw text below.')
            st.text_area('Raw text', doc.raw_text[:8000], height=300)

# --------------------------------------------------------------------------- 2. compliance table

with tabs[1]:
    st.subheader('Compliances / obligations')
    st.caption('The first 29 columns are the compliance repository headings; the rest capture the granular '
               'legal reasoning layer and provenance.')
    records = X.rows_to_records(rows)
    df = pd.DataFrame(records)
    f1, f2, f3, f4 = st.columns(4)
    risk_sel = f1.multiselect('Risk', sorted(df['Risk'].unique()) if len(df) else [])
    actor_sel = f2.multiselect('Actor', sorted(df['Actor'].unique()) if len(df) else [])
    nature_sel = f3.multiselect('Compliance Nature', sorted(df['Compliance Nature'].unique()) if len(df) else [])
    only_flagged = f4.checkbox('Only flagged for review')
    view = df.copy()
    if risk_sel:
        view = view[view['Risk'].isin(risk_sel)]
    if actor_sel:
        view = view[view['Actor'].isin(actor_sel)]
    if nature_sel:
        view = view[view['Compliance Nature'].isin(nature_sel)]
    if only_flagged:
        view = view[view['Review Flag'] != '']
    st.caption(f'{len(view)} of {len(df)} rows')
    st.dataframe(view, use_container_width=True, height=460)
    if dupes:
        with st.expander(f'De-duplicated rows ({len(dupes)})'):
            st.dataframe(pd.DataFrame(dupes, columns=['Dropped (citation)', 'Kept (citation)']),
                         use_container_width=True, hide_index=True)
    st.markdown('### Row detail')
    if len(view):
        idx = st.selectbox('Compliance', view.index,
                           format_func=lambda i: f"{df.loc[i, 'Provision']} — {df.loc[i, 'Compliance Name'][:70]}")
        row = rows[idx]
        d1, d2 = st.columns(2)
        with d1:
            st.markdown(f"**Actor** · {row.actor} (`cmp:{row.actor_class}`)")
            st.markdown(f"**Action** · {row.legal_action} — {row.action_object}")
            st.markdown(f"**Trigger** · {row.trigger or '—'}")
            st.markdown(f"**Deadline** · {row.statutory_due_date or '—'}")
            st.markdown(f"**On Date** · {row.on_date} ({row.periodicity}/{row.start_end_period}) — {row.on_date_calc}")
            st.markdown(f"**Form** · {row.form_no} · **Evidence** · {row.proof_of_compliance}")
        with d2:
            st.markdown(f"**Authority** · {row.statutory_authority}")
            st.markdown(f"**Nature** · {row.compliance_nature} · **Law category** · {row.law_category}")
            st.markdown(f"**Risk** · {risk_pill(row.risk)} (max ₹{row.max_penalty_amount:,.0f}, "
                        f"imprisonment {row.imprisonment}, continuing {row.continuing_penalty}, "
                        f"licence {row.cancellation_suspension})", unsafe_allow_html=True)
            st.markdown(f"**Thresholds** · {'; '.join(row.thresholds) or '—'}")
            st.markdown(f"**Cross-references** · {', '.join(row.cross_references) or '—'}")
            st.markdown(f"**Confidence** · {row.confidence}")
        if row.review_note:
            st.warning(f'**{row.review_flag}** — {row.review_note}')
        st.text_area('Source text', row.source_text, height=120)

# --------------------------------------------------------------------------- 3. classification

with tabs[2]:
    st.subheader('Classification')
    if not rows:
        st.info('No compliances extracted.')
    else:
        g1, g2, g3 = st.columns(3)
        g1.markdown('**By risk**')
        g1.bar_chart(pd.Series(Counter(r.risk for r in rows)).sort_values(ascending=False))
        g2.markdown('**By compliance nature**')
        g2.bar_chart(pd.Series(Counter(r.compliance_nature for r in rows)).sort_values(ascending=False))
        g3.markdown('**By periodicity**')
        g3.bar_chart(pd.Series(Counter(r.periodicity for r in rows)).sort_values(ascending=False))
        h1, h2 = st.columns(2)
        h1.markdown('**By actor**')
        h1.dataframe(pd.DataFrame(Counter(r.actor for r in rows).most_common(), columns=['Actor', 'Compliances']),
                     use_container_width=True, hide_index=True, height=320)
        h2.markdown('**By statutory authority**')
        h2.dataframe(pd.DataFrame(Counter(r.statutory_authority for r in rows).most_common(),
                                  columns=['Authority', 'Compliances']),
                     use_container_width=True, hide_index=True, height=320)
        st.markdown('**Deontic classification** (obligation / prohibition / permission)')
        st.dataframe(pd.DataFrame(Counter(r.deontic for r in rows).most_common(), columns=['Type', 'Count']),
                     use_container_width=True, hide_index=True)
        st.markdown('**Risk engine trace** — each row shows the first rule that fired')
        st.dataframe(pd.DataFrame([{'Compliance': r.compliance_name[:60], 'Max penalty': r.max_penalty_amount,
                                    'Imprisonment': r.imprisonment, 'Continuing': r.continuing_penalty,
                                    'Licence': r.cancellation_suspension, 'Risk': r.risk,
                                    'Rule': r.on_date_calc.split('Risk Rule')[-1].strip()} for r in rows]),
                     use_container_width=True, height=300)

# --------------------------------------------------------------------------- 4. knowledge graph

with tabs[3]:
    st.subheader('Knowledge graph of this document')
    st.caption('Every compliance is an instance of cmp:ComplianceRequirement, linked to its provision, actor, '
               'authority, form, event, penalty and the ontology\'s controlled vocabularies.')
    k1, k2, k3 = st.columns(3)
    k1.metric('Triples', f'{len(igraph):,}')
    k2.metric('Ontology terms used', len(imported))
    k3.metric('Unresolved terms', sum(1 for i in imported if i['In ontology'].startswith('NO')))

    st.markdown('#### Imported ontology entities and IRIs')
    imp_df = pd.DataFrame(imported)
    q = st.text_input('Filter entities', '')
    if q:
        mask = imp_df.apply(lambda r: q.lower() in ' '.join(map(str, r.values)).lower(), axis=1)
        imp_df = imp_df[mask]
    st.dataframe(imp_df, use_container_width=True, height=320)

    st.markdown('#### Graph view')
    v1, v2, v3 = st.columns([2, 1, 1])
    subject_options = ['(whole document — sample)'] + [r.compliance_name[:70] for r in rows]
    pick = v1.selectbox('Focus', subject_options)
    max_nodes = v2.slider('Max nodes', 10, 120, 45, 5)
    show_literals = v3.checkbox('Include literal values', value=False)
    if pick == '(whole document — sample)':
        triples = G.instance_triples(igraph, ontology, limit=max_nodes * 2)
    else:
        row = rows[subject_options.index(pick) - 1]
        uri = None
        for s, p, o in igraph:
            if str(p).endswith('complianceName') and str(o) == row.compliance_name:
                uri = s
                break
        triples = []
        if uri is not None:
            def lab(n):
                v = igraph.value(n, G.RDFS.label)
                if v:
                    return str(v)
                ent = ontology._index.get(str(n)) if ontology else None
                return ent.label if ent else str(n).rsplit('/', 1)[-1].rsplit('#', 1)[-1]
            for p, o in igraph.predicate_objects(uri):
                if isinstance(o, G.Literal) and not show_literals:
                    continue
                pred = ontology.curie(p) if ontology else str(p).rsplit('#', 1)[-1]
                triples.append((lab(uri), pred, str(o)[:60] if isinstance(o, G.Literal) else lab(o)))
                if not isinstance(o, G.Literal):
                    for p2, o2 in igraph.predicate_objects(o):
                        if isinstance(o2, G.Literal):
                            continue
                        triples.append((lab(o), ontology.curie(p2) if ontology else str(p2).rsplit('#', 1)[-1], lab(o2)))
    if triples:
        st.graphviz_chart(X.to_dot(triples, max_nodes=max_nodes), use_container_width=True)
        with st.expander('Triples behind this view'):
            st.dataframe(pd.DataFrame(triples, columns=['Subject', 'Predicate', 'Object']),
                         use_container_width=True, hide_index=True, height=260)
    else:
        st.info('No edges to display for this selection.')

# --------------------------------------------------------------------------- 5. entity explorer

with tabs[4]:
    st.subheader('Ontology entity explorer')
    if ontology is None:
        st.warning('Load an ontology file in the sidebar to use this section.')
    else:
        e1, e2, e3 = st.columns([2, 1, 1])
        query = e1.text_input('Search by label, CURIE or IRI', 'Compliance Requirement')
        kinds = e2.multiselect('Kind', ['Class', 'ObjectProperty', 'DatatypeProperty', 'NamedIndividual',
                                        'Datatype', 'AnnotationProperty'], default=['Class'])
        ns_choice = e3.selectbox('Namespace', ['(all)'] + ontology.namespaces())
        ns = '' if ns_choice == '(all)' else ns_choice.split('  (')[0]
        matches = ontology.entities(kinds=kinds, query=query, namespace=ns)[:400]
        st.caption(f'{len(matches)} matching entities')
        if matches:
            labels = [f'{m.label}   ·   {m.curie}' for m in matches]
            sel = st.selectbox('Entity', labels)
            ent = matches[labels.index(sel)]
            info = ontology.describe(G.URIRef(ent.iri))

            st.markdown(f"## {info['label']}")
            st.code(info['iri'], language='text')
            m1, m2, m3, m4 = st.columns(4)
            m1.metric('Kind', ent.kind)
            m2.metric('Module', ent.module or '—')
            m3.metric('Sub-classes', len(info['children']))
            m4.metric('Individuals', len(info['individuals']))
            if info['comment']:
                st.info(info['comment'])
            if info['notation'] or info['alt_labels']:
                st.caption('Notation: ' + (info['notation'] or '—') +
                           ' · Also known as: ' + (', '.join(info['alt_labels']) or '—'))
            for note in info['notes']:
                st.warning(note)

            col1, col2 = st.columns(2)
            with col1:
                st.markdown('**Parents**')
                st.write(pd.DataFrame(info['parents'], columns=['Label', 'CURIE']) if info['parents'] else '—')
                st.markdown('**Children**')
                st.write(pd.DataFrame(info['children'], columns=['Label', 'CURIE']).head(60)
                         if info['children'] else '—')
            with col2:
                st.markdown('**Axioms / restrictions**')
                st.write(pd.DataFrame(info['restrictions'], columns=['Restriction']).head(60)
                         if info['restrictions'] else '—')
                st.markdown('**Property of / used by**')
                st.write(pd.DataFrame(info['used_by'], columns=['Entity', 'CURIE', 'Predicate']).head(40)
                         if info['used_by'] else '—')
            if info['domain_of'] or info['range_of']:
                st.markdown('**Properties with this class as domain / range**')
                st.write(pd.DataFrame(
                    [('domain', a, b) for a, b in info['domain_of']] + [('range', a, b) for a, b in info['range_of']],
                    columns=['Position', 'Property', 'CURIE']))
            if info['individuals']:
                st.markdown('**Individuals of this class**')
                st.write(pd.DataFrame(info['individuals'], columns=['Label', 'CURIE']))

            st.markdown('#### Neighbourhood')
            hops = st.slider('Hops', 1, 2, 1)
            nb = ontology.neighbourhood(G.URIRef(ent.iri), hops=hops, limit=60)
            if nb:
                st.graphviz_chart(X.to_dot(nb, max_nodes=60, title=info['label']), use_container_width=True)
            used_here = [i for i in imported if i['IRI'] == ent.iri]
            if used_here:
                st.success(f"This entity is used {used_here[0]['Usages']} time(s) in the current document's graph "
                           f"({used_here[0]['Used as']}).")
                hits = [r for r in rows if ent.curie.split(':')[-1] in (r.actor_class, f'Periodicity_{r.periodicity}',
                                                                       f'Risk_{r.risk}')]
                if hits:
                    st.dataframe(pd.DataFrame([{'Provision': h.citation, 'Compliance': h.compliance_name,
                                                'Actor': h.actor, 'Risk': h.risk} for h in hits[:50]]),
                                 use_container_width=True, hide_index=True)
            with st.expander('All triples for this entity'):
                st.dataframe(pd.DataFrame(info['triples_out'], columns=['Predicate', 'Object']),
                             use_container_width=True, hide_index=True, height=240)

# --------------------------------------------------------------------------- 6. exports

with tabs[5]:
    st.subheader('Exports')
    stats = {'provisions': len(doc.provisions()),
             'counts': {'compliances': len(rows), 'high_risk': sum(1 for r in rows if r.risk == 'HIGH'),
                        'flagged': sum(1 for r in rows if r.review_flag), 'graph_triples': len(igraph),
                        'ontology_terms_used': len(imported)}}
    prov_summary = [{'Citation': p.citation, 'Level': p.level, 'Number': p.number, 'Heading': p.heading,
                     'Compliances': sum(1 for r in rows if r.provision_id == p.node_id),
                     'Text': (p.text or '')[:2000]} for p in doc.provisions()
                    if p.level in ('rule', 'section', 'clause', 'sub_rule', 'sub_section', 'paragraph')]
    base = Path(doc.source_file).stem[:40] or 'document'
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown('**Excel workbook**')
        st.caption('Compliances (29 repository headings + granular columns), document metadata, '
                   'ontology entities, provisions.')
        st.download_button('⬇️ Download .xlsx',
                           X.build_excel(doc, rows, imported, prov_summary, stats),
                           file_name=f'{base}_compliances.xlsx',
                           mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    with c2:
        st.markdown('**JSON bundle**')
        st.caption('Document metadata, full provision tree, every compliance with its granular fields, '
                   'and the ontology entities used.')
        st.download_button('⬇️ Download .json', X.build_json(doc, rows, imported, stats['counts']),
                           file_name=f'{base}_compliances.json', mime='application/json')
    with c3:
        st.markdown('**Knowledge graph**')
        fmt = st.selectbox('Format', ['turtle', 'xml', 'json-ld', 'nt'])
        ext = {'turtle': 'ttl', 'xml': 'rdf', 'json-ld': 'jsonld', 'nt': 'nt'}[fmt]
        st.download_button(f'⬇️ Download .{ext}', X.serialise_graph(igraph, fmt),
                           file_name=f'{base}_graph.{ext}', mime='text/plain')
    st.markdown('---')
    st.markdown('**Merged graph** — instance graph plus the ontology (single file for loading into a triple store)')
    if ontology is not None and st.checkbox('Build merged graph (large)'):
        merged = G.Graph()
        merged += ontology.g
        merged += igraph
        st.caption(f'{len(merged):,} triples')
        st.download_button('⬇️ Download merged .ttl', X.serialise_graph(merged, 'turtle'),
                           file_name=f'{base}_with_ontology.ttl', mime='text/turtle')
    st.markdown('---')
    st.markdown('**All uploaded documents** — batch extract and download one workbook per document')
    if len(uploaded) > 1 and st.button('Process all documents'):
        prog = st.progress(0.0)
        for i, f in enumerate(uploaded, 1):
            d2, r2, _ = cached_extract(f.name, f.getvalue(), include_permissions, min_conf)
            g2 = G.build_instance_graph(d2, r2, ontology)
            imp2 = G.imported_entities(g2, ontology)
            st.download_button(f'⬇️ {f.name} → Excel',
                               X.build_excel(d2, r2, imp2,
                                             [{'Citation': p.citation, 'Level': p.level, 'Number': p.number,
                                               'Heading': p.heading,
                                               'Compliances': sum(1 for r in r2 if r.provision_id == p.node_id),
                                               'Text': (p.text or '')[:2000]} for p in d2.provisions()],
                                             {'provisions': len(d2.provisions()),
                                              'counts': {'compliances': len(r2)}}),
                               file_name=f'{Path(f.name).stem[:40]}_compliances.xlsx',
                               key=f'batch{i}',
                               mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            prog.progress(i / len(uploaded))
