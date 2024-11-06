# Branch: ProsumerJack
# File: visualisation_manager.py

from multiprocessing import Process, Queue, Event
from energy_types import EnergyReading
from dataAnalysis import calculate_end_date, update_plot_same
import logging

# set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class VisualisationManager:
    def __init__(self, start_date, timescale):
        logging.info("Initializing visualization manager...")
        self.queue = Queue()
        self.ready_event = Event()
        self.plot_process = None
        self.start_date = start_date
        self.timescale = timescale

    def start(self, df):
        logging.info("Starting visualization process...")
        # start the plot process
        end_date = calculate_end_date(self.start_date, self.timescale)
        self.plot_process = Process(
            target=update_plot_same,
            args=(df, self.start_date, end_date, self.timescale, self.queue, self.ready_event)
        )
        self.plot_process.start()
        logging.info("Waiting for plot initialization...")
        self.ready_event.wait()  # Wait for the plot to be initialized
        logging.info("Plot initialization complete")

    def update(self, reading: EnergyReading):
        # update the plot with new data
        try:
            self.queue.put({
                'timestamp': reading.timestamp,
                'generation': reading.generation if hasattr(reading, 'generation') else 0,
                'demand': reading.demand
            })
            logging.debug(f"Updated plot with new data point at {reading.timestamp}")
        except Exception as e:
            logging.error(f"Error updating visualization: {e}")

    def stop(self):
        logging.info("Stopping visualization...")
        # stop the plot process
        try:
            self.queue.put("done")
            if self.plot_process:
                self.plot_process.join(timeout=5)  # Add timeout to prevent hanging
                logging.info("Visualization process stopped successfully")
        except Exception as e:
            logging.error(f"Error stopping visualization: {e}")