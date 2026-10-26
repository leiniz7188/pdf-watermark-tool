# PDF Watermark Script Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver a double-clickable Windows batch PDF watermark tool matching the approved centered, light watermark, without changing input PDFs.

**Architecture:** A Python CLI accepts PDF paths or one folder; no arguments opens a native file/folder picker. A JSON file supplies the two approved lines, opacity and angle. Pure full-page image PDFs retain their native image pixels; other pages are rendered at 300 PPI before watermarking. Every run writes a new timestamped output folder and a processing report.

**Tech Stack:** Python 3, PyMuPDF, Pillow, tkinter, Windows batch launcher.

---

### Task 1: Implement processing and settings

Create `pdf_watermark.py`, `watermark_config.json`, and `requirements.txt` in this tool folder. Validate settings and paths, preserve source pages and files, place one flattened watermark in the center, and isolate failures by PDF. Use temporary output files and unique output folders. Reject encrypted files and excessive raster sizes with clear messages.

### Task 2: Deliver a launcher and instructions

Create `双击运行.bat` and `使用说明.md`. The launcher checks Python and dependencies and installs missing dependencies from requirements when necessary. Explain selection, output location, optional drag-and-drop, JSON settings, and the quality/searchability limits of flattened image output.

### Task 3: Verify representative inputs

Create `tests/test_watermark.py` with integration coverage for source preservation, native image dimensions, single centered watermark, vector/rotated pages, independent corrupted/encrypted-input failures, and unique output directories. Run with `py -3 -m unittest discover -s tests -v`. Then process the actual 1A source PDF, verify all four output pages, and inspect the rendered output. Exercise the batch launcher without opening the chooser.

Execution is already authorized by the user's request; implement locally in this folder. No repository or worktree is present, and no delegation or git commit is needed.

## Completion and validation

- Processing script, Windows launcher, editable settings, requirements, and Chinese instructions delivered.
- Integration tests passed for native pixel preservation, centered placement, source preservation, existing-output rejection, vector and rotated pages, password and corruption failures, input deduplication, unique output folders, and settings validation.
- The Windows launcher successfully processed the actual 1A PDF with exit code 0. All four pages retained 1055 × 1491 pixels and the original page dimensions. Pixel differences were limited to one centered watermark per page; the four-page contact sheet was visually checked.
- No original PDFs were modified. The real launcher's example output and processing report are kept under `运行示例`.
