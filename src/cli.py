from pathlib import Path
import json

import typer
from rich.progress import track

from src.pipeline import process_pdf, deduplicate_latest, records_to_frames


app = typer.Typer(add_completion=False)


@app.command()
def extract(
    input: Path = typer.Option(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Dossier d'entrée avec PDFs",
    ),
    output_jsonl: Path = typer.Option(None, help="Chemin de sortie JSONL"),
    output_csv: Path = typer.Option(None, help="Chemin de sortie CSV"),
):
    pdfs = sorted([p for p in input.rglob("*.pdf")])
    records = []
    for p in track(pdfs, description="Extraction"):
        rec, meta = process_pdf(p)
        if rec is not None:
            records.append(rec)

    dedup = deduplicate_latest(records)

    if output_jsonl:
        output_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with output_jsonl.open("w", encoding="utf-8") as f:
            for r in dedup:
                f.write(json.dumps(r.model_dump(), ensure_ascii=False) + "\n")

    if output_csv:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        df = records_to_frames(dedup)
        df.to_csv(output_csv, index=False)

    typer.echo(f"Invoices kept after dedup: {len(dedup)}")


if __name__ == "__main__":
    app()
