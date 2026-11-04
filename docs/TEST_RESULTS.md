# Test results (actual output)

Python 3.12, pinned `requirements.txt`. Run with `python -m pytest -q`.

## Working copy, full dataset present
```
131 passed, 1 skipped in 27.52s
```
Skipped: `tests/test_ai.py::test_real_llm_end_to_end` (needs `OPENAI_API_KEY`).

## Fresh clone, clean virtualenv, **no raw data** (app runs on the bundled demo sample)
```
127 passed, 5 skipped in 33.07s
SKIPPED tests/test_ai.py:227                      OPENAI_API_KEY not set
SKIPPED tests/test_metrics.py:148                 full dataset not present in data/raw
SKIPPED tests/test_metrics.py:171                 full dataset not present in data/raw
SKIPPED tests/test_schema_and_pipeline.py:93      full dataset not present in data/raw
SKIPPED tests/test_security.py:80                 full dataset not present in data/raw
```

## What still requires credentials / external access
- Live OpenAI behaviour (planner quality, explanation quality): only a scripted stub was exercised.
- GitHub publication and cloud deployment.
- Docker image build (Dockerfile written, not built here).
