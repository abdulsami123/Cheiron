# cheiron2 — ClinicalTrials.gov visualizer

Async FastAPI service that fetches data from the public ClinicalTrials.gov v2 API
and asks an LLM (OpenAI Structured Outputs) to choose a visualization spec for
the user's question. The response is a typed JSON document containing the chosen
visualization plus server-injected metadata.

## Requirements

- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv)
- An OpenAI-compatible API key (OpenAI / Azure OpenAI / vLLM / OpenRouter, ...)

## Setup

```bash
uv sync
```

## Environment variables

All variables can be set in the shell or in a `.env` file at the project root.

| Variable | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | _(empty)_ | Required to call the LLM. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Override for Azure / vLLM / OpenRouter. |
| `OPENAI_MODEL` | `gpt-4.1` | Any model that supports Structured Outputs. |
| `OPENAI_TIMEOUT_S` | `60` | LLM client timeout (seconds). |
| `CTGOV_BASE_URL` | `https://clinicaltrials.gov/api/v2` | |
| `CTGOV_PAGE_SIZE` | `50` | Page size for searches. |
| `CTGOV_CONNECT_TIMEOUT_S` | `5` | |
| `CTGOV_READ_TIMEOUT_S` | `20` | |
| `CTGOV_WRITE_TIMEOUT_S` | `5` | |
| `CTGOV_POOL_TIMEOUT_S` | `5` | |
| `LLM_MAX_STUDIES` | `50` | Cap on studies sent to the LLM. |
| `LLM_MAX_CHARS` | `120000` | Hard cap on serialized payload size sent to the LLM. |
| `LOG_LEVEL` | `INFO` | |

## Run

```bash
uv run uvicorn app.main:app --reload
```

OpenAPI docs: <http://127.0.0.1:8000/docs>

## Endpoint

`GET /trials/visualize`

Query parameters:

| Name | Required | Notes |
|---|---|---|
| `query` | yes | Free-text user question. |
| `conditions` | no | Disease/condition filter. |
| `location` | no | Geographic filter. |
| `title` | no | Title keyword. |
| `intervention` | no | Intervention name. |
| `outcome_measure` | no | Outcome measure keyword. |
| `sponsor` | no | Sponsor name. |
| `lead` | no | Lead investigator/org. |
| `study_id` | no | Org study ID. |
| `nct_id` | no | NCT identifier (`^NCT\d{8}$`). When present, fetches the single study. |

## Example

```bash
curl -G "http://127.0.0.1:8000/trials/visualize" \
  --data-urlencode "query=How has enrollment in pediatric leukemia trials changed over time?" \
  --data-urlencode "conditions=leukemia" \
  --data-urlencode "intervention=chemotherapy"
```

Response shape (truncated):

```json
{
  "visualization": {
    "type": "time_series",
    "title": "Enrollment in pediatric leukemia trials by start year",
    "encoding": { "x": "start_year", "y": "enrollment", "tooltip": ["nct_id", "brief_title"] },
    "data": [ { "start_year": 2018, "enrollment": 120, "nct_id": "NCT01234567" } ]
  },
  "metadata": {
    "units": { "enrollment": "participants" },
    "sort": { "start_year": "asc" },
    "total_studies": 412,
    "nct_ids": ["NCT01234567", "..."],
    "notes": "...\nprompt_version=viz-planner.v1",
    "source": "clinicaltrials.gov",
    "generated_at": "2026-05-09T12:34:56Z"
  }
}
```

## Response schema

Top-level `TrialsVisualizationResponse`:

| Field | Type | Notes |
|---|---|---|
| `visualization` | `Visualization` | The chart spec the LLM picked. |
| `metadata` | `Metadata` | Server-injected context + LLM annotations. |

### `Visualization`

| Field | Type | Notes |
|---|---|---|
| `type` | `enum` | One of: `bar_chart`, `scatter_plot`, `time_series`, `histogram`, `network_graph`. |
| `title` | `string` | Human-readable chart title. |
| `encoding` | `Encoding` | Maps row fields to chart channels. |
| `data` | `list[object]` | Rows the chart is built from. Per-row shape varies by `type`; see "Data shape per `type`" below. |

### `Encoding`

Channels not used by the chosen `type` are `null`. `tooltip` is always a list (possibly empty).

