#!/bin/bash

# Default values
DATA_FILE="~/block_0.csv" # Replace with path to data file
START_DATE="2012-10-24"
TIMESCALE="d"
PROSUMER_HOUSEHOLD="MAC000246"  # Default prosumer household ID from your config

# Function to show usage
show_usage() {
    echo "Usage: $0 [options]"
    echo "Options:"
    echo "  --file <path>      : Path to data file (default: $DATA_FILE)"
    echo "  --date <YYYY-MM-DD>: Start date (default: $START_DATE)"
    echo "  --scale <d|w|m|y>  : Timescale (default: $TIMESCALE)"
    echo "  --household <id>   : Household ID (default: $PROSUMER_HOUSEHOLD)"
    echo "Example:"
    echo "  $0 --file ~/block_0.csv --date 2012-10-24 --scale d --household MAC000246"
}

# Parse optional arguments
while [ $# -gt 0 ]; do
    case $1 in
        --file)
            DATA_FILE="$2"
            shift 2
            ;;
        --date)
            START_DATE="$2"
            shift 2
            ;;
        --scale)
            TIMESCALE="$2"
            shift 2
            ;;
        --household)
            PROSUMER_HOUSEHOLD="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

echo "Starting Prosumer simulation..."
echo "File path: $DATA_FILE"
echo "Household ID: $PROSUMER_HOUSEHOLD"
echo "Start date: $START_DATE"
echo "Timescale: $TIMESCALE"

python run.py \
    --file_path "$DATA_FILE" \
    --household "$PROSUMER_HOUSEHOLD" \
    --start_date "$START_DATE" \
    --timescale "$TIMESCALE"