# Branch: ProsumerJack
# File: run.sh

#!/bin/bash

# Default values
DATA_FILE="/home/pi/block_0.csv"
START_DATE="2012-10-24"
TIMESCALE="d"
PROSUMER_HOUSEHOLD="MAC000246"  # Default prosumer household ID

# Function to show usage
show_usage() {
    echo "Usage: $0 [options]"
    echo "Options:"
    echo "  --file <path>      : Path to data file (default: $DATA_FILE)"
    echo "  --date <YYYY-MM-DD>: Start date (default: $START_DATE)"
    echo "  --scale <d|w|m|y>  : Timescale (default: $TIMESCALE)"
    echo "  --household <id>   : Household ID (default: $PROSUMER_HOUSEHOLD)"
    echo "Example:"
    echo "  $0 --file /home/pi/block_0.csv --date 2012-10-24 --scale d --household MAC000246"
}

# Function to cleanup background processes on script exit
cleanup() {
    echo "Cleaning up..."
    if [ -f "server.pid" ]; then
        SERVER_PID=$(cat server.pid)
        kill $SERVER_PID 2>/dev/null
        rm server.pid
    fi
}

# Register cleanup function to run on script exit
trap cleanup EXIT

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

echo "Starting server..."
# Start the server in the background and save its PID
python server.py &
SERVER_PID=$!
echo $SERVER_PID > server.pid

# Wait a moment for the server to start
sleep 2

# Check if server started successfully
if ! kill -0 $SERVER_PID 2>/dev/null; then
    echo "Failed to start server"
    exit 1
fi

echo "Server started successfully (PID: $SERVER_PID)"

echo "Starting Prosumer simulation..."
echo "File path: $DATA_FILE"
echo "Household ID: $PROSUMER_HOUSEHOLD"
echo "Start date: $START_DATE"
echo "Timescale: $TIMESCALE"

# Run the simulation
python run.py \
    --file_path "$DATA_FILE" \
    --household "$PROSUMER_HOUSEHOLD" \
    --start_date "$START_DATE" \
    --timescale "$TIMESCALE"