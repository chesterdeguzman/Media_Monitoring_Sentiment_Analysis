from __future__ import annotations

import argparse
import re
import string
from pathlib import Path

import pandas as pd
try:
    from langdetect import LangDetectException, detect
except ImportError:  # Allows basic cleaning before optional language detection is installed.
    class LangDetectException(Exception):
        pass
    def detect(text: str) -> str:
        return "en"

SM_KEYWORDS = ["facebook", "twitter", "instagram", "bsky", "youtube", "blogs", "reddit", "tiktok"]
FORCE_MSM_SOURCES = [
    "germanexinthephilppines", "juancrisostomo", "wordpress",
    "kwebanibarok", "majeca", "republikanews.org", "reyfortmedia",
]
SOCIAL_ECHO_COLUMNS = ["Twitter Social Echo", "Facebook Social Echo", "Reddit Social Echo"]
DROP_COLUMNS = [
    "Subregion", "Desktop Reach", "Mobile Reach", "National Viewership", "Engagement", "AVE",
    "Twitter Authority", "Tweet Id", "Twitter Id", "Twitter Client", "Twitter Screen Name",
    "User Profile Url", "Twitter Bio", "Twitter Followers", "Twitter Following",
    "Alternate Date Format", "Time", "State", "City", "Editorial Echo", "Views",
    "Estimated Views", "Likes", "Replies", "Retweets", "Comments", "Shares", "Reactions",
    "Threads", "Is Verified", "Parent URL", "Document Tags", "Document ID", "Custom Categories",
]


def classify_row(row: pd.Series) -> str:
    source = str(row.get("Source", "")).lower()
    headline = str(row.get("Headline", "")).strip()
    if any(keyword in source for keyword in FORCE_MSM_SOURCES):
        return "MSM"
    if not headline or any(keyword in source for keyword in SM_KEYWORDS):
        return "SM"
    return "MSM"


def normalize_source(source: object, category: str) -> str:
    value = str(source).strip().lower()
    value = re.sub(r"www\.", "", value)
    value = re.sub(r"\.(com|ph|net|org)\b", "", value)
    value = re.sub(r"\s+", " ", value)
    if category == "SM" and "reddit" in value:
        return "Forums"
    acronyms = {"abs": "ABS", "cbn": "CBN", "msn": "MSN", "ptv": "PTV"}
    return " ".join(acronyms.get(word, word.title()) for word in value.split())


