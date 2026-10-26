"""
pages/analysis.py
Dedicated progress + results page.
Reads st.session_state.analysis_payload set by app.py.
"""
import os
import streamlit as st
from fields_config import ALL_FIELDS, SOURCE_FIELDS, VISUAL_FIELDS
from data_extractor import extract_batch
from comparator import compare_all
from excel_writer import write_excel, make_filename

def _attempts_md(attempts):
    """Numbered list of every fetch step tried for one URL."""
    lines = []
    for i, a in enumerate(attempts or [], 1):
        mark = "✅" if a.get("ok") else "❌"
        note = a.get("note") or ""
        note = f" — {note[:120]}" if note and not a.get("ok") else ""
        credits = f" · {a['credits']} credits" if a.get("credits") else ""
        secs = f" · {a['seconds']}s" if a.get("seconds") else ""
        memo = " · 🧠 remembered: this site needs rendering" if a.get("remembered") else ""
        lines.append(f"{i}. {mark} {a.get('step', '?')}{note}{memo}{credits}{secs}")
    return "\n".join(lines)


def _fetch_label(fetch):
    """'ScraperAPI premium' / 'none (every step failed)' / '' for one row."""
    if fetch.get("method"):
        return fetch["method"]
    return "none (every step failed)" if fetch.get("attempts") else ""


st.set_page_config(
    page_title="Analysis in Progress — Business Listing Checker",
    page_icon="🔍",
    layout="wide",
)

st.markdown("""
<style>
    .block-container { padding-top: 2rem; }
    .section-header {
        font-size: 1.1rem; font-weight: 700;
        margin-bottom: 1rem; padding-bottom: 6px;
        border-bottom: 2px solid #4472C4;
    }
    .metric-card {
        background: #f8f9fa; border-radius: 8px;
        padding: 1rem; text-align: center;
    }
</style>
""", unsafe_allow_html=True)

# ── Back button ───────────────────────────────────────────────────────────────
if st.button("← Back to Form"):
    st.switch_page("app.py")

st.title("🔍 Analysis Progress")

# ── Guard: no payload means user landed here directly ─────────────────────────
payload = st.session_state.get("analysis_payload")
results_ready = st.session_state.get("results")

if not payload and not results_ready:
    st.warning("No analysis job found. Please go back and click **Start Analysis**.")
    st.stop()

# ── If results already exist (e.g. user navigated back then returned) ─────────
if results_ready and not payload:
    st.success("✅ Previous analysis results are shown below.")
    # jump straight to results section (handled at bottom)
else:
    # ── Run the analysis ──────────────────────────────────────────────────────
    user_data      = payload["user_data"]
    known_urls     = payload["known_urls"]
    url_source_map = payload["url_source_map"]

    total = len(known_urls)

    # Apply the ScraperAPI key pasted in the UI (if any) for this run
    _key = payload.get("scraperapi_key") or st.session_state.get("scraperapi_key", "")
    if _key:
        os.environ["SCRAPERAPI_KEY"] = _key

    status_box = st.empty()
    progress   = st.progress(0)
    log_box    = st.empty()
    log_lines: list = []

    def log(msg: str):
        log_lines.append(msg)
        log_box.markdown("\n".join(f"- {l}" for l in log_lines[-10:]))

    try:
        # ── Stage 1: Extract ──────────────────────────────────────────────
        status_box.info(f"🌐 **Stage 1/2** — Extracting data from {total} page(s)…")
        log(f"Starting extraction of {total} URLs…")

        def on_progress(done, total_count):
            pct = (done / total_count) * 0.80
            progress.progress(min(pct, 0.80))
            log(f"Extracted {done}/{total_count} pages…")

        all_extracted = extract_batch(
            known_urls, batch_size=4, progress_callback=on_progress,
        )

        errors    = sum(1 for ex in all_extracted if ex.get("_scrape_error"))
        extract_ok = total - errors
        credits_total = sum((ex.get("_fetch") or {}).get("credits", 0) for ex in all_extracted)
        log(f"Extraction done — {extract_ok} OK, {errors} failed. "
            f"ScraperAPI credits used: {credits_total}.")
        progress.progress(0.80)

        with st.expander("🔍 Extraction debug info"):
            for ex in all_extracted:
                url        = ex.get("_url", "")
                src        = url_source_map.get(url, "unknown")
                error      = ex.get("_scrape_error")
                icon       = "✅" if not error else "❌"
                st.markdown(f"**{icon} {url}** [{src}]")
                fetch = ex.get("_fetch") or {}
                if fetch.get("attempts") or fetch.get("method"):
                    st.caption(
                        f"🛰️ Fetched via **{_fetch_label(fetch)}** · "
                        f"💳 **{fetch.get('credits', 0)}** ScraperAPI credit(s)"
                    )
                    st.markdown(_attempts_md(fetch.get("attempts")))
                if error:
                    st.error(error)
                else:
                    st.json({k: v for k, v in ex.items() if not k.startswith("_")})

        # ── Stage 2: Compare ───────────────────────────────────────────────
        status_box.info("📊 **Stage 2/2** — Comparing data…")
        log("Running comparison…")
        results = compare_all(
            user_data,
            all_extracted,
            url_source_map,
            SOURCE_FIELDS,
        )
        log("Comparison done.")
        progress.progress(1.0)

        # Persist results and clear payload so re-visits don't re-run
        st.session_state.results          = results
        st.session_state.extracted_data   = all_extracted
        st.session_state.user_data        = user_data
        st.session_state.analysis_payload = None  # consumed

        status_box.success(f"✅ Analysis complete — {total} URL(s) checked.")

    except Exception as e:
        status_box.error(f"Error: {e}")
        st.exception(e)
        st.stop()

# ── Results section ───────────────────────────────────────────────────────────
results        = st.session_state.get("results", [])
extracted_data = st.session_state.get("extracted_data", [])
user_data      = st.session_state.get("user_data", {})

if results:
    st.markdown("---")
    st.markdown('<div class="section-header">📊 Results</div>', unsafe_allow_html=True)

    total     = len(results)
    correct   = sum(1 for r in results if r.get("Status") == "CORRECT")
    incorrect = total - correct

    credits_used = sum((ex.get("_fetch") or {}).get("credits", 0) for ex in extracted_data)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total URLs", total)
    c2.metric("✅ Correct", correct)
    c3.metric("❌ Issues Found", incorrect)
    c4.metric("💳 ScraperAPI credits", credits_used)

    if extracted_data:
        with st.expander("🛰️ How each page was fetched"):
            st.dataframe(
                [
                    {
                        "URL": ex.get("_url", ""),
                        "Fetched via": _fetch_label(ex.get("_fetch") or {}),
                        "Credits": (ex.get("_fetch") or {}).get("credits", 0),
                        "Result": "❌ failed" if ex.get("_scrape_error") else "✅ extracted",
                    }
                    for ex in extracted_data
                ],
                use_container_width=True,
                hide_index=True,
            )

    excel_bytes = write_excel(results, extracted_data, user_data)
    st.download_button(
        label="📥 Download Excel Report",
        data=excel_bytes,
        file_name=make_filename(user_data.get("Name", "")),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        use_container_width=True,
    )

    st.markdown("---")
    if st.button("🔄 Run Another Analysis"):
        st.session_state.results          = None
        st.session_state.extracted_data    = None
        st.session_state.analysis_payload = None
        st.switch_page("app.py")