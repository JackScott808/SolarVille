# Branch: ProsumerJack
# File: run.py
import logging
from config import parse_arguments
from main import SimulationManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def main():
    try:
        # Parse command line arguments
        args = parse_arguments()
        
        # Create and start simulation
        # Note: is_prosumer is hardcoded to True since this is the Prosumer branch
        simulation = SimulationManager(args, is_prosumer=True)
        
        logging.info("Starting Prosumer simulation...")
        logging.info(f"Using data file: {args.file_path}")
        logging.info(f"Household ID: {args.household}")
        logging.info(f"Start date: {args.start_date}")
        
        # Run simulation
        simulation.start_simulation()
        
    except KeyboardInterrupt:
        logging.info("Consumer simulation stopped by user")
    except Exception as e:
        logging.error(f"Error in consumer simulation: {e}", exc_info=True)
    finally:
        logging.info("Consumer simulation ended")

if __name__ == "__main__":
    main()