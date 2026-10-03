# STARAM Compliance Extractor

A Streamlit application that turns an uploaded **legislation** (Act, Rules, Regulations, Notification,
Circular) or **contract** into:

* a **compliance / obligation table** using the 29 compliance-repository headings,
* **granular legal detail** for every row (actor, action, object, trigger, deadline, manner, thresholds,
  inclusions/exclusions, cross-references, evidence, penalty, review flags),
* a **knowledge graph** typed with the STARAM ontology (`cmp:`, `leg:`, `folio:` IRIs), and
* downloads in **Excel, JSON and RDF** (Turtle / RDF-XML / JSON-LD / N-Triples).

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then upload a document in the sidebar. The ontology is read from
`ontology/STARAM_Core_Modules_with_Compliance.owl` by default; you can upload a different one or point
the sidebar path at another file (or set `STARAM_ONTOLOGY`).

Offline check of the pipeline, without the UI:

```bash
python smoke_test.py /path/to/document.pdf
```

## What the six tabs do

| Tab | Contents |
|---|---|
| **Document** | Detected metadata (instrument kind, parent Act, gazette number, commencement), provision-level counts, and a browser that shows any Rule/Section with the compliances extracted from it. |
| **Compliance table** | All rows: the 29 repository columns first, then the granular reasoning columns. Filter by risk, actor, nature or review flag, then open any row for its full detail and source text. |
| **Classification** | Distribution by risk, nature, periodicity, actor, authority and deontic type, plus the risk-engine trace showing which rule fired for each row. |
| **Knowledge graph** | Counts for the instance graph, the table of **imported ontology entities with their IRIs**, and a Graphviz view of the whole document or of one compliance and its neighbours. |
| **Entity explorer** | Search any entity by label, CURIE or IRI. Shows its IRI, kind, module, comment, notation, parents, children, OWL restrictions, properties whose domain/range it is, individuals, where it is used in the current document, a neighbourhood graph, and all its triples. |
| **Exports** | Excel, JSON, RDF in four formats, an optional merged graph (instance + ontology) and batch processing of every uploaded document. |

## How extraction works

1. **Ingest** (`core/ingest.py`) — PDF (pdfplumber, falling back to pypdf), DOCX, Akoma-Ntoso style JSON, TXT/MD.
   Bilingual Gazette PDFs are handled: Devanagari lines are dropped and parsing starts at the English notification.
   The parser builds a provision tree: Part → Rule/Section → sub-rule → clause → proviso/explanation, plus
   schedules and forms, with a citation path such as `Rule 4(2)(a)`.
2. **Extract** (`core/extract.py`) — each provision is split into sentences and tested for deontic force
   (shall / must / shall not / is required to). Machinery provisions (short title, commencement, definitions,
   interpretation) and duties of the Tribunal or Registry are skipped. For every obligation the engine captures
   actor, legal action, object, trigger, manner, forms, thresholds, inclusions, exclusions, cross-reference
   indicators, referenced sections and rules, evidence, penalty and NIL-return wording.
3. **Classify** (`core/extract.py`) — periodicity and On Date from the deadline wording
   (`within 30 days` → 30; `not less than 14 days before` → −14; `forthwith` → 0; `next working day` → 1;
   `seven clear days` → −8), periodicity nature (Static/Ongoing), compliance nature against the 12 master
   values, law category, statutory authority, and the **deterministic risk engine**
   (cancellation → HIGH; imprisonment → HIGH; max > 1,00,000 → HIGH; > 10,000 → MEDIUM; continuing → MEDIUM;
   else LOW). A per-day penalty is never counted as a one-time maximum.
4. **Map to the ontology** (`core/graph.py`) — every row becomes a `cmp:ComplianceRequirement` individual
   linked to its provision (`cmp:derivedFromProvision`), actor (`cmp:hasActor`), authority, form, event,
   penalty and the controlled-vocabulary individuals (`cmp:Periodicity_E`, `cmp:Risk_HIGH`,
   `cmp:Nature_Intimation`, …). Provisions are typed with `leg:Rule`, `leg:SubRule`, `leg:Paragraph` and so on.
   The app reports any term that does not resolve in the loaded ontology.

## Optional Claude refinement

Tick **Refine with Claude** in the sidebar and supply an `ANTHROPIC_API_KEY`. The model may rewrite the
name, description, applicability, actor, event and authority and may add review notes; it cannot change the
penalty fields, and the risk is always recomputed locally so the classification stays deterministic.

## Review flags

Rows are flagged `NEEDS_MASTER_REVIEW` when the compliance nature cannot be mapped to the master list, no
statutory deadline is stated, the actor cannot be identified, or no penalty provision is found in the supplied
document. These flags are the queue for a compliance analyst — the app never invents a provision, deadline,
form, penalty or authority that is not in the text.

## Layout

```
app.py                 Streamlit UI (6 tabs)
core/ingest.py         file readers + provision-tree parser
core/extract.py        obligation extraction, classification, risk engine, de-duplication
core/graph.py          ontology index, instance-graph builder, entity description & neighbourhood
core/exporters.py      Excel / JSON / RDF / Graphviz DOT
core/llm.py            optional Claude enrichment
ontology/              STARAM_Core_Modules_with_Compliance.owl
smoke_test.py          offline end-to-end run
```
