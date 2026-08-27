"""Streamlit front end for the corrected Kimi K3 nanoMINER pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import streamlit as st
from openai import OpenAI

from nanominer_k3.config import ConfigurationError, KimiSettings
from nanominer_k3.documents import DocumentCorpus, PdfDocument
from nanominer_k3.pipeline import run_extraction
from nanominer_k3.profiles import load_profile


st.set_page_config(page_title="nanoMINER · Kimi K3", layout="wide")
st.title("nanoMINER corrected reproduction")
st.caption("Kimi K3 core agent · evidence-first candidate extraction")
st.warning(
    "Results are staging candidates only. They are forced to needs_review and "
    "must not be written directly to PE main tables or Gold."
)

profile_name = st.selectbox(
    "Extraction profile",
    options=("pe_crystal", "nanozyme"),
    format_func=lambda value: {
        "pe_crystal": "PE crystal literature",
        "nanozyme": "Upstream nanozyme case study",
    }[value],
)
article_upload = st.file_uploader("Article PDF", type=("pdf",))
supplement_upload = st.file_uploader("Supplement PDF (optional)", type=("pdf",))
enable_vision = st.checkbox("Analyze figures, tables, and scan-only pages", value=True)

if st.button("Extract candidates", type="primary", disabled=article_upload is None):
    try:
        settings = KimiSettings.from_env()
        profile = load_profile(profile_name)
        client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.request_timeout_seconds,
            max_retries=settings.transport_retries,
        )
        with TemporaryDirectory(prefix="nanominer_k3_") as temp:
            temp_root = Path(temp)
            article_root = temp_root / "article"
            article_root.mkdir()
            article_path = article_root / Path(article_upload.name).name
            article_path.write_bytes(article_upload.getvalue())
            documents = [PdfDocument.load(article_path, role="article")]
            if supplement_upload is not None:
                supplement_root = temp_root / "supplement"
                supplement_root.mkdir()
                supplement_path = supplement_root / Path(supplement_upload.name).name
                supplement_path.write_bytes(supplement_upload.getvalue())
                documents.append(PdfDocument.load(supplement_path, role="supplement"))
            with st.spinner("Kimi K3 is collecting and structuring page-level evidence…"):
                run = run_extraction(
                    client=client,
                    settings=settings,
                    corpus=DocumentCorpus(documents),
                    profile=profile,
                    enable_vision=enable_vision,
                )
        rendered = json.dumps(run.as_dict(), ensure_ascii=False, indent=2)
        st.session_state["latest_extraction"] = rendered
    except ConfigurationError as exc:
        st.error(str(exc))
    except Exception as exc:
        st.exception(exc)

if rendered := st.session_state.get("latest_extraction"):
    st.subheader("Candidate extraction")
    st.json(rendered)
    st.download_button(
        "Download staging JSON",
        data=rendered + "\n",
        file_name="candidate_extraction.json",
        mime="application/json",
    )
