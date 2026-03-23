# UniversalReviewer (v9.0 "Iron-Clad")

UniversalReviewer is an elite autonomous agentic workflow for creating high-impact academic literature reviews. It transforms a simple research prompt into a professional, Nature-style LaTeX/PDF document with 150+ citations.

## Key Features
- **Zero-Config Autonomy**: Just provide a topic, the skill handles workspace management, recursive discovery, and deep synthesis.
- **150+ Citation Target**: Uses 2-round citation snowballing and mass evidence harvesting to ensure extreme depth.
- **Iron-Clad Reliability**: Features automatic BibTeX reconciliation, LaTeX character cleaning, and math package integration (amsmath/amssymb).
- **Deep Modular Synthesis**: Employs a two-stage writing process (Draft + Expand) to ensure long-form, data-rich chapters.
- **AI Figure Prompts**: Automatically generates high-fidelity prompts for scientific figure generation.

## Project Structure
- `SKILL.md`: The core autonomous protocol.
- `agents/`: The multi-agent system (Researcher, Curator, Extractor, Expander, Writer, Editor, Visualizer).
- `scripts/`: The data engine (Search, Fetch, Parse, Clean, BibGen, Render).

## Quick Start
1.  Ensure you have `tectonic` and `playwright` installed on your system.
2.  Input: *"Write a review on [Your Topic]."*
3.  The system will automatically create a folder in `workspaces/` and start the Iron-Clad pipeline.

---
*Autonomous Academic Excellence.*
