"""Generate docs/METRICS.md from the metric registry so documentation cannot drift from the code."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.metrics.definitions import METRICS, STATUS_ORDER  # noqa: E402

out = ["# Metric definitions", "", "_Generated from `src/metrics/definitions.py` by `python scripts/gen_docs.py`. Do not edit by hand._", "",
       "Status key: **available** direct; **caveated** computed but a known data issue changes interpretation; **approximate** proxy; **unavailable** not computed.", "",
       "| Metric | Status |", "|---|---|"]
out += [f"| {m.name} | {m.status} |" for s in STATUS_ORDER for m in METRICS.values() if m.status == s]
for s in STATUS_ORDER:
    for m in (m for m in METRICS.values() if m.status == s):
        out += ["", f"## {m.name}", f"`{m.id}` - status: **{m.status}**", "", f"- **Formula:** {m.formula}", f"- **Date basis:** {m.date_basis}", f"- **Grain:** {m.grain}",
                f"- **Filters:** {m.filters}", f"- **Assumptions:** {m.assumptions}", f"- **Limitations:** {m.limitations}",
                f"- **Related data-quality issues:** {', '.join(m.related_dq) or 'none'}"]
(ROOT / "docs" / "METRICS.md").write_text("\n".join(out) + "\n")
print("wrote docs/METRICS.md")
