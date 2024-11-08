# Branch: consumerJack
# In visualisation_manager.py

import logging
from multiprocessing import Process, Queue, Event
from dataAnalysis import calculate_end_date, update_plot_same
from energy_types import EnergyReading

class VisualisationManager:
    def __init__(self, start_date, timescale):
        self.queue = Queue()
        self.ready_event = Event()
        self.plot_process = None
        self.start_date = start_date
        self.timescale = timescale
        self.df = None  # Store DataFrame to access simulated generation

    def start(self, df):
        """Start the visualization process"""
        try:
            self.df = df  # Store DataFrame
            end_date = calculate_end_date(self.start_date, self.timescale)
            self.plot_process = Process(
                target=update_plot_same,
                args=(df, self.start_date, end_date, self.timescale, self.queue, self.ready_event)
            )
            self.plot_process.start()
            self.ready_event.wait()  # Wait for plot initialization
            logging.info("Visualization process started successfully")
        except Exception as e:
            logging.error(f"Failed to start visualization: {e}")
            raise

    def update(self, reading: EnergyReading):
        """Update plot with new data"""
        try:
            # Get simulated generation for current timestamp
            generation = self.df.loc[reading.timestamp, 'generation'] if self.df is not None else 0
            
            self.queue.put({
                'timestamp': reading.timestamp,
                'generation': generation,  # Use simulated generation
                'demand': reading.demand,
                'net': generation - reading.demand  # Calculate net with simulated generation
            })
        except Exception as e:
            logging.error(f"Error updating visualization: {e}")
            # Fall back to zero generation if there's an error
            self.queue.put({
                'timestamp': reading.timestamp,
                'generation': 0,
                'demand': reading.demand,
                'net': -reading.demand
            })