| Field | Type | Notes |
|---|---|---|
| `x` | `string \| null` | Row field for the x-axis (or edge source for `network_graph`). |
| `y` | `string \| null` | Row field for the y-axis (or edge target for `network_graph`). |
| `color` | `string \| null` | Row field driving color encoding. |
| `size` | `string \| null` | Row field driving marker size (used by `scatter_plot`). |
| `group` | `string \| null` | Row field for grouping series. |
| `tooltip` | `list[string]` | Row fields to surface on hover. |

### Data shape per `type`

- `bar_chart`, `scatter_plot`, `time_series`, `histogram` — rows are arbitrary objects; the keys named in `encoding.x` / `encoding.y` / `encoding.color` / `encoding.size` / `encoding.tooltip` must exist on each row.
- `network_graph` — every row is an edge. Rows MUST have:
  - `source`: `string` — endpoint A
  - `target`: `string` — endpoint B
  - `weight`: `number` *(optional)* — edge weight
  
  `encoding.x` is set to `"source"` and `encoding.y` to `"target"`.

### `Metadata`

| Field | Type | Notes |
|---|---|---|
| `units` | `object<string, string>` | Map from numeric row-field name to its unit string. May be `{}`. |
| `sort` | `object<string, "asc" \| "desc"> \| null` | Single-key map indicating sort order on a row field. |
| `total_studies` | `integer \| null` | Upstream `totalCount` for the search (or `1` for single-study lookups). Note: this is the upstream filter total, not the count of rows in `visualization.data`. |
| `nct_ids` | `list[string]` | NCT IDs from the upstream payload, server-injected (independent of what the LLM emitted). |
| `notes` | `string \| null` | One-paragraph LLM-written explanation, suffixed with `prompt_version=...; essie_prompt_version=...; essie_translation: '...'` for traceability. |
| `source` | `"clinicaltrials.gov"` | Pinned literal. |
| `generated_at` | `string (ISO-8601 datetime, UTC)` | Server-side timestamp. |

## Error model

All errors return a JSON document of the form:

```json
{ "error": { "code": "<code>", "message": "<msg>", "details": { ... }, "retry_after": 2 } }
```

Status mapping:

- `404` — `nct_id` not found upstream.
- `400` — other 4xx from ClinicalTrials.gov, surfaced via `upstream_bad_request`.
- `502` — 5xx, timeouts, malformed JSON, or LLM planning failure.

## Project layout

```
app/
  main.py              FastAPI app + lifespan
  config.py            pydantic-settings
  deps.py              shared httpx + AsyncOpenAI deps
  errors.py            AppError + handlers
  logging_config.py
  routers/trials.py    GET /trials/visualize
  services/
    clinicaltrials.py  httpx wrapper + param mapping
    llm.py             structured-outputs planner + repair fallback
    trimming.py        payload trimming
  schemas/
    request.py         query inputs
    ctgov.py           v2 surface we use
    visualization.py   LLM-facing + public response models + mapper
```
### To run the frontend : 

uv run python -m frontend.app

## Key Decisions : 
The thought process involved into building this first required a thorough inspection of the endpoints and how they could be leveraged. I realised after a while that simple Natural language was not working and first needed to be translated into Essie Syntax Expression. Right now the primary interface is through the /Studies endpoint since that covers the majority of cases for a chat agent and inside it we have utilized the query parameters mainly since that was the main task. The filter parameters inside the application can also be leveraged but for now I felt to get to v1 this was a good checkpoint.
Another decision I made was deciding to go for a simpler chained workflow rather than a complex graph based flow.
For the implementation part Claude and Claude code were leveraged. I gathered the requirements, came up with my solution and to optimize the prompt for implementation worked with claude to optimize it. I had a preference for my stack : 
 fastapi: Chose it for the simplicity it offers and its Async first nature 
 httpx: A fast Async library that has a syntax very similar to the requests library so its just convenient
  uv: I can't begin to thank the guys at astral for making uv
  openAI: The main edge here was the strucuted output that openAI gives and the gpt-4.1 supports with 1M context window.
  So after the stack was deciding the schemas and then the system prompts for the LLM calls for Essie syntax to Natural language conversion and visualization decisioning. Once that was done I handed off the implementation to Claude.

## Improvements 

