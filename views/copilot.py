"""AI Business Copilot chat page."""
import streamlit as st

from src.ai.client import get_client_or_none
from src.ai.copilot import EXAMPLE_QUESTIONS, Copilot, CopilotResponse
from src.ui import charts as ch
from src.ui.components import dataset_banner, page_setup

con, source = page_setup("AI Copilot", "🤖")


@st.cache_resource
def get_copilot(_con):
    return Copilot(_con, get_client_or_none())


copilot = get_copilot(con)
st.title("AI Business Copilot")
dataset_banner(source)
if copilot.mode == "llm":
    st.success("AI mode: questions are interpreted by the model, then answered by validated, read-only queries or trusted metric functions.", icon="✅")
else:
    st.warning("Offline mode: no `OPENAI_API_KEY` is set, so a built-in keyword router maps questions to the same trusted analyses. "
               "Free-form questions need an API key (see README).", icon="🔌")

st.session_state.setdefault("messages", [])
st.session_state.setdefault("history", [])


def render(resp: CopilotResponse, key: str):
    if resp.status in ("error", "unsupported", "clarify"):
        (st.error if resp.status == "error" else st.info)(resp.answer)
        for n in resp.notes:
            st.caption(n)
        return
    st.markdown(f"**{resp.answer}**" if resp.method_kind == "analysis" and not resp.notes else resp.answer)
    for kp in resp.key_points:
        st.markdown(f"- {kp}")
    fig = ch.make_chart(resp.df, resp.chart_spec, resp.title) if resp.df is not None else None
    if fig is not None:
        st.plotly_chart(fig, width="stretch", key=f"fig_{key}")
    if resp.sections:
        for name, k in (("Possible explanations (hypotheses)", "explanations"), ("Recommendations", "recommendations"), ("Limitations", "limitations")):
            with st.expander(name, expanded=(k == "recommendations")):
                for x in resp.sections[k]:
                    st.markdown(f"- {x}")
    if resp.df is not None and not resp.df.empty:
        with st.expander(f"Data ({len(resp.df):,} rows)"):
            st.dataframe(resp.df, width="stretch", hide_index=True)
            st.download_button("Download CSV", resp.df.to_csv(index=False), file_name="copilot_result.csv", mime="text/csv", key=f"dl_{key}")
    with st.expander("How this was calculated"):
        st.caption(f"Method: {'trusted metric function' if resp.method_kind == 'analysis' else 'generated SQL (validated, read-only)'} `{resp.method_id}`")
        st.code(resp.method_text or "n/a", language="sql" if resp.method_kind == "sql" or "SELECT" in (resp.method_text or "") else "text")
        for a in resp.assumptions:
            st.markdown(f"- Assumption: {a}")
    if resp.caveats:
        with st.expander("Caveats and limitations"):
            for c in resp.caveats:
                st.markdown(f"- {c}")
    if resp.dq_warnings:
        with st.expander(f"⚠️ Data-quality issues that could change this result ({len(resp.dq_warnings)})", expanded=True):
            for w in resp.dq_warnings:
                st.markdown(f"- {w}")
    for w in resp.verification_warnings:
        st.warning(w)
    for n in resp.notes:
        st.caption(n)
    st.caption(f"{resp.elapsed_ms} ms")


with st.sidebar:
    st.subheader("Try asking")
    for i, q in enumerate(EXAMPLE_QUESTIONS):
        if st.button(q, key=f"ex{i}", width="stretch"):
            st.session_state["pending"] = q
    if st.button("Clear conversation", width="stretch"):
        st.session_state["messages"], st.session_state["history"] = [], []
        st.rerun()

for i, m in enumerate(st.session_state["messages"]):
    with st.chat_message(m["role"]):
        if m["role"] == "user":
            st.write(m["content"])
        else:
            render(m["resp"], str(i))

typed = st.chat_input("Ask about MRR, churn, plans, cohorts, support, usage...", max_chars=600)
question = typed or st.session_state.pop("pending", None)
if question:
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        with st.spinner("Analysing..."):
            resp = copilot.ask(question, st.session_state["history"])
        render(resp, str(len(st.session_state["messages"])))
    st.session_state["messages"].append({"role": "assistant", "resp": resp})
    if resp.status == "ok":
        st.session_state["history"].append(resp.history_entry(question))
        st.session_state["history"] = st.session_state["history"][-6:]
