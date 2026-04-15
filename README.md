# Places des Energies - Data Extraction

This repository contains tools to automatically extract data from energy bills (PDFs) using the Gemini API. It includes batch processing scripts and a Streamlit user interface for manual, single-file extractions.

## Environment Setup

The project requires **Python 3.12+** and uses [uv](https://github.com/astral-sh/uv) or `pip` for dependency management.

```bash
# 1. Install dependencies using uv (recommended)
uv sync

# Or using pip:
pip install -e .

# 2. Setup your environment variables
cp .env.example .env
# Open .env and add your GEMINI_API_KEY and other parameters
```

## Available Commands

Here are all the executable scripts and commands in the repository, and how to use them.

### 1. Batch Extraction (`batch_extract.py`)

The batch extraction script processes all PDF files located in `data/bills/`. 
*(Note: It expects `data/routing_results.json` and `data/supplier_rules_all.json` to be present).*

It groups outputs into `data/extraction_results_all.json` and `data/extraction_results_all.csv`.

```bash
# Process all PDFs in data/bills/
python batch_extract.py

# Attempt to resume, skipping any PDFs already present in the output JSON
python batch_extract.py --resume
```

### 2. Export / Format Results (`export_excel_format.py`)

This script converts the existing `data/extraction_results_all.json` into a specific flat CSV format designed for Excel import (`data/extraction_results_excel_format.csv`).

```bash
# Execute the formatter script
python export_excel_format.py
```

### 3. Streamlit Web UI (`app.py`)

Launches a web interface where you can drag & drop individual PDFs, visualize them, and run single extractions. It's especially useful for testing.

```bash
# Launch the local Streamlit server
streamlit run src/places_extractor/ui/app.py
```
After running this command, simply go to your browser and open `http://localhost:8501` to use the application.

### 4. Running Tests

The application is configured to use `pytest` for unit testing. 

```bash
# Run the test suite found in the tests/ directory
pytest tests/
```