def headline_key(text: object) -> str:
    value = "" if pd.isna(text) else str(text).lower()
    value = re.sub(f"[{re.escape(string.punctuation)}]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def standardize_keyphrases(text: object) -> str:
    if pd.isna(text) or not str(text).strip():
        return "Unknown"
    values = []
    for part in re.split(r"[;,|/•]+", str(text)):
        part = part.strip()
        if not part:
            continue
        low = part.lower()
        if "pco" in low or "presidential communications" in low:
            item = "Presidential Communications Office"
        elif any(term in low for term in ["philippines", "pilipinas", "pilipino"]):
            item = "Philippines"
        else:
            item = part.title()
        if item not in values:
            values.append(item)
    return ", ".join(values) if values else "Unknown"


def infer_country(text: object) -> str:
    mapping = {"tl": "Philippines", "fil": "Philippines", "en": "United States", "ja": "Japan",
               "ko": "South Korea", "zh": "China", "fr": "France", "es": "Spain",
               "de": "Germany", "ms": "Malaysia", "id": "Indonesia"}
    if pd.isna(text) or not str(text).strip():
        return "Unknown"
    try:
        return mapping.get(detect(str(text)), "Unknown")
    except LangDetectException:
        return "Unknown"


def infer_language(text: object) -> str:
    if pd.isna(text) or not str(text).strip():
        return "English"
    try:
        return "Tagalog" if detect(str(text)) in {"tl", "fil"} else "English"
    except LangDetectException:
        return "English"


def clean_subset(df: pd.DataFrame, category: str) -> pd.DataFrame:
    df = df.copy()
    required = ["Date", "Source", "Country"] + (["Headline"] if category == "MSM" else [])
    df = df.drop_duplicates(subset=["Source Link"], keep="first").dropna(subset=required)

    if category == "SM":
        missing = df["Headline"].isna() | df["Headline"].astype(str).str.strip().eq("")
        df.loc[missing, "Headline"] = df.loc[missing, "Opening Text"]

    dates = df["Date"].where(df["Date"].notna(), df.get("Alternate Date Format"))
    df["Clean Date"] = pd.to_datetime(dates, errors="coerce")
    df = df.dropna(subset=["Clean Date"])
    df["Clean Date"] = df["Clean Date"].dt.strftime("%m-%d-%Y")
    df["Source"] = df["Source"].apply(lambda value: normalize_source(value, category))

    df["headline_key"] = df["Headline"].apply(headline_key)
    df = df.drop_duplicates(subset=["headline_key"], keep="first").drop(columns=["headline_key"])

    missing_columns = ["Opening Text", "Influencer", "Key Phrases"]
    if category == "SM":
        missing_columns += ["Language", "Sentiment"]
    for column in missing_columns:
        if column in df.columns:
            df[column] = df[column].fillna("Unknown").replace(r"^\s*$", "Unknown", regex=True)

    numeric_columns = SOCIAL_ECHO_COLUMNS + (["Reach"] if category == "SM" else [])
    for column in numeric_columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).astype(int)
    if all(column in df.columns for column in SOCIAL_ECHO_COLUMNS):
        df["Social Echo Total"] = df[SOCIAL_ECHO_COLUMNS].sum(axis=1).astype(int)
    elif "Social Echo Total" in df.columns:
        df["Social Echo Total"] = pd.to_numeric(df["Social Echo Total"], errors="coerce").fillna(0).astype(int)

    df = df[~df["Headline"].str.contains("content from this publisher", case=False, na=False)]
    df = df[~df["Source Link"].str.contains("proquest", case=False, na=False)]
    df = df[~df["Headline"].str.contains("test", case=False, na=False)]
    df = df[df["Source Link"].fillna("").str.strip().ne("")]
    if "Hit Sentence" in df.columns:
        df = df[~df["Hit Sentence"].str.contains(r"\[Courtesy:|\[Photo courtesy\]", case=False, na=False)]

    df = df.drop(columns=[column for column in DROP_COLUMNS if column in df.columns])
    if "Country" in df.columns:
        df["Country"] = df["Country"].astype(str).str.strip().str.title()
    df["Headline"] = df["Headline"].astype(str).str.strip().replace(r"\s+", " ", regex=True).str.title()
    for column in ["Opening Text", "Hit Sentence"]:
        if column in df.columns:
            df[column] = df[column].astype(str).str.strip().replace(r"\s+", " ", regex=True).str.capitalize()
    for column in ["Key Phrases", "Keywords"]:
        if column in df.columns:
            df[column] = df[column].apply(standardize_keyphrases)

    if "Hit Sentence" in df.columns:
        if "Country" in df.columns:
            blank = df["Country"].astype(str).str.strip().str.lower().isin(["", "unknown", "nan"])
            df.loc[blank, "Country"] = df.loc[blank, "Hit Sentence"].apply(infer_country)
        if "Language" in df.columns:
            blank = df["Language"].astype(str).str.strip().str.lower().isin(["", "unknown", "nan"])
            df.loc[blank, "Language"] = df.loc[blank, "Hit Sentence"].apply(infer_language)

    return df[["Clean Date"] + [column for column in df.columns if column != "Clean Date"]]


def run(input_file: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_excel(input_file)
    df["Headline"] = df["Headline"].fillna("")
    df["Source"] = df["Source"].fillna("")
    df["Category"] = df.apply(classify_row, axis=1)
    for category in ["MSM", "SM"]:
        subset = df[df["Category"] == category]
        subset.to_csv(output_dir / f"{category}.csv", index=False, encoding="utf-8-sig")
        cleaned = clean_subset(subset, category)
        cleaned.to_excel(output_dir / f"{category}_cleaned.xlsx", index=False, engine="openpyxl")
        print(f"{category}: {len(subset)} raw rows -> {len(cleaned)} cleaned rows")


def main() -> None:
    parser = argparse.ArgumentParser(description="Split and clean media-monitoring data into MSM and SM datasets.")
    parser.add_argument("input", type=Path, help="Path to the source Excel workbook")
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    args = parser.parse_args()
    run(args.input, args.output_dir)


if __name__ == "__main__":
    main()