Given more time I have a few ideas to make this even more robust : 
1. Having an LLM as a judge to evaluate outputs
2. Adding tracebility so that its easier to identify which part of the service would be failing
I also was wary of what the constraints were so I have made certain assumptions and went with them like the 120k LLM limit since larger data injected into the prompted will start costing alot per query.


## Demo link

<https://youtu.be/UhpkIlKLQ84>

## Example runs

<Example 1>

```json 
1. query : how has the number of trials for Pembrolizumab changed since 2015
{
  "visualization": {
    "type": "time_series",
    "title": "Number of Pembrolizumab Trials Started Each Year Since 2015",
    "encoding": {
      "x": "year",
      "y": "trial_count",
      "color": null,
      "size": null,
      "group": null,
      "tooltip": [
        "year",
        "trial_count"
      ]
    },
    "data": [
      {
        "year": 2016,
        "trial_count": 1
      },
      {
        "year": 2017,
        "trial_count": 3
      },
      {
        "year": 2018,
        "trial_count": 2
      },
      {
        "year": 2019,
        "trial_count": 4
      },
      {
        "year": 2020,
        "trial_count": 1
      },
      {
        "year": 2021,
        "trial_count": 3
      },
      {
        "year": 2022,
        "trial_count": 4
      },
      {
        "year": 2023,
        "trial_count": 3
      },
      {
        "year": 2025,
        "trial_count": 3
      }
    ]
  },
  "metadata": {
    "units": {},
    "sort": {
      "year": "asc"
    },
    "total_studies": 2982,
    "nct_ids": [
      "NCT03867175",
      "NCT03321630",
      "NCT02939651",
      "NCT05092373",
      "NCT05980702",
      "NCT05527795",
      "NCT03322267",
      "NCT04700072",
      "NCT05824975",
      "NCT06195254",
      "NCT05284539",
      "NCT04641728",
      "NCT07283822",
      "NCT05431270",
      "NCT03720431",
      "NCT04645602",
      "NCT03260894",
      "NCT03805594",
      "NCT05942872",
      "NCT02811861",
      "NCT05282901",
      "NCT03922204",
      "NCT03386357",
      "NCT07419932",
      "NCT02824965",
      "NCT06624644",
      "NCT03197467",
      "NCT03224871",
      "NCT05585034",
      "NCT04520711",
      "NCT06890598",
      "NCT05482893",
      "NCT03238638",
      "NCT03430700",
      "NCT06894771",
      "NCT03757858",
      "NCT07365319",
      "NCT02903914",
      "NCT03932409",
      "NCT05232409",
      "NCT03921021",
      "NCT04432857",
      "NCT03240211",
      "NCT04227509",
      "NCT03412877",
      "NCT05241899",
      "NCT05096663",
      "NCT04318730",
      "NCT06371807",
      "NCT07455032"
    ],
    "notes": "This time series plots the count of clinical trials involving Pembrolizumab that started in each year since 2015. It provides a clear visualization of trends in research activity for this drug over time, helping to answer how the number of such trials has changed annually.\nprompt_version=viz-planner.v1; essie_prompt_version=essie-translator.v1; essie_translation: 'pembrolizumab' (rationale: Kept 'pembrolizumab' as the key filter. Dropped dimension/aspect words about counting, change, and time (e.g., 'number', 'changed since 2015'); those describe what to measure, not what to filter on.)",
    "source": "clinicaltrials.gov",
    "generated_at": "2026-05-10T07:24:04.696689Z"
  }
}
```
</Example 1>
2. how are cancer trials distributed across phases

