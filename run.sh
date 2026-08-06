#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found. Install Python 3.11+ first."
    exit 1
fi

if [ ! -d venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

# First argument selects the tool:
#   app       -> interactive Streamlit UI (pick teams, run searches, view battles)
#   generate  -> build an optimal 6-mon team from the whole pool
#   (default) -> arrange a team you specify into the best lead/back per opponent
case "$1" in
  app)
    shift
    echo
    echo "Launching Streamlit app -- it should open in your browser."
    echo "If it doesn't, use the Local URL printed below. Ctrl+C to stop."
    echo
    python -m streamlit run src/app.py "$@"
    ;;
  generate)
    shift
    echo; echo "Running team GENERATION..."; echo
    python src/generate_team.py "$@"
    ;;
  *)
    echo; echo "Running lead/back SEARCH..."; echo
    python src/run_search.py "$@"
    ;;
esac