{
  "visualization": {
    "type": "bar_chart",
    "title": "Distribution of Cancer Trials Across Phases",
    "encoding": {
      "x": "phase",
      "y": "count",
      "color": null,
      "size": null,
      "group": null,
      "tooltip": [
        "phase",
        "count"
      ]
    },
    "data": [
      {
        "phase": "EARLY_PHASE1",
        "count": 1
      },
      {
        "phase": "PHASE1",
        "count": 6
      },
      {
        "phase": "PHASE1/PHASE2",
        "count": 3
      },
      {
        "phase": "PHASE2",
        "count": 8
      },
      {
        "phase": "PHASE2/PHASE3",
        "count": 1
      },
      {
        "phase": "PHASE3",
        "count": 6
      },
      {
        "phase": "PHASE4",
        "count": 1
      },
      {
        "phase": "NA",
        "count": 7
      },
      {
        "phase": "Not Applicable/None",
        "count": 10
      }
    ]
  },
  "metadata": {
    "units": {},
    "sort": {
      "count": "desc"
    },
    "total_studies": 140194,
    "nct_ids": [
      "NCT05796973",
      "NCT00940069",
      "NCT03178383",
      "NCT06445283",
      "NCT06089083",
      "NCT04507841",
      "NCT02531841",
      "NCT04156841",
      "NCT01649505",
      "NCT06229405",
      "NCT02689505",
      "NCT05593094",
      "NCT06188494",
      "NCT05171166",
      "NCT04258566",
      "NCT04950166",
      "NCT02120456",
      "NCT05575622",
      "NCT05537051",
      "NCT04586751",
      "NCT00290251",
      "NCT03724929",
      "NCT03677531",
      "NCT03007602",
      "NCT01905202",
      "NCT00520702",
      "NCT00108953",
      "NCT01620853",
      "NCT05683353",
      "NCT06688253",
      "NCT04675645",
      "NCT05134337",
      "NCT07198945",
      "NCT00775645",
      "NCT01193595",
      "NCT03893539",
      "NCT03039439",
      "NCT02561039",
      "NCT06023875",
      "NCT03867175",
      "NCT00437957",
      "NCT00716157",
      "NCT05245357",
      "NCT05550701",
      "NCT01096901",
      "NCT05092412",
      "NCT02434081",
      "NCT00099281",
      "NCT03353181",
      "NCT01273181"
    ],
    "notes": "This bar chart visualizes the number of cancer clinical trials per trial phase using data from registration records. It provides a clear breakdown, allowing you to quickly see which phases have the most and the least number of trials, highlighting the focus areas for ongoing cancer research.\nprompt_version=viz-planner.v1; essie_prompt_version=essie-translator.v1; essie_translation: 'cancer' (rationale: Kept only the disease filter 'cancer', dropped non-filter words like 'how', 'distributed', and 'phases' which describe visualization and trial phases (a dimension, not a filter).)",
    "source": "clinicaltrials.gov",
    "generated_at": "2026-05-10T07:33:20.109778Z"
  }
}

3.show a network of sponsors to drugs for cancer trials

{
  "visualization": {
    "type": "network_graph",
    "title": "Network of Sponsors to Drugs in Cancer Trials",
    "encoding": {
      "x": "source",
      "y": "target",
      "color": null,
      "size": null,
      "group": null,
      "tooltip": []
    },
    "data": [
      {
        "source": "Tampere University Hospital",
        "target": "Atorvastatin"
      },
      {
        "source": "Hunan Province Tumor Hospital",
        "target": "pemetrexed"
      },
      {
        "source": "Tongji Hospital",
        "target": "Niraparib"
      },
      {
        "source": "University Hospital Freiburg",
        "target": "Fortecortin®-ETOPOPHOS®-IFO-cell®-CARBO-cell®"
      },
      {
        "source": "University Hospital Freiburg",
        "target": "TEPADINA®-CARMUBRIS®-Busilvex®"
      },
      {
        "source": "OHSU Knight Cancer Institute",
        "target": "fibrin sealant (Beriplast P, TISSEEL VH)"
      },
      {
        "source": "Boehringer Ingelheim",
        "target": "BI 836880"
      },
      {
        "source": "Hoffmann-La Roche",
        "target": "ZN-A-1041"
      },
      {
        "source": "Hoffmann-La Roche",
        "target": "ZN-A-1041 + T-DM1 3.6 mg/kg iv. for Phase 1b"
      },
      {
        "source": "Hoffmann-La Roche",
        "target": "ZN-A-1041 + T-Dxd 5.4 mg/kg iv. for Phase 1b"
      },
      {
        "source": "Hoffmann-La Roche",
        "target": "ZN-A-1041 + PHESGO / Herceptin plus Perjeta injection for Phase 1b"
      },
      {
        "source": "Hoffmann-La Roche",
        "target": "ZN-A-1041 + T-DM1 3.6 mg/kg iv. for Phase 1c"
      },
      {
        "source": "Hoffmann-La Roche",
        "target": "ZN-A-1041 + T-Dxd 5.4 mg/kg iv. for Phase 1c"
      },
      {
        "source": "Hoffmann-La Roche",
        "target": "ZN-A-1041 + PHESGO / Herceptin plus Perjeta injection for Phase 1c"
      },
      {
        "source": "AstraZeneca",
        "target": "No drug intervention documented"
      },
      {
        "source": "OncoNano Medicine, Inc.",
        "target": "pegsitacianine"
      },
      {
        "source": "LEO Pharma",
        "target": "LEO 43204"
      },
      {
        "source": "LEO Pharma",
        "target": "Placebo"
      },
      {
        "source": "Biotheus Inc.",
        "target": "PM1021, PM8001"
      },
      {
        "source": "Effexus Pharmaceutical",
        "target": "Secretrol"
      },
      {
        "source": "Bayer",
        "target": "Sorafenib (Nexavar, BAY43-9006) plus Doxorubicin"
      },
      {
        "source": "Bayer",
        "target": "Doxorubicin/Placebo"
      },
      {
        "source": "Sanofi",
        "target": "Ombrabulin (AVE8062)"
      },
      {
        "source": "Sanofi",
        "target": "bevacizumab"
      },
      {
        "source": "Loxo Oncology, Inc.",
        "target": "LOXO-305"
      },
      {
        "source": "Loxo Oncology, Inc.",
        "target": "Itraconazole"
      },
      {
        "source": "Loxo Oncology, Inc.",
        "target": "Rifampin"
      },
      {
        "source": "Xuekui Liu",
        "target": "Cadonilimab"
      },
      {
        "source": "Xuekui Liu",
        "target": "Docetaxel"
      },
      {
        "source": "Xuekui Liu",
        "target": "Cisplatin"
      },
      {
        "source": "YM BioSciences",
        "target": "YMB 1002"
      },
      {
        "source": "National Cancer Institute (NCI)",
        "target": "Aldesleukin"
      },
      {
        "source": "National Cancer Institute (NCI)",
        "target": "Cyclophosphamide"
      },
      {
        "source": "National Cancer Institute (NCI)",
        "target": "Fludarabine"
      },
      {
        "source": "National Institutes of Health Clinical Center (CC)",
        "target": "Indocyanine Green (ICG)"
      },
      {
        "source": "SWOG Cancer Research Network",
        "target": "acetyl-L-carnitine hydrochloride"
      }
    ]
  },
  "metadata": {
    "units": {},
    "sort": null,
    "total_studies": 140194,
    "nct_ids": [
      "NCT05796973",
      "NCT00940069",
      "NCT03178383",
      "NCT06445283",
      "NCT06089083",
      "NCT04507841",
      "NCT02531841",
      "NCT04156841",
      "NCT01649505",
      "NCT06229405",
      "NCT02689505",
      "NCT05593094",
      "NCT06188494",
      "NCT05171166",
      "NCT04258566",
      "NCT04950166",
      "NCT02120456",
      "NCT05575622",
      "NCT05537051",
      "NCT04586751",
      "NCT00290251",
      "NCT03724929",
      "NCT03677531",
      "NCT03007602",
      "NCT01905202",
      "NCT00520702",
      "NCT00108953",
      "NCT01620853",
      "NCT05683353",
      "NCT06688253",
      "NCT04675645",
      "NCT05134337",
      "NCT07198945",
      "NCT00775645",
      "NCT01193595",
      "NCT03893539",
      "NCT03039439",
      "NCT02561039",
      "NCT06023875",
      "NCT03867175",
      "NCT00437957",
      "NCT00716157",
      "NCT05245357",
      "NCT05550701",
      "NCT01096901",
      "NCT05092412",
      "NCT02434081",
      "NCT00099281",
      "NCT03353181",
      "NCT01273181"
    ],
    "notes": "This network graph shows connections between lead sponsors and drug interventions in cancer-related clinical trials from the provided data. Each edge represents a sponsor (source) supporting a trial with a specific drug (target), mapping direct relationships and helping users understand the landscape of sponsor-drug involvement in cancer trials.\nprompt_version=viz-planner.v1; essie_prompt_version=essie-translator.v1; essie_translation: 'cancer' (rationale: Kept only 'cancer' as the disease filter; excluded dimension/visualization concepts (network, sponsors, drugs) because these are not clinical trial search criteria.)",
    "source": "clinicaltrials.gov",
    "generated_at": "2026-05-10T07:34:05.311027Z"
  }
